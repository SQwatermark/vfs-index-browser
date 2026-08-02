#!/usr/bin/env python3
"""Read and optionally disassemble bytes at an RVA in a running Windows process."""

from __future__ import annotations

import argparse
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path


TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002
ERROR_NOT_ALL_ASSIGNED = 1300
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", LUID_AND_ATTRIBUTES * 1),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("process", help="Process executable name, for example Endfield.exe")
    parser.add_argument("module", help="Module name, for example GameAssembly.dll")
    parser.add_argument("rva", type=lambda value: int(value, 0))
    parser.add_argument("--bytes", type=int, default=1024, dest="byte_count")
    parser.add_argument("--instructions", type=int, default=120)
    parser.add_argument("--output", type=Path, help="Optional raw byte output")
    return parser.parse_args()


def configure_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Module32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    kernel32.Module32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def enable_debug_privilege(kernel32) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(LUID),
    ]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
        ctypes.byref(token),
    ):
        raise ctypes.WinError(ctypes.get_last_error(), "OpenProcessToken")
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(
            None, "SeDebugPrivilege", ctypes.byref(luid)
        ):
            raise ctypes.WinError(ctypes.get_last_error(), "LookupPrivilegeValueW")
        privileges = TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        if not advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privileges), 0, None, None
        ):
            raise ctypes.WinError(ctypes.get_last_error(), "AdjustTokenPrivileges")
        if ctypes.get_last_error() == ERROR_NOT_ALL_ASSIGNED:
            raise PermissionError("SeDebugPrivilege is not present in this process token")
    finally:
        kernel32.CloseHandle(token)


def checked_handle(handle: int, operation: str) -> int:
    if handle in {0, INVALID_HANDLE_VALUE}:
        raise ctypes.WinError(ctypes.get_last_error(), operation)
    return handle


def find_process_id(kernel32, executable_name: str) -> int:
    snapshot = checked_handle(
        kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0),
        "CreateToolhelp32Snapshot(processes)",
    )
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error(), "Process32FirstW")
        target = executable_name.casefold()
        while True:
            if entry.szExeFile.casefold() == target:
                return entry.th32ProcessID
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"process not found: {executable_name}")


def find_module(kernel32, process_id: int, module_name: str) -> tuple[int, int]:
    snapshot = checked_handle(
        kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, process_id
        ),
        "CreateToolhelp32Snapshot(modules)",
    )
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error(), "Module32FirstW")
        target = module_name.casefold()
        while True:
            if entry.szModule.casefold() == target:
                module_base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                if module_base is None:
                    raise RuntimeError(f"module has no base address: {module_name}")
                return module_base, entry.modBaseSize
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"module not found: {module_name}")


def read_process_memory(
    kernel32, process_id: int, address: int, byte_count: int
) -> bytes:
    process = checked_handle(
        kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, process_id
        ),
        "OpenProcess",
    )
    try:
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
    finally:
        kernel32.CloseHandle(process)


def print_disassembly(code: bytes, address: int, instruction_limit: int) -> None:
    if instruction_limit <= 0:
        return
    try:
        from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    except ImportError as error:
        raise SystemExit("capstone is required for disassembly") from error
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    for index, instruction in enumerate(decoder.disasm(code, address)):
        if index >= instruction_limit:
            break
        print(f"{instruction.address:016x}  {instruction.mnemonic:<8} {instruction.op_str}")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("this tool requires Windows")
    args = parse_args()
    kernel32 = configure_kernel32()
    enable_debug_privilege(kernel32)
    process_id = find_process_id(kernel32, args.process)
    module_base, module_size = find_module(kernel32, process_id, args.module)
    if args.rva < 0 or args.rva + args.byte_count > module_size:
        raise SystemExit(
            f"requested range 0x{args.rva:x}+0x{args.byte_count:x} "
            f"is outside module size 0x{module_size:x}"
        )
    address = module_base + args.rva
    code = read_process_memory(kernel32, process_id, address, args.byte_count)
    print(
        f"pid={process_id} moduleBase=0x{module_base:x} "
        f"rva=0x{args.rva:x} address=0x{address:x} bytes={len(code)}"
    )
    if args.output:
        args.output.write_bytes(code)
        print(f"saved={args.output}")
    print_disassembly(code, address, args.instructions)


if __name__ == "__main__":
    main()
