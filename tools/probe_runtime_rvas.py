#!/usr/bin/env python3
"""Capture versioned RVA evidence from a running Windows process."""

from __future__ import annotations

import argparse
import ctypes
import json
import struct
import sys
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

from tools.inspect_process_rva import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    checked_handle,
    configure_kernel32,
    find_module,
    find_process_id,
)


MEMORY_PROTECTIONS = {
    0x01: "NOACCESS",
    0x02: "READONLY",
    0x04: "READWRITE",
    0x08: "WRITECOPY",
    0x10: "EXECUTE",
    0x20: "EXECUTE_READ",
    0x40: "EXECUTE_READWRITE",
    0x80: "EXECUTE_WRITECOPY",
}
PAGE_GUARD = 0x100
PAGE_NOCACHE = 0x200
PAGE_WRITECOMBINE = 0x400


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def format_protection(value: int) -> str:
    parts = [MEMORY_PROTECTIONS.get(value & 0xFF, f"0x{value & 0xFF:x}")]
    if value & PAGE_GUARD:
        parts.append("GUARD")
    if value & PAGE_NOCACHE:
        parts.append("NOCACHE")
    if value & PAGE_WRITECOMBINE:
        parts.append("WRITECOMBINE")
    return "|".join(parts)


def configure_probe_api(kernel32) -> None:
    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t


def query_region(kernel32, process, address: int) -> dict:
    info = MEMORY_BASIC_INFORMATION()
    if not kernel32.VirtualQueryEx(
        process, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "VirtualQueryEx")
    return {
        "base": info.BaseAddress,
        "size": info.RegionSize,
        "state": info.State,
        "protect": info.Protect,
        "protection": format_protection(info.Protect),
        "type": info.Type,
    }


def read_memory(kernel32, process, address: int, byte_count: int) -> bytes:
    buffer = ctypes.create_string_buffer(byte_count)
    bytes_read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        process,
        ctypes.c_void_p(address),
        buffer,
        byte_count,
        ctypes.byref(bytes_read),
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "ReadProcessMemory")
    return buffer.raw[: bytes_read.value]


def inspect_probe(kernel32, process, module_base, module_size, probe, byte_count):
    rva_value = probe["rva"]
    rva = int(rva_value, 0) if isinstance(rva_value, str) else rva_value
    if rva < 0 or rva + byte_count > module_size:
        raise ValueError(f"RVA 0x{rva:x} is outside module size 0x{module_size:x}")
    address = module_base + rva
    data = read_memory(kernel32, process, address, byte_count)
    first_pointer = struct.unpack_from("<Q", data)[0] if len(data) >= 8 else None
    result = {
        **probe,
        "rva": rva,
        "address": address,
        "region": query_region(kernel32, process, address),
        "bytes": data.hex(),
        "firstPointer": first_pointer,
    }
    if first_pointer is not None and module_base <= first_pointer < module_base + module_size:
        result["firstPointerRva"] = first_pointer - module_base
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("probe_set", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bytes", type=int, default=32, dest="byte_count")
    args = parser.parse_args()

    if sys.platform != "win32":
        raise SystemExit("this tool requires Windows")
    if args.byte_count < 8:
        raise SystemExit("--bytes must be at least 8")

    probe_set = json.loads(args.probe_set.read_text(encoding="utf-8"))
    if probe_set.get("format") != "CombatRuntimeProbeSet":
        raise SystemExit(f"unsupported probe set: {probe_set.get('format')!r}")

    kernel32 = configure_kernel32()
    configure_probe_api(kernel32)
    process_id = find_process_id(kernel32, probe_set["process"])
    module_base, module_size = find_module(kernel32, process_id, probe_set["module"])
    process = checked_handle(
        kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, process_id),
        "OpenProcess",
    )
    try:
        probes = [
            inspect_probe(kernel32, process, module_base, module_size, probe, args.byte_count)
            for probe in probe_set["probes"]
        ]
    finally:
        kernel32.CloseHandle(process)

    report = {
        "format": "CombatRuntimeProbeReport",
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceSet": probe_set.get("evidenceSet"),
        "process": probe_set["process"],
        "processId": process_id,
        "module": probe_set["module"],
        "moduleBase": module_base,
        "moduleSize": module_size,
        "byteCount": args.byte_count,
        "probes": probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(probes)} runtime probes to {args.output}")


if __name__ == "__main__":
    main()
