"""Coordinate startup audits and repairs before the HTTP server listens."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ApplicationStartupReports:
    index_freshness: dict
    index_rebuild: dict
    secondary_audio_indexes: dict
    secondary_audio_rebuild: dict
    manifest_index: dict


class ApplicationStartupService:
    def __init__(
        self,
        inspect_index: Callable[[Path], dict],
        rebuild_index: Callable[[Path], dict],
        start_secondary_audio: Callable[[Path, bool], object],
        manifest_summary: Callable[[Path], dict],
        emit: Callable[[str, str, dict], None],
        *,
        rebuild_error_types: tuple[type[BaseException], ...] = (RuntimeError,),
    ) -> None:
        self._inspect_index = inspect_index
        self._rebuild_index = rebuild_index
        self._start_secondary_audio = start_secondary_audio
        self._manifest_summary = manifest_summary
        self._emit = emit
        self._rebuild_error_types = rebuild_error_types

    def run(
        self,
        database_path: Path,
        *,
        auto_rebuild: bool,
    ) -> ApplicationStartupReports:
        freshness = self._inspect_index(database_path)
        rebuild = {"status": "notNeeded"}
        if auto_rebuild and freshness.get("status") in {"stale", "unverified"}:
            self._emit(
                "warning",
                "index_rebuild_started",
                {"freshnessStatus": freshness.get("status")},
            )
            try:
                rebuild = self._rebuild_index(database_path)
                freshness = self._inspect_index(database_path)
            except self._rebuild_error_types as error:
                rebuild = {"status": "failed", "message": str(error)}
                self._emit(
                    "error",
                    "index_rebuild_failed",
                    {"error": str(error)},
                )

        secondary = self._start_secondary_audio(database_path, auto_rebuild)
        secondary_indexes = secondary.index_report
        secondary_rebuild = secondary.rebuild_report
        if secondary_indexes.get("status") != "current":
            self._emit(
                "warning",
                "secondary_audio_index_audit_failed",
                {"report": secondary_indexes},
            )

        try:
            manifest = {"status": "ready", **self._manifest_summary(database_path)}
        except (OSError, sqlite3.Error, ValueError) as error:
            manifest = {"status": "unavailable", "message": str(error)}
            self._emit(
                "error",
                "manifest_prewarm_failed",
                {"error": str(error)},
            )

        return ApplicationStartupReports(
            index_freshness=freshness,
            index_rebuild=rebuild,
            secondary_audio_indexes=secondary_indexes,
            secondary_audio_rebuild=secondary_rebuild,
            manifest_index=manifest,
        )
