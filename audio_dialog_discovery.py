"""Discover local AudioDialog inputs from the VFS SQLite metadata index.

This module deliberately performs metadata queries only. It does not open,
parse, or scan any PCK payload.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass


AUDIO_DIALOG_LOGICAL_ID = "Table/Data/TableCfg/AudioDialog.bytes"
AUDIO_PCK_PREFIX = "Data/Audio/PCK/Windows/"
# '/' sorts immediately before '0', so this is the exclusive bound for descendants.
AUDIO_PCK_PREFIX_UPPER_BOUND = "Data/Audio/PCK/Windows0"
SUPPORTED_AUDIO_LANGUAGES = ("chinese", "english", "japanese", "korean")

_REQUIRED_FILES_COLUMNS = frozenset({
    "id",
    "source",
    "block_name",
    "logical_id",
    "source_logical_id",
    "file_name",
    "chunk_path",
    "chunk_exists",
    "offset",
    "length",
    "encrypted",
})
_REQUIRED_ENTRIES_COLUMNS = frozenset({
    "scope",
    "type",
    "path",
    "file_id",
})

_DEFAULT_PCK_PATTERN = re.compile(
    r"^Data/Audio/PCK/Windows/"
    r"(?P<directory>Chinese|English|Japanese|Korean)/"
    r"default_(?P<language>chinese|english|japanese|korean)_"
    r"(?P<kind>banks|stream(?:_[0-9]+)?)\.pck$"
)
_HOTFIX_PCK_PATTERN = re.compile(
    r"^Data/Audio/PCK/Windows/Hotfix/"
    r"hotfix_(?P<language>chinese|english|japanese|korean)\.pck$"
)


class AudioDialogDiscoveryError(RuntimeError):
    """The VFS index cannot support a reliable AudioDialog discovery query."""


@dataclass(frozen=True)
class VfsFileLocation:
    file_id: int
    source: str
    block_name: str
    logical_id: str
    source_logical_id: str
    file_name: str
    chunk_path: str
    chunk_exists: bool
    offset: int
    length: int
    encrypted: bool
    effective: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AudioPackageCandidate:
    language: str
    role: str
    location: VfsFileLocation

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AudioDialogDiscovery:
    tablecfg_candidates: tuple[VfsFileLocation, ...]
    packages: dict[str, tuple[AudioPackageCandidate, ...]]
    unclassified_audio_pcks: tuple[VfsFileLocation, ...]

    @property
    def preferred_tablecfg(self) -> VfsFileLocation | None:
        return self.tablecfg_candidates[0] if self.tablecfg_candidates else None

    def to_dict(self) -> dict:
        return {
            "tablecfg": {
                "logicalId": AUDIO_DIALOG_LOGICAL_ID,
                "preferred": (
                    self.preferred_tablecfg.to_dict()
                    if self.preferred_tablecfg is not None
                    else None
                ),
                "candidates": [item.to_dict() for item in self.tablecfg_candidates],
            },
            "packages": {
                language: [item.to_dict() for item in candidates]
                for language, candidates in self.packages.items()
            },
            "unclassifiedAudioPcks": [
                item.to_dict() for item in self.unclassified_audio_pcks
            ],
        }


def discover_audio_dialog_inputs(conn: sqlite3.Connection) -> AudioDialogDiscovery:
    """Locate AudioDialog TableCfg and language PCK candidates without reading files."""

    validate_vfs_schema(conn)

    tablecfg_rows = conn.execute(
        """
        SELECT id, source, block_name, logical_id, source_logical_id,
               file_name, chunk_path, chunk_exists, offset, length, encrypted
        FROM files
        WHERE logical_id = ?
        """,
        (AUDIO_DIALOG_LOGICAL_ID,),
    ).fetchall()
    tablecfg_effective_ids = _effective_file_ids_for_rows(conn, tablecfg_rows)
    tablecfg_candidates = tuple(sorted(
        (_location_from_row(row, tablecfg_effective_ids) for row in tablecfg_rows),
        key=_location_rank,
    ))

    pck_rows = conn.execute(
        """
        SELECT id, source, block_name, logical_id, source_logical_id,
               file_name, chunk_path, chunk_exists, offset, length, encrypted
        FROM files
        WHERE file_name >= ? AND file_name < ?
        ORDER BY file_name, id
        """,
        (AUDIO_PCK_PREFIX, AUDIO_PCK_PREFIX_UPPER_BOUND),
    ).fetchall()
    pck_effective_ids = _effective_file_ids_for_rows(conn, pck_rows)

    packages: dict[str, list[AudioPackageCandidate]] = {
        language: [] for language in SUPPORTED_AUDIO_LANGUAGES
    }
    unclassified = []
    for row in pck_rows:
        location = _location_from_row(row, pck_effective_ids)
        classification = classify_language_pck(location.file_name)
        if classification is None:
            unclassified.append(location)
            continue
        language, role = classification
        packages[language].append(AudioPackageCandidate(language, role, location))

    return AudioDialogDiscovery(
        tablecfg_candidates=tablecfg_candidates,
        packages={
            language: tuple(sorted(candidates, key=_package_rank))
            for language, candidates in packages.items()
        },
        unclassified_audio_pcks=tuple(sorted(unclassified, key=_location_rank)),
    )


def validate_vfs_schema(conn: sqlite3.Connection) -> None:
    """Fail clearly when the supplied SQLite database is not a supported VFS index."""

    for table, required in (
        ("files", _REQUIRED_FILES_COLUMNS),
        ("entries", _REQUIRED_ENTRIES_COLUMNS),
    ):
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        missing = sorted(required - columns)
        if missing:
            raise AudioDialogDiscoveryError(
                f"VFS SQLite table {table!r} is missing columns: {', '.join(missing)}"
            )


def classify_language_pck(file_name: str) -> tuple[str, str] | None:
    """Classify only PCK path shapes observed in the local VFS index."""

    default_match = _DEFAULT_PCK_PATTERN.fullmatch(file_name)
    if default_match is not None:
        language = default_match.group("language")
        if default_match.group("directory").lower() != language:
            return None
        kind = default_match.group("kind")
        role = "banks" if kind == "banks" else "stream"
        return language, role

    hotfix_match = _HOTFIX_PCK_PATTERN.fullmatch(file_name)
    if hotfix_match is not None:
        return hotfix_match.group("language"), "hotfix"
    return None


def _effective_file_ids_for_rows(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row] | list[tuple],
) -> set[int]:
    """Resolve a small candidate set through the indexed effective path lookup."""

    effective_ids = set()
    for logical_id in {str(tuple(row)[3]) for row in rows}:
        effective_ids.update(
            int(entry[0])
            for entry in conn.execute(
                """
                SELECT file_id
                FROM entries
                WHERE scope = 'effective' AND path = ? AND type = 'file'
                  AND file_id IS NOT NULL
                """,
                (logical_id,),
            )
        )
    return effective_ids


def _location_from_row(
    row: sqlite3.Row | tuple,
    effective_file_ids: set[int],
) -> VfsFileLocation:
    values = tuple(row)
    file_id = int(values[0])
    return VfsFileLocation(
        file_id=file_id,
        source=str(values[1]),
        block_name=str(values[2]),
        logical_id=str(values[3]),
        source_logical_id=str(values[4]),
        file_name=str(values[5]),
        chunk_path=str(values[6]),
        chunk_exists=bool(values[7]),
        offset=int(values[8]),
        length=int(values[9]),
        encrypted=bool(values[10]),
        effective=file_id in effective_file_ids,
    )


def _location_rank(location: VfsFileLocation) -> tuple[int, int, str, int]:
    return (
        0 if location.effective else 1,
        0 if location.chunk_exists else 1,
        location.source.lower(),
        location.file_id,
    )


def _package_rank(candidate: AudioPackageCandidate) -> tuple[int, int, int, str, int]:
    role_order = {"banks": 0, "stream": 1, "hotfix": 2}
    return (
        role_order[candidate.role],
        0 if candidate.location.effective else 1,
        0 if candidate.location.chunk_exists else 1,
        candidate.location.file_name.lower(),
        candidate.location.file_id,
    )
