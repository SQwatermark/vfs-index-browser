#!/usr/bin/env python3
"""Parse Bundle metadata from Endfield's BundleManifest ``.hgmmap``.

The VFS payload is ChaCha20-encrypted and the resulting HGM map is Brotli
compressed. Bundle strings and dependency arrays use offsets relative to the
data area immediately following the fixed-size Bundle record array.
"""

from __future__ import annotations

import argparse
import brotli
import json
import sqlite3
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vfs_crypto import decrypt_vfs_file


DEFAULT_DB = PROJECT_ROOT / "data" / "endfield-vfs-index.sqlite"
HEAD1 = 0xFF11FF11
HEAD2 = 0xF1F2F3F4
BUNDLE_RECORD_SIZE = 48
ASSET_RECORD_SIZE = 24


@dataclass(frozen=True)
class Header:
    version: str
    manifest_hash: str
    perforce_cl: str
    body_offset: int


def read_dotnet_string(data: bytes, offset: int) -> tuple[str, int]:
    length = struct.unpack_from("<I", data, offset)[0]
    start = offset + 4
    end = start + length * 2
    if end > len(data):
        raise ValueError(f"UTF-16 string exceeds payload at 0x{offset:x}")
    return data[start:end].decode("utf-16-le"), end


def parse_header(data: bytes) -> Header:
    first_magic = struct.unpack_from("<I", data)[0]
    if first_magic != HEAD1:
        raise ValueError(f"unexpected HEAD1: 0x{first_magic:08x}")
    version, offset = read_dotnet_string(data, 4)
    second_magic = struct.unpack_from("<I", data, offset)[0]
    if second_magic != HEAD2:
        raise ValueError(f"unexpected HEAD2: 0x{second_magic:08x}")
    manifest_hash, offset = read_dotnet_string(data, offset + 4)
    perforce_cl, offset = read_dotnet_string(data, offset)
    return Header(version, manifest_hash, perforce_cl, offset)


def read_vfs_payload(db_path: Path, file_id: int) -> bytes:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise SystemExit(f"file id not found: {file_id}")
        with Path(row["chunk_path"]).open("rb") as source:
            source.seek(int(row["offset"]))
            payload = source.read(int(row["length"]))
        if row["encrypted"]:
            payload = decrypt_vfs_file(payload, int(row["iv_seed"]))
        return payload


def load_manifest(args: argparse.Namespace) -> bytes:
    if args.decompressed:
        return args.decompressed.read_bytes()
    try:
        import brotli
    except ImportError as error:
        raise SystemExit("brotli is required: python -m pip install brotli") from error
    return brotli.decompress(read_vfs_payload(args.db, args.id))


def locate_records(data: bytes, body_offset: int) -> tuple[int, int, int, int]:
    # The body starts with the asset hash table. Its value offset lands on a
    # sentinel followed by the Bundle hash table header.
    first_value_offset, asset_count = struct.unpack_from("<II", data, body_offset)
    asset_records_offset = body_offset + 8 + asset_count * 8
    # The final AssetInfo padding word is also the zero sentinel immediately
    # before the following Bundle hash table.
    if asset_records_offset + asset_count * ASSET_RECORD_SIZE != body_offset + first_value_offset + 4:
        raise ValueError("AssetInfo table does not end at the Bundle table")
    bundle_table = body_offset + first_value_offset + 4
    bundle_value_offset, bundle_count = struct.unpack_from("<II", data, bundle_table)
    bundle_values = bundle_table + bundle_value_offset

    # Bundle hash-table values wrap the same contiguous records in a RefArray:
    # sentinel, data offset, count, then count fixed-size Bundle structures.
    array_count = struct.unpack_from("<I", data, bundle_values + 8)[0]
    if array_count != bundle_count:
        raise ValueError(
            f"Bundle table count mismatch: header={bundle_count}, array={array_count}"
        )
    records_offset = bundle_values + 12
    expected_end = records_offset + bundle_count * BUNDLE_RECORD_SIZE
    if expected_end > len(data):
        raise ValueError("Bundle records exceed payload")
    return asset_records_offset, asset_count, records_offset, bundle_count


def locate_bundle_records(data: bytes, body_offset: int) -> tuple[int, int]:
    """Compatibility helper used by the existing probing scripts."""
    _, _, records_offset, bundle_count = locate_records(data, body_offset)
    return records_offset, bundle_count


def read_ref_string(data: bytes, data_offset: int, ref: int) -> str:
    offset = data_offset + ref
    byte_count = struct.unpack_from("<i", data, offset)[0]
    end = offset + 4 + byte_count
    if byte_count < 0 or end > len(data):
        raise ValueError(f"invalid RefString length {byte_count} at 0x{offset:x}")
    return data[offset + 4 : end].decode("utf-16-le")


