"""Build, validate, and atomically publish a fresh VFS SQLite index."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Callable

from index_freshness import inspect_index_freshness
from tools.index_endfield_vfs import build_index


class IndexRebuildError(RuntimeError):
    pass


def load_index_source_roots(db_path: Path) -> dict[str, Path]:
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'header'").fetchone()
        header = json.loads(row[0]) if row else None
        sources = header["sources"]
        roots = {
            str(item["name"]): Path(str(item["root"]))
            for item in sources
            if Path(str(item["root"])).is_dir()
        }
    except (OSError, sqlite3.Error, json.JSONDecodeError, KeyError, TypeError) as error:
        raise IndexRebuildError(f"cannot recover VFS source roots: {error}") from error
    if not roots:
        raise IndexRebuildError("the index does not contain any available VFS source roots")
    return roots


def validate_rebuilt_database(db_path: Path) -> dict:
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            meta = dict(conn.execute("SELECT key, value FROM meta"))
    except sqlite3.Error as error:
        raise IndexRebuildError(f"rebuilt database is invalid: {error}") from error
    if integrity != "ok":
        raise IndexRebuildError(f"rebuilt database integrity check failed: {integrity}")
    try:
        source_count = int(json.loads(meta["sourceFileCount"]))
        effective_count = int(json.loads(meta["effectiveFileCount"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IndexRebuildError(f"rebuilt database metadata is invalid: {error}") from error
    if source_count <= 0 or effective_count <= 0:
        raise IndexRebuildError("rebuilt database contains no usable file records")
    freshness = inspect_index_freshness(db_path)
    if freshness.get("status") != "current":
        raise IndexRebuildError(
            "rebuilt database did not pass source identity validation: "
            f"{freshness.get('status')} ({freshness.get('reason')})"
        )
    return freshness


def rebuild_index_atomically(
    db_path: Path,
    source_roots: dict[str, Path],
    database_builder: Callable[[Path, Path], None],
    *,
    generator: Callable[[argparse.Namespace], dict] = build_index,
) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".vfs-index-rebuild-",
        dir=db_path.parent,
    ) as directory:
        run_root = Path(directory)
        jsonl_path = run_root / "index.jsonl"
        summary_path = run_root / "summary.json"
        candidate_db = run_root / "index.sqlite"
        args = argparse.Namespace(
            game_data_root=None,
            streaming_assets=source_roots.get("StreamingAssets"),
            persistent_assets=source_roots.get("Persistent"),
            output=jsonl_path,
            summary=summary_path,
            block_type=[],
            file_regex=[],
            no_crc=False,
            progress_every=4,
        )
        try:
            generated = generator(args)
            totals = generated["totals"]
            if int(totals.get("parseErrorCount", 0)) != 0:
                raise IndexRebuildError(
                    f"VFS metadata parsing failed for {totals['parseErrorCount']} blocks"
                )
            if int(totals.get("selectedFileCount", 0)) <= 0:
                raise IndexRebuildError("VFS generator produced no file records")
            database_builder(jsonl_path, candidate_db)
            freshness = validate_rebuilt_database(candidate_db)
            os.replace(candidate_db, db_path)
        except IndexRebuildError:
            raise
        except Exception as error:
            raise IndexRebuildError(f"VFS index rebuild failed: {error}") from error
    return {
        "status": "rebuilt",
        "sourceFileCount": int(totals["selectedFileCount"]),
        "missingChunkCount": int(totals.get("missingChunkCount", 0)),
        "checkedBlcCount": int(freshness.get("checkedBlcCount", 0)),
    }
