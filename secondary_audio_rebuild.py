"""Build, validate, and atomically publish secondary audio indexes."""

from __future__ import annotations

import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import Callable

from secondary_audio_freshness import inspect_secondary_audio_indexes


class SecondaryAudioRebuildError(RuntimeError):
    pass


def rebuild_wwise_index_atomically(
    vfs_database: Path,
    audio_dialog_database: Path,
    wwise_database: Path,
    project_root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_seconds: int = 15 * 60,
) -> dict:
    wwise_database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".wwise-index-rebuild-",
        dir=wwise_database.parent,
    ) as directory:
        candidate = Path(directory) / "wwise-index.sqlite"
        command = [
            sys.executable,
            str(project_root / "tools" / "build_wwise_index.py"),
            "--vfs-index",
            str(vfs_database),
            "--output",
            str(candidate),
            "--reset",
        ]
        options = {
            "cwd": str(project_root),
            "capture_output": True,
            "text": True,
            "timeout": timeout_seconds,
            "check": False,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            completed = runner(command, **options)
        except (OSError, subprocess.SubprocessError) as error:
            raise SecondaryAudioRebuildError(
                f"Wwise index builder failed to start: {error}"
            ) from error
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown error").strip()
            raise SecondaryAudioRebuildError(f"Wwise index builder failed: {message}")
        validation = _validate_wwise_candidate(
            vfs_database,
            audio_dialog_database,
            candidate,
        )
        backup = wwise_database.with_name(f"{wwise_database.stem}.previous.sqlite")
        backup_temporary = Path(directory) / "previous.sqlite"
        try:
            if wwise_database.is_file():
                shutil.copy2(wwise_database, backup_temporary)
                os.replace(backup_temporary, backup)
            os.replace(candidate, wwise_database)
        except OSError as error:
            raise SecondaryAudioRebuildError(
                f"Wwise index publication failed: {error}"
            ) from error
    return {
        "status": "rebuilt",
        "packageCount": validation["packageCount"],
        "resolvedPackageCount": validation["resolvedPackageCount"],
        "backup": str(backup) if backup.is_file() else None,
    }


def _validate_wwise_candidate(
    vfs_database: Path,
    audio_dialog_database: Path,
    candidate: Path,
) -> dict:
    if not candidate.is_file():
        raise SecondaryAudioRebuildError("Wwise index builder produced no database")
    try:
        with closing(sqlite3.connect(candidate)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        raise SecondaryAudioRebuildError(
            f"Wwise candidate database is invalid: {error}"
        ) from error
    if integrity != "ok":
        raise SecondaryAudioRebuildError(
            f"Wwise candidate integrity check failed: {integrity}"
        )
    report = inspect_secondary_audio_indexes(
        vfs_database,
        audio_dialog_database,
        candidate,
    )["wwise"]
    if report["status"] != "current" or report["packageCount"] <= 0:
        raise SecondaryAudioRebuildError(
            f"Wwise candidate failed freshness validation: {report}"
        )
    return report
