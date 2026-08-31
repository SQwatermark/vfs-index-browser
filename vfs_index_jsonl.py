"""Read VFS index JSONL from plain, gzip, or single-member tar archives."""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path
from typing import Iterator


def open_index(path: Path) -> Iterator[str]:
    name = path.name.lower()
    if name.endswith(".tgz") or name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            members = [member for member in archive.getmembers() if member.isfile()]
            if len(members) != 1:
                raise ValueError(
                    f"expected one JSONL file in archive, found {len(members)}"
                )
            raw = archive.extractfile(members[0])
            if raw is None:
                raise ValueError(f"cannot read {members[0].name} from {path}")
            with io.TextIOWrapper(raw, encoding="utf-8") as reader:
                yield from reader
        return

    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as reader:
            yield from reader
        return

    with path.open("r", encoding="utf-8") as reader:
        yield from reader
