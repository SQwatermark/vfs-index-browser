"""Prepare, validate, and atomically publish the AudioDialog index."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Callable

from audio_dialog_discovery import discover_audio_dialog_inputs
from audio_dialog_index import build_audio_dialog_index_from_packages
from audio_dialog_store import replace_audio_dialog_language
from secondary_audio_freshness import inspect_secondary_audio_indexes
from sparkbuffer import SparkBufferError, parse_sparkbuffer


class AudioDialogRebuildError(RuntimeError):
    pass


def rebuild_audio_dialog_index_atomically(
    vfs_database: Path,
    audio_dialog_database: Path,
    wwise_database: Path,
    package_service,
    decrypt_file: Callable[[bytes, int], bytes],
) -> dict:
    """Build every locally installed language without trusting old derived files."""

    audio_dialog_database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".audio-dialog-index-rebuild-",
        dir=audio_dialog_database.parent,
    ) as directory:
        candidate = Path(directory) / "audio-dialog-index.sqlite"
        language_reports = _build_candidate(
            vfs_database,
            candidate,
            package_service,
            decrypt_file,
        )
        validation = _validate_candidate(vfs_database, candidate, wwise_database)
        backup = audio_dialog_database.with_name(
            f"{audio_dialog_database.stem}.previous.sqlite"
        )
        backup_temporary = Path(directory) / "previous.sqlite"
        try:
            if audio_dialog_database.is_file():
                shutil.copy2(audio_dialog_database, backup_temporary)
                os.replace(backup_temporary, backup)
            os.replace(candidate, audio_dialog_database)
        except OSError as error:
            raise AudioDialogRebuildError(
                f"AudioDialog index publication failed: {error}"
            ) from error
    return {
        "status": "rebuilt",
        "languages": language_reports,
        "packageCount": validation["packageCount"],
        "resolvedPackageCount": validation["resolvedPackageCount"],
        "backup": str(backup) if backup.is_file() else None,
    }


def _build_candidate(
    vfs_database: Path,
    candidate: Path,
    package_service,
    decrypt_file: Callable[[bytes, int], bytes],
) -> dict:
    try:
        with closing(sqlite3.connect(vfs_database)) as conn:
            conn.row_factory = sqlite3.Row
            discovery = discover_audio_dialog_inputs(conn)
            table = discovery.preferred_tablecfg
            if table is None:
                raise AudioDialogRebuildError("AudioDialog TableCfg was not discovered")
            table_record = _load_record(conn, table.file_id)
            table_payload = parse_sparkbuffer(
                _read_file_slice(table_record, decrypt_file)
            )["data"]

            reports = {}
            input_records = []
            for language, discovered in discovery.packages.items():
                usable = [
                    item
                    for item in discovered
                    if item.location.effective
                    and Path(item.location.chunk_path).is_file()
                ]
                roles = {item.role for item in usable}
                if not {"banks", "stream"}.issubset(roles):
                    continue
                packages = []
                for item in usable:
                    record = _load_record(conn, item.location.file_id)
                    input_records.append(record)
                    meta = package_service.ensure_index(
                        record,
                        lambda offset, size, record=record: _read_file_range(
                            record,
                            offset,
                            size,
                            decrypt_file,
                        ),
                    )
                    packages.append((int(record["id"]), meta))
                matches = build_audio_dialog_index_from_packages(
                    table_payload,
                    language,
                    packages,
                )
                with closing(sqlite3.connect(candidate)) as output:
                    replace_audio_dialog_language(output, matches)
                counts = Counter(match.status for match in matches)
                reports[language] = {
                    "recordCount": len(matches),
                    "packageCount": len(packages),
                    "matchedCount": counts["matched"],
                    "missingCount": counts["missing"],
                    "ambiguousCount": counts["ambiguous"],
                    "collisionCount": counts["collision"],
                }
            if not reports:
                raise AudioDialogRebuildError(
                    "no installed AudioDialog language has both readable banks and stream packages"
                )
            _write_source_identities(candidate, table_record, input_records)
    except AudioDialogRebuildError:
        raise
    except (OSError, sqlite3.Error, UnicodeError, ValueError, SparkBufferError) as error:
        raise AudioDialogRebuildError(f"AudioDialog candidate build failed: {error}") from error
    return reports


def _validate_candidate(
    vfs_database: Path,
    candidate: Path,
    wwise_database: Path,
) -> dict:
    try:
        with closing(sqlite3.connect(candidate)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        raise AudioDialogRebuildError(
            f"AudioDialog candidate database is invalid: {error}"
        ) from error
    if integrity != "ok":
        raise AudioDialogRebuildError(
            f"AudioDialog candidate integrity check failed: {integrity}"
        )
    report = inspect_secondary_audio_indexes(
        vfs_database,
        candidate,
        wwise_database,
    )["audioDialog"]
    if report["status"] != "current" or report["packageCount"] <= 0:
        raise AudioDialogRebuildError(
            f"AudioDialog candidate failed freshness validation: {report}"
        )
    return report


def _load_record(conn: sqlite3.Connection, file_id: int) -> dict:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if row is None:
        raise AudioDialogRebuildError(f"VFS file id {file_id} disappeared")
    return dict(row)


def _write_source_identities(
    database: Path,
    record: dict,
    input_records: list[dict],
) -> None:
    content_md5 = str(record.get("file_data_md5") or "").casefold()
    if not content_md5:
        raise AudioDialogRebuildError(
            "AudioDialog TableCfg has no content identity in the VFS index"
        )
    values = {
        "tablecfg_logical_path": str(record["logical_id"]),
        "tablecfg_file_size": str(int(record["length"])),
        "tablecfg_file_data_md5": content_md5,
        "input_packages_json": json.dumps(
            [
                {
                    "logicalPath": str(item["logical_id"]),
                    "fileSize": int(item["length"]),
                    "fileDataMd5": str(item.get("file_data_md5") or "").casefold(),
                }
                for item in input_records
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    if any(not item["fileDataMd5"] for item in json.loads(values["input_packages_json"])):
        raise AudioDialogRebuildError(
            "an AudioDialog input PCK has no content identity in the VFS index"
        )
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.executemany(
            "INSERT OR REPLACE INTO audio_index_meta(key, value) VALUES (?, ?)",
            values.items(),
        )


def _read_file_slice(record: dict, decrypt_file: Callable[[bytes, int], bytes]) -> bytes:
    path = Path(record["chunk_path"])
    with path.open("rb") as source:
        source.seek(int(record["offset"]))
        data = source.read(int(record["length"]))
    if len(data) != int(record["length"]):
        raise OSError(f"short VFS read for file id {record['id']}")
    if record.get("encrypted"):
        data = decrypt_file(data, int(record["iv_seed"]))
    return data


def _read_file_range(
    record: dict,
    relative_offset: int,
    length: int,
    decrypt_file: Callable[[bytes, int], bytes],
) -> bytes:
    file_length = int(record["length"])
    if relative_offset < 0 or length < 0 or relative_offset + length > file_length:
        raise ValueError("file range is outside the VFS record")
    if record.get("encrypted"):
        return _read_file_slice(record, decrypt_file)[
            relative_offset : relative_offset + length
        ]
    path = Path(record["chunk_path"])
    with path.open("rb") as source:
        source.seek(int(record["offset"]) + relative_offset)
        data = source.read(length)
    if len(data) != length:
        raise OSError(f"short VFS range read for file id {record['id']}")
    return data
