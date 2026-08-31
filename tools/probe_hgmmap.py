#!/usr/bin/env python3
"""Decompress and inspect Endfield's Brotli-compressed ``.hgmmap`` manifest."""

from __future__ import annotations

import argparse
import math
import sqlite3
import struct
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vfs_crypto import decrypt_vfs_file


DEFAULT_DB = PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"


def read_payload(db_path: Path, file_id: int) -> tuple[bytes, sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise SystemExit(f"file id not found: {file_id}")
        with Path(row["chunk_path"]).open("rb") as source:
            source.seek(int(row["offset"]))
            data = source.read(int(row["length"]))
        if row["encrypted"]:
            data = decrypt_vfs_file(data, int(row["iv_seed"]))
        return data, row


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def ascii_runs(data: bytes, minimum: int = 5, limit: int = 40) -> list[tuple[int, str]]:
    runs: list[tuple[int, str]] = []
    start = 0
    while start < len(data) and len(runs) < limit:
        while start < len(data) and not (32 <= data[start] < 127):
            start += 1
        end = start
        while end < len(data) and 32 <= data[end] < 127:
            end += 1
        if end - start >= minimum:
            runs.append((start, data[start:end].decode("ascii")))
        start = max(end, start + 1)
    return runs


def hex_lines(data: bytes, width: int = 16) -> list[str]:
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_text = " ".join(f"{value:02x}" for value in chunk)
        text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append(f"{offset:04x}: {hex_text:<{width * 3 - 1}}  {text}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--id", type=int, help="file id in the VFS index")
    source.add_argument("--decompressed", type=Path, help="previously decompressed manifest")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dump", type=Path, help="write the decompressed manifest")
    args = parser.parse_args()

    try:
        import brotli
    except ImportError as error:
        raise SystemExit("brotli is required: python -m pip install brotli") from error

    if args.decompressed:
        decompressed = args.decompressed.read_bytes()
        compressed = None
        print(f"path: {args.decompressed}")
    else:
        compressed, row = read_payload(args.db, args.id)
        try:
            decompressed = brotli.decompress(compressed)
        except brotli.error as error:
            raise SystemExit(f"Brotli decompression failed: {error}") from error
        print(f"path: {row['source_logical_id']}")
        print(f"compressed: {len(compressed)} bytes, entropy {entropy(compressed):.4f}")

    print(f"decompressed: {len(decompressed)} bytes, entropy {entropy(decompressed):.4f}")
    if compressed is not None:
        print(f"ratio: {len(decompressed) / len(compressed):.3f}x")
    print("head:")
    print("\n".join(hex_lines(decompressed[:512])))
    print("first 32 little-endian uint32 values:")
    print("  " + " ".join(f"{value:08x}" for value in struct.unpack_from("<32I", decompressed)))

    cursor = 4
    version_length = struct.unpack_from("<I", decompressed, cursor)[0]
    cursor += 4
    version = decompressed[cursor : cursor + version_length * 2].decode("utf-16-le")
    cursor += version_length * 2
    second_magic = struct.unpack_from("<I", decompressed, cursor)[0]
    cursor += 4
    hash_length = struct.unpack_from("<I", decompressed, cursor)[0]
    cursor += 4
    manifest_hash = decompressed[cursor : cursor + hash_length * 2].decode("utf-16-le")
    cursor += hash_length * 2
    perforce_length = struct.unpack_from("<I", decompressed, cursor)[0]
    cursor += 4
    perforce_cl = decompressed[cursor : cursor + perforce_length * 2].decode("utf-16-le")
    cursor += perforce_length * 2
    print(
        "header: "
        f"magic=0x{struct.unpack_from('<I', decompressed)[0]:08x}, "
        f"version={version!r}, secondMagic=0x{second_magic:08x}, "
        f"hash={manifest_hash!r}, perforceCL={perforce_cl!r}, body=0x{cursor:x}"
    )

    candidate_offset, candidate_count = struct.unpack_from("<II", decompressed, cursor)
    print(f"first body reference candidate: offset=0x{candidate_offset:x}, count={candidate_count}")
    for base_kind, base in (("absolute", candidate_offset), ("body-relative", cursor + candidate_offset)):
        if base + 144 <= len(decompressed):
            print(f"{base_kind} candidate records @ 0x{base:x}:")
            print("  " + "\n  ".join(hex_lines(decompressed[base : base + 144])))
    print("ASCII runs:")
    for offset, text in ascii_runs(decompressed):
        print(f"  0x{offset:08x}: {text}")

    print("token matches:")
    for token in (".ab", "assets/", "data/", "characters", "prefab", "texture"):
        for encoding in ("utf-8", "utf-16-le"):
            needle = token.encode(encoding)
            offsets = []
            start = 0
            while len(offsets) < 5:
                offset = decompressed.find(needle, start)
                if offset < 0:
                    break
                offsets.append(offset)
                start = offset + 1
            print(f"  {token!r} {encoding}: " + ", ".join(f"0x{x:x}" for x in offsets))
            if offsets and encoding == "utf-16-le":
                context_start = max(offsets[0] - 64, 0) & ~1
                context = decompressed[context_start : offsets[0] + 192]
                print(f"    context @ 0x{context_start:x}: {context.decode('utf-16-le', errors='replace')!r}")
                print("    " + "\n    ".join(hex_lines(context[:160])))

    if args.dump:
        args.dump.parent.mkdir(parents=True, exist_ok=True)
        args.dump.write_bytes(decompressed)
        print(f"dumped: {args.dump}")


if __name__ == "__main__":
    main()
