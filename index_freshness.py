"""Conservative startup audit for stale VFS index chunk references."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def inspect_index_freshness(db_path: Path, *, example_limit: int = 5) -> dict:
    if not db_path.is_file():
        return {
            "status": "unavailable",
            "reason": "index_database_missing",
            "checkedChunkCount": 0,
            "missingChunkCount": 0,
            "checkedBlcCount": 0,
            "changedBlcCount": 0,
            "examples": [],
        }
    try:
        with closing(sqlite3.connect(db_path)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            rows = conn.execute(
                """
                SELECT source, block_hash, chunk_file, chunk_path
                FROM files
                WHERE chunk_exists = 1
                GROUP BY source, block_hash, chunk_file, chunk_path
                ORDER BY source, block_hash, chunk_file
                """
            ).fetchall()
    except sqlite3.Error as error:
        return {
            "status": "unavailable",
            "reason": "index_database_invalid",
            "message": str(error),
            "checkedChunkCount": 0,
            "missingChunkCount": 0,
            "checkedBlcCount": 0,
            "changedBlcCount": 0,
            "examples": [],
        }

    missing = [row for row in rows if not Path(row[3]).is_file()]
    report = {
        "status": "stale" if missing else "unverified",
        "reason": "expected_chunks_missing" if missing else "blc_content_identity_not_recorded",
        "checkedChunkCount": len(rows),
        "missingChunkCount": len(missing),
        "checkedBlcCount": 0,
        "changedBlcCount": 0,
        "examples": [
            {
                "source": str(row[0]),
                "blockHash": str(row[1]),
                "chunkFile": str(row[2]),
            }
            for row in missing[:example_limit]
        ],
    }
    if "builtAtEpoch" in meta:
        try:
            report["builtAtEpoch"] = int(meta["builtAtEpoch"])
        except ValueError:
            pass
    if missing:
        return report

    try:
        summary = json.loads(meta.get("summary", "null"))
        sources = summary["sources"]
        identities = [
            (source, identity)
            for source in sources
            for identity in source.get("blcIdentities", [])
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return report
    if not identities:
        return report

    changed = []
    for source, identity in identities:
        try:
            path = Path(source["sourceRoot"]) / str(identity["relativePath"])
            expected_length = int(identity["length"])
            expected_sha256 = str(identity["sha256"]).casefold()
            matches = (
                path.is_file()
                and path.stat().st_size == expected_length
                and hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
            )
        except (KeyError, OSError, TypeError, ValueError):
            matches = False
        if not matches:
            changed.append(
                {
                    "source": str(source.get("source") or "unknown"),
                    "blockHash": str(identity.get("blockHash") or "unknown"),
                }
            )
    report["checkedBlcCount"] = len(identities)
    report["changedBlcCount"] = len(changed)
    report["examples"] = changed[:example_limit]
    if changed:
        report["status"] = "stale"
        report["reason"] = "blc_content_identity_changed"
    else:
        report["status"] = "current"
        report["reason"] = "blc_content_identity_matches"
    return report
