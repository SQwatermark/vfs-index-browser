"""批量解析 Manifest Bundle 名称对应的本地 VFS 来源。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable


class BundleSourceService:
    def __init__(
        self,
        db_path: Path,
        source_ranker: Callable[[str, bool], tuple],
    ) -> None:
        self._db_path = db_path
        self._source_ranker = source_ranker

    def resolve_many(
        self,
        bundles: list[dict],
    ) -> tuple[list[tuple[dict, Path]], list[dict]]:
        """一次查询全部 Bundle，并按调用方给出的顺序返回结果。"""

        if not bundles:
            return [], []
        file_names = [self._file_name(bundle) for bundle in bundles]
        unique_file_names = list(dict.fromkeys(file_names))
        placeholders = ",".join("?" for _ in unique_file_names)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM files WHERE file_name IN ({placeholders})",
                unique_file_names,
            ).fetchall()

        candidates_by_name: dict[str, list[dict]] = {
            file_name: [] for file_name in unique_file_names
        }
        for row in rows:
            candidate = dict(row)
            candidates_by_name[str(candidate["file_name"])].append(candidate)
        for candidates in candidates_by_name.values():
            candidates.sort(
                key=lambda row: self._source_ranker(
                    str(row["source"]), bool(row["chunk_exists"])
                )
            )

        resolved: list[tuple[dict, Path]] = []
        missing: list[dict] = []
        for bundle, file_name in zip(bundles, file_names, strict=True):
            source = next(
                (
                    (candidate, Path(candidate["chunk_path"]))
                    for candidate in candidates_by_name[file_name]
                    if Path(candidate["chunk_path"]).exists()
                ),
                None,
            )
            if source is None:
                missing.append(bundle)
            else:
                resolved.append(source)
        return resolved, missing

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _file_name(bundle: dict) -> str:
        return f"Data/Bundles/Windows/{bundle['name']}"
