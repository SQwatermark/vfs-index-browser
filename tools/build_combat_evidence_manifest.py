#!/usr/bin/env python3
"""Build a versioned manifest for combat reverse-engineering evidence files."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "EndfieldCombatEvidenceManifest"
VERSION = 1


def parse_artifact(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("artifact must use NAME=PATH")
    return name.strip(), Path(raw_path.strip())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def describe_artifact(name: str, path: Path, root: Path | None) -> dict:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"evidence artifact is not a file: {resolved}")
    if root is not None:
        try:
            display_path = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as error:
            raise ValueError(
                f"evidence artifact is outside the declared root: {resolved}"
            ) from error
    else:
        display_path = resolved.as_posix()
    stat = resolved.stat()
    return {
        "name": name,
        "path": display_path,
        "size": stat.st_size,
        "modifiedAt": datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat(),
        "sha256": sha256_file(resolved),
    }


def build_manifest(
    artifacts: list[tuple[str, Path]],
    *,
    client_version: str,
    root: Path | None = None,
) -> dict:
    names = [name for name, _path in artifacts]
    if len(names) != len(set(names)):
        raise ValueError("evidence artifact names must be unique")
    return {
        "format": FORMAT,
        "version": VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "clientVersion": client_version,
        "artifacts": [
            describe_artifact(name, path, root)
            for name, path in sorted(artifacts, key=lambda item: item[0])
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-version", required=True)
    parser.add_argument(
        "--root",
        type=Path,
        help="Optional common root; stored paths must be relative to it",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        type=parse_artifact,
        required=True,
        help="Evidence input in NAME=PATH form; may be repeated",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(
        args.artifact,
        client_version=args.client_version,
        root=args.root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(manifest['artifacts'])} artifacts to {args.output}")


if __name__ == "__main__":
    main()
