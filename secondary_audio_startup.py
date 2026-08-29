"""Coordinate secondary audio freshness checks and startup rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from audio_dialog_rebuild import (
    AudioDialogRebuildError,
    rebuild_audio_dialog_index_atomically,
)
from secondary_audio_freshness import inspect_secondary_audio_indexes
from secondary_audio_rebuild import (
    SecondaryAudioRebuildError,
    rebuild_wwise_index_atomically,
)


EventSink = Callable[[str, str, dict], None]


@dataclass(frozen=True)
class SecondaryAudioStartupResult:
    index_report: dict
    rebuild_report: dict


def ensure_secondary_audio_indexes(
    vfs_database: Path,
    audio_dialog_database: Path,
    wwise_database: Path,
    project_root: Path,
    package_service,
    decrypt_file,
    *,
    auto_rebuild: bool = True,
    inspect: Callable[..., dict] = inspect_secondary_audio_indexes,
    rebuild_audio: Callable[..., dict] = rebuild_audio_dialog_index_atomically,
    rebuild_wwise: Callable[..., dict] = rebuild_wwise_index_atomically,
    emit: EventSink | None = None,
) -> SecondaryAudioStartupResult:
    emit = emit or (lambda _level, _event, _payload: None)
    report = inspect(vfs_database, audio_dialog_database, wwise_database)
    if not auto_rebuild:
        return SecondaryAudioStartupResult(report, {"status": "notNeeded"})

    rebuilt = {}
    failures = {}
    if report["audioDialog"]["status"] in {"stale", "unavailable"}:
        emit("warning", "audio_dialog_index_rebuild_started", {
            "report": report["audioDialog"],
        })
        try:
            rebuilt["audioDialog"] = rebuild_audio(
                vfs_database,
                audio_dialog_database,
                wwise_database,
                package_service,
                decrypt_file,
            )
            report = inspect(vfs_database, audio_dialog_database, wwise_database)
        except AudioDialogRebuildError as error:
            failures["audioDialog"] = {
                "status": "failed",
                "message": str(error),
            }
            emit("error", "audio_dialog_index_rebuild_failed", {
                "error": str(error),
            })

    if report["wwise"]["status"] in {"stale", "unavailable"}:
        emit("warning", "wwise_index_rebuild_started", {
            "report": report["wwise"],
        })
        try:
            rebuilt["wwise"] = rebuild_wwise(
                vfs_database,
                audio_dialog_database,
                wwise_database,
                project_root,
            )
            report = inspect(vfs_database, audio_dialog_database, wwise_database)
        except SecondaryAudioRebuildError as error:
            failures["wwise"] = {
                "status": "failed",
                "message": str(error),
            }
            emit("error", "wwise_index_rebuild_failed", {"error": str(error)})

    if failures:
        rebuild_report = {"status": "failed", **rebuilt, **failures}
    elif rebuilt:
        rebuild_report = {"status": "rebuilt", **rebuilt}
    else:
        rebuild_report = {"status": "notNeeded"}
    return SecondaryAudioStartupResult(report, rebuild_report)
