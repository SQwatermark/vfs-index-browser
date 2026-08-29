from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


FNV64_OFFSET = 0xCBF29CE484222325
FNV64_PRIME = 0x100000001B3
UINT64_MASK = 0xFFFFFFFFFFFFFFFF

MatchStatus = Literal["matched", "missing", "ambiguous", "collision"]

_DIALOG_KEY_PATTERN = re.compile(r"-?\d+\Z")
_LANGUAGE_ALIASES = {
    "chinese": "chinese",
    "cn": "chinese",
    "english": "english",
    "en": "english",
    "japanese": "japanese",
    "jp": "japanese",
    "korean": "korean",
    "kr": "korean",
}
_MEDIA_LANGUAGE_ALIASES = {
    **_LANGUAGE_ALIASES,
    "sfx": "sfx",
}


class AudioDialogFormatError(ValueError):
    """AudioDialog or media index data does not match the expected contract."""


@dataclass(frozen=True)
class AudioDialogSourceEntry:
    dialog_key: int
    logical_path: str


@dataclass(frozen=True)
class AudioDialogRecord:
    dialog_key: int
    language: str
    logical_path: str
    normalized_hash_input: str
    media_id: int

    @property
    def media_id_hex(self) -> str:
        return f"{self.media_id:016x}"


@dataclass(frozen=True)
class AudioMediaEntry:
    media_id: int
    pck_file_id: int
    offset: int
    size: int
    source: str
    language: str | None = None
    bank_id: int | None = None
    bank_offset: int | None = None
    bank_size: int | None = None
    bank_wem_offset: int | None = None
    bank_encrypted: bool = False
    pck_logical_path: str | None = None
    pck_file_size: int | None = None

    def __post_init__(self) -> None:
        _validate_uint64(self.media_id, "media_id")
        _validate_nonnegative_int64(self.pck_file_id, "pck_file_id")
        _validate_nonnegative_int64(self.offset, "offset")
        _validate_positive_int64(self.size, "size")
        if (
            not isinstance(self.source, str)
            or not self.source.strip()
            or self.source != self.source.strip()
        ):
            raise AudioDialogFormatError("media source must be a non-empty trimmed string")
        if self.language is not None:
            object.__setattr__(self, "language", normalize_audio_media_language(self.language))
        for name in ("bank_id", "bank_offset", "bank_size", "bank_wem_offset"):
            value = getattr(self, name)
            if value is not None:
                _validate_nonnegative_int64(value, name)
        if not isinstance(self.bank_encrypted, bool):
            raise AudioDialogFormatError("bank_encrypted must be a boolean")
        if (self.pck_logical_path is None) != (self.pck_file_size is None):
            raise AudioDialogFormatError(
                "pck_logical_path and pck_file_size must be provided together"
            )
        if self.pck_logical_path is not None:
            if not self.pck_logical_path.strip() or self.pck_logical_path != self.pck_logical_path.strip():
                raise AudioDialogFormatError(
                    "pck_logical_path must be a non-empty trimmed string"
                )
            _validate_positive_int64(self.pck_file_size, "pck_file_size")

    @property
    def media_id_hex(self) -> str:
        return f"{self.media_id:016x}"

    def sqlite_record(self) -> dict[str, int | str | bool | None]:
        return {
            "media_id": self.media_id_hex,
            "pck_file_id": self.pck_file_id,
            "offset": self.offset,
            "size": self.size,
            "source": self.source,
            "language": self.language,
            "bank_id": self.bank_id,
            "bank_offset": self.bank_offset,
            "bank_size": self.bank_size,
            "bank_wem_offset": self.bank_wem_offset,
            "bank_encrypted": self.bank_encrypted,
            "pck_logical_path": self.pck_logical_path,
            "pck_file_size": self.pck_file_size,
        }


@dataclass(frozen=True)
class AudioDialogMatch:
    record: AudioDialogRecord
    status: MatchStatus
    media_entries: tuple[AudioMediaEntry, ...]

    @property
    def media_match_count(self) -> int:
        return len(self.media_entries)

    def sqlite_record(self) -> dict[str, int | str]:
        return {
            "dialog_key": self.record.dialog_key,
            "language": self.record.language,
            "logical_path": self.record.logical_path,
            "normalized_hash_input": self.record.normalized_hash_input,
            "media_id": self.record.media_id_hex,
            "match_status": self.status,
            "media_match_count": self.media_match_count,
        }


