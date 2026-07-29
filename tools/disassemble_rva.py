#!/usr/bin/env python3
"""Print x86-64 instructions from a PE image at a relative virtual address."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("rva", type=lambda value: int(value, 0))
    parser.add_argument("--bytes", type=int, default=512, dest="byte_count")
    parser.add_argument("--instructions", type=int, default=120)
    args = parser.parse_args()

    try:
        import pefile
        from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    except ImportError as error:
        raise SystemExit("pefile and capstone are required") from error

    pe = pefile.PE(str(args.image), fast_load=True)
    offset = pe.get_offset_from_rva(args.rva)
    with args.image.open("rb") as source:
        source.seek(offset)
        code = source.read(args.byte_count)

    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    for index, instruction in enumerate(decoder.disasm(code, args.rva)):
        if index >= args.instructions:
            break
        print(f"{instruction.address:08x}  {instruction.mnemonic:<8} {instruction.op_str}")


if __name__ == "__main__":
    main()