def read_ref_int_array(data: bytes, data_offset: int, ref: int) -> list[int]:
    offset = data_offset + ref
    count = struct.unpack_from("<i", data, offset)[0]
    end = offset + 4 + count * 4
    if count < 0 or end > len(data):
        raise ValueError(f"invalid RefArray count {count} at 0x{offset:x}")
    return list(struct.unpack_from(f"<{count}i", data, offset + 4)) if count else []


def read_ref_compressed_string(data: bytes, data_offset: int, ref: int) -> str:
    offset = data_offset + ref
    byte_count = struct.unpack_from("<I", data, offset)[0]
    end = offset + 4 + byte_count
    if end > len(data):
        raise ValueError(f"compressed string exceeds payload at 0x{offset:x}")
    return brotli.decompress(data[offset + 4 : end]).decode("utf-16-le")


def parse_bundles(data: bytes, records_offset: int, count: int) -> list[dict]:
    records = [
        struct.unpack_from("<6I4I2I", data, records_offset + index * BUNDLE_RECORD_SIZE)
        for index in range(count)
    ]
    # A four-byte sentinel separates the fixed Bundle array from the data area.
    data_offset = records_offset + count * BUNDLE_RECORD_SIZE + 4
    bundles = []
    for index, record in enumerate(records):
        (
            bundle_index,
            name_ref,
            dependencies_ref,
            reverse_dependencies_ref,
            direct_dependencies_ref,
            bundle_flags,
            hash_name_low,
            hash_name_high,
            hash_version_low,
            hash_version_high,
            category,
            padding,
        ) = record
        if bundle_index != index:
            raise ValueError(f"Bundle index mismatch at {index}: {bundle_index}")
        if padding != 0:
            raise ValueError(f"unexpected Bundle padding at {index}: {padding}")

        name = read_ref_string(data, data_offset, name_ref)
        if not (
            (name.startswith("main/") or name.startswith("initial/"))
            and name.endswith(".ab")
        ):
            raise ValueError(f"unexpected Bundle name at {index}: {name!r}")

        bundles.append(
            {
                "index": bundle_index,
                "name": name,
                "dependencies": read_ref_int_array(data, data_offset, dependencies_ref),
                "directReverseDependencies": read_ref_int_array(
                    data, data_offset, reverse_dependencies_ref
                ),
                "directDependencies": read_ref_int_array(
                    data, data_offset, direct_dependencies_ref
                ),
                "flags": bundle_flags,
                "hashName": f"{hash_name_high:08x}{hash_name_low:08x}",
                "hashVersion": f"{hash_version_high:08x}{hash_version_low:08x}",
                "category": category,
            }
        )
    return bundles


def parse_assets(
    data: bytes, records_offset: int, count: int, data_offset: int, bundle_count: int
) -> list[dict]:
    assets = []
    for index in range(count):
        path_hash, path_ref, bundle_index, asset_size, padding = struct.unpack_from(
            "<qIiiI", data, records_offset + index * ASSET_RECORD_SIZE
        )
        if padding != 0:
            raise ValueError(f"unexpected AssetInfo padding at {index}: {padding}")
        if not 0 <= bundle_index < bundle_count:
            raise ValueError(f"invalid AssetInfo bundle index at {index}: {bundle_index}")
        if asset_size < 0:
            raise ValueError(f"negative AssetInfo size at {index}: {asset_size}")
        assets.append(
            {
                "path": read_ref_compressed_string(data, data_offset, path_ref),
                "pathHashHead": f"{path_hash & 0xFFFFFFFFFFFFFFFF:016x}",
                "bundleIndex": bundle_index,
                "size": asset_size,
            }
        )
    return assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--id", type=int, help="file id in the VFS index")
    source.add_argument("--decompressed", type=Path, help="decompressed HGM map")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, help="write parsed JSON")
    parser.add_argument("--limit", type=int, help="only write the first N records")
    parser.add_argument("--include-assets", action="store_true", help="also export AssetInfo")
    parser.add_argument("--asset-limit", type=int, help="only write the first N AssetInfo rows")
    args = parser.parse_args()

    data = load_manifest(args)
    header = parse_header(data)
    asset_records_offset, asset_count, records_offset, count = locate_records(
        data, header.body_offset
    )
    data_offset = records_offset + count * BUNDLE_RECORD_SIZE + 4
    bundles = parse_bundles(data, records_offset, count)
    selected = bundles[: args.limit] if args.limit is not None else bundles
    result = {
        "version": header.version,
        "hash": header.manifest_hash,
        "perforceCL": header.perforce_cl,
        "bundleCount": count,
        "bundles": selected,
    }
    if args.include_assets:
        assets = parse_assets(data, asset_records_offset, asset_count, data_offset, count)
        result["assetCount"] = asset_count
        result["assets"] = assets[: args.asset_limit] if args.asset_limit is not None else assets
    print(
        f"parsed {count} Bundles; records=0x{records_offset:x}; "
        f"exporting {len(selected)}"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written: {args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