def parse_audio_dialog(payload: object) -> tuple[AudioDialogSourceEntry, ...]:
    if not isinstance(payload, dict):
        raise AudioDialogFormatError("AudioDialog top level must be an object")

    entries: list[AudioDialogSourceEntry] = []
    seen_keys: set[int] = set()
    for raw_key, row in payload.items():
        if not isinstance(raw_key, str) or not _DIALOG_KEY_PATTERN.fullmatch(raw_key):
            raise AudioDialogFormatError(
                f"AudioDialog key must be a decimal integer string: {raw_key!r}"
            )
        dialog_key = int(raw_key)
        _validate_int64(dialog_key, "AudioDialog key")
        if dialog_key in seen_keys:
            raise AudioDialogFormatError(f"AudioDialog contains duplicate numeric key: {dialog_key}")
        seen_keys.add(dialog_key)

        if not isinstance(row, dict):
            raise AudioDialogFormatError(f"AudioDialog[{raw_key!r}] must be an object")
        if "path" not in row:
            raise AudioDialogFormatError(f"AudioDialog[{raw_key!r}] is missing path")
        logical_path = normalize_audio_dialog_path(row["path"], raw_key)
        entries.append(AudioDialogSourceEntry(dialog_key=dialog_key, logical_path=logical_path))

    return tuple(sorted(entries, key=lambda entry: entry.dialog_key))


def normalize_audio_language(language: object) -> str:
    if not isinstance(language, str) or not language.strip():
        raise AudioDialogFormatError("audio language must be a non-empty string")
    normalized = language.strip().lower()
    try:
        return _LANGUAGE_ALIASES[normalized]
    except KeyError as exc:
        raise AudioDialogFormatError(f"unsupported audio language: {language!r}") from exc


def normalize_audio_media_language(language: object) -> str:
    """Normalize language tags emitted by PCK metadata.

    Wwise packages use ``sfx`` for language-neutral media. It is a valid
    package classification, but it must not match any AudioDialog language.
    """

    if not isinstance(language, str) or not language.strip():
        raise AudioDialogFormatError("media language must be a non-empty string")
    normalized = language.strip().lower()
    try:
        return _MEDIA_LANGUAGE_ALIASES[normalized]
    except KeyError as exc:
        raise AudioDialogFormatError(f"unsupported media language: {language!r}") from exc


def normalize_audio_dialog_path(path: object, dialog_key: str | int | None = None) -> str:
    prefix = f"AudioDialog[{dialog_key!r}] path" if dialog_key is not None else "AudioDialog path"
    if not isinstance(path, str) or not path:
        raise AudioDialogFormatError(f"{prefix} must be a non-empty string")
    if path != path.strip():
        raise AudioDialogFormatError(f"{prefix} must not have leading or trailing whitespace")
    if "\0" in path:
        raise AudioDialogFormatError(f"{prefix} contains a NUL byte")

    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or normalized.endswith("/"):
        raise AudioDialogFormatError(f"{prefix} must be a relative file path")
    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise AudioDialogFormatError(f"{prefix} contains an empty or relative segment")
    if ":" in segments[0]:
        raise AudioDialogFormatError(f"{prefix} must not contain a drive prefix")
    return normalized


def make_voice_hash_input(path: object, language: object) -> str:
    logical_path = normalize_audio_dialog_path(path)
    normalized_language = normalize_audio_language(language)
    return f"voice/{normalized_language}/{logical_path}".lower()


def endfield_fnv1_64(data: bytes) -> int:
    if not isinstance(data, bytes):
        raise TypeError("Endfield FNV input must be bytes")
    value = FNV64_OFFSET
    for byte in data:
        value = ((value * FNV64_PRIME) & UINT64_MASK) ^ byte
    return value


def hash_audio_dialog_path(path: object, language: object) -> tuple[str, int]:
    normalized_hash_input = make_voice_hash_input(path, language)
    return normalized_hash_input, endfield_fnv1_64(normalized_hash_input.encode("utf-8"))


def build_audio_dialog_records(payload: object, language: object) -> tuple[AudioDialogRecord, ...]:
    normalized_language = normalize_audio_language(language)
    records = []
    for source_entry in parse_audio_dialog(payload):
        hash_input, media_id = hash_audio_dialog_path(source_entry.logical_path, normalized_language)
        records.append(
            AudioDialogRecord(
                dialog_key=source_entry.dialog_key,
                language=normalized_language,
                logical_path=source_entry.logical_path,
                normalized_hash_input=hash_input,
                media_id=media_id,
            )
        )
    return tuple(records)


def map_audio_dialog_records(
    records: Iterable[AudioDialogRecord],
    media_entries: Iterable[AudioMediaEntry],
) -> tuple[AudioDialogMatch, ...]:
    record_list = tuple(records)
    media_by_id: dict[int, list[AudioMediaEntry]] = {}
    for media_entry in media_entries:
        if not isinstance(media_entry, AudioMediaEntry):
            raise TypeError("media_entries must contain AudioMediaEntry values")
        media_by_id.setdefault(media_entry.media_id, []).append(media_entry)

    hash_inputs_by_id: dict[int, set[str]] = {}
    for record in record_list:
        _validate_record(record)
        hash_inputs_by_id.setdefault(record.media_id, set()).add(record.normalized_hash_input)
    collision_ids = {
        media_id
        for media_id, hash_inputs in hash_inputs_by_id.items()
        if len(hash_inputs) > 1
    }

    matches = []
    for record in record_list:
        candidates = tuple(
            sorted(
                (
                    entry
                    for entry in media_by_id.get(record.media_id, ())
                    if entry.language in {None, "sfx", record.language}
                ),
                key=_media_sort_key,
            )
        )
        if record.media_id in collision_ids:
            status: MatchStatus = "collision"
        elif not candidates:
            status = "missing"
        elif len(candidates) == 1:
            status = "matched"
        else:
            status = "ambiguous"
        matches.append(AudioDialogMatch(record=record, status=status, media_entries=candidates))

    return tuple(matches)


