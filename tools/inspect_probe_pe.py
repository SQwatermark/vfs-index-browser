#!/usr/bin/env python3
"""Inspect a runtime probe set at the corresponding RVAs in an on-disk PE image."""

from __future__ import annotations

import argparse
import json
import struct
from datetime import datetime, timezone
from pathlib import Path


IMAGE_SCN_MEM_EXECUTE = 0x20000000


def parse_rva(value: int | str) -> int:
    return int(value, 0) if isinstance(value, str) else value


def is_executable(characteristics: int) -> bool:
    return bool(characteristics & IMAGE_SCN_MEM_EXECUTE)


def section_name(section) -> str:
    return section.Name.rstrip(b"\0").decode("ascii", errors="replace")


def scan_image_pointers(data: bytes, image_base: int, image_size: int):
    for offset in range(0, len(data) - 7, 8):
        value = struct.unpack_from("<Q", data, offset)[0]
        if image_base <= value < image_base + image_size:
            yield {"offset": offset, "value": value, "rva": value - image_base}


def inspect_probe(pe, image: bytes, probe: dict, byte_count: int) -> dict:
    rva = parse_rva(probe["rva"])
    section = pe.get_section_by_rva(rva)
    if section is None:
        return {**probe, "rva": rva, "error": "RVA is not covered by a PE section"}

    try:
        offset = pe.get_offset_from_rva(rva)
    except Exception as error:  # pefile uses several exception classes across versions.
        return {**probe, "rva": rva, "error": str(error)}

    data = image[offset : offset + byte_count]
    first_qword = struct.unpack_from("<Q", data)[0] if len(data) >= 8 else None
    image_pointers = []
    for pointer in scan_image_pointers(
        data, pe.OPTIONAL_HEADER.ImageBase, pe.OPTIONAL_HEADER.SizeOfImage
    ):
        target_section = pe.get_section_by_rva(pointer["rva"])
        image_pointers.append(
            {
                **pointer,
                "section": section_name(target_section) if target_section else None,
                "executable": (
                    is_executable(target_section.Characteristics)
                    if target_section
                    else None
                ),
            }
        )
    return {
        **probe,
        "rva": rva,
        "fileOffset": offset,
        "section": {
            "name": section_name(section),
            "virtualAddress": section.VirtualAddress,
            "virtualSize": section.Misc_VirtualSize,
            "rawOffset": section.PointerToRawData,
            "rawSize": section.SizeOfRawData,
            "characteristics": section.Characteristics,
            "executable": is_executable(section.Characteristics),
        },
        "bytes": data.hex(),
        "firstQword": first_qword,
        "imagePointers": image_pointers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("probe_set", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bytes", type=int, default=32, dest="byte_count")
    args = parser.parse_args()

    if args.byte_count < 8:
        raise SystemExit("--bytes must be at least 8")
    try:
        import pefile
    except ImportError as error:
        raise SystemExit(
            "pefile is required; install requirements-research.txt"
        ) from error

    probe_set = json.loads(args.probe_set.read_text(encoding="utf-8"))
    if probe_set.get("format") != "CombatRuntimeProbeSet":
        raise SystemExit(f"unsupported probe set: {probe_set.get('format')!r}")

    image = args.image.read_bytes()
    pe = pefile.PE(data=image, fast_load=True)
    probes = [
        inspect_probe(pe, image, probe, args.byte_count)
        for probe in probe_set["probes"]
    ]
    report = {
        "format": "CombatStaticPeProbeReport",
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceSet": probe_set.get("evidenceSet"),
        "image": args.image.name,
        "imageBase": pe.OPTIONAL_HEADER.ImageBase,
        "sizeOfImage": pe.OPTIONAL_HEADER.SizeOfImage,
        "byteCount": args.byte_count,
        "probes": probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(probes)} static PE probes to {args.output}")


if __name__ == "__main__":
    main()