def build_audio_dialog_index(
    payload: object,
    language: object,
    media_entries: Iterable[AudioMediaEntry],
) -> tuple[AudioDialogMatch, ...]:
    return map_audio_dialog_records(build_audio_dialog_records(payload, language), media_entries)


def media_entries_from_audio_package_meta(
    pck_file_id: int,
    payload: object,
) -> tuple[AudioMediaEntry, ...]:
    _validate_nonnegative_int64(pck_file_id, "pck_file_id")
    if not isinstance(payload, dict):
        raise AudioDialogFormatError("audio package metadata must be an object")
    legacy = payload.get("version") == 1
    identity = payload.get("identity")
    if not legacy and not isinstance(identity, dict):
        raise AudioDialogFormatError(
            f"unsupported audio package metadata version: {payload.get('version')!r}"
        )
    pck_logical_path = None if legacy else identity.get("logicalId")
    pck_file_size = None if legacy else identity.get("length")
    if not legacy and (not pck_logical_path or pck_file_size is None):
        raise AudioDialogFormatError(
            "audio package metadata identity is missing logicalId or length"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise AudioDialogFormatError("audio package metadata entries must be an array")
    if payload.get("entryCount") != len(entries):
        raise AudioDialogFormatError("audio package metadata entryCount is inconsistent")

    result = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AudioDialogFormatError(
                f"audio package metadata entry {index} must be an object"
            )
        try:
            result.append(
                AudioMediaEntry(
                    media_id=entry["id"],
                    pck_file_id=pck_file_id,
                    offset=entry["offset"],
                    size=entry["size"],
                    source=entry["source"],
                    language=entry.get("language"),
                    bank_id=entry.get("bankId"),
                    bank_offset=entry.get("bankOffset"),
                    bank_size=entry.get("bankSize"),
                    bank_wem_offset=entry.get("bankWemOffset"),
                    bank_encrypted=entry.get("bankEncrypted", False),
                    pck_logical_path=pck_logical_path,
                    pck_file_size=pck_file_size,
                )
            )
        except KeyError as error:
            raise AudioDialogFormatError(
                f"audio package metadata entry {index} is missing {error.args[0]}"
            ) from error
    return tuple(result)


def build_audio_dialog_index_from_packages(
    payload: object,
    language: object,
    packages: Iterable[tuple[int, object]],
) -> tuple[AudioDialogMatch, ...]:
    media_entries = []
    seen_pck_ids = set()
    for pck_file_id, package_meta in packages:
        if pck_file_id in seen_pck_ids:
            raise AudioDialogFormatError(
                f"audio package file {pck_file_id!r} was supplied more than once"
            )
        seen_pck_ids.add(pck_file_id)
        media_entries.extend(
            media_entries_from_audio_package_meta(pck_file_id, package_meta)
        )
    return build_audio_dialog_index(payload, language, media_entries)


def _validate_record(record: AudioDialogRecord) -> None:
    if not isinstance(record, AudioDialogRecord):
        raise TypeError("records must contain AudioDialogRecord values")
    _validate_uint64(record.media_id, "media_id")
    expected_language = normalize_audio_language(record.language)
    expected_input = make_voice_hash_input(record.logical_path, expected_language)
    if record.language != expected_language:
        raise AudioDialogFormatError("AudioDialogRecord language is not canonical")
    if record.normalized_hash_input != expected_input:
        raise AudioDialogFormatError("AudioDialogRecord normalized_hash_input is inconsistent")


def _validate_uint64(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UINT64_MASK:
        raise AudioDialogFormatError(f"{name} must be an unsigned 64-bit integer")


def _validate_nonnegative_int64(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < (1 << 63):
        raise AudioDialogFormatError(f"{name} must be a non-negative signed 64-bit integer")


def _validate_positive_int64(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 63):
        raise AudioDialogFormatError(f"{name} must be a positive signed 64-bit integer")


def _validate_int64(value: object, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not -(1 << 63) <= value < (1 << 63)
    ):
        raise AudioDialogFormatError(f"{name} must be a signed 64-bit integer")


def _media_sort_key(entry: AudioMediaEntry) -> tuple[int, int, int, str, int]:
    return (
        entry.pck_file_id,
        entry.offset,
        entry.size,
        entry.source,
        entry.bank_id if entry.bank_id is not None else -1,
    )
