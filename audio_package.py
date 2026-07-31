"""Read Endfield AKPK metadata while preserving both banks and media entries."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from wwise_hirc import WwiseBankGraph, parse_soundbank


ReadRange = Callable[[int, int], bytes]


@dataclass(frozen=True)
class AudioPackageBank:
    bank_id: int
    offset: int
    size: int
    language: str | None
    encrypted: bool
    graph: WwiseBankGraph


@dataclass(frozen=True)
class AudioPackageMedia:
    media_id: int
    offset: int
    size: int
    source: str
    language: str | None
    bank_id: int | None = None
    bank_offset: int | None = None
    bank_size: int | None = None
    bank_media_offset: int | None = None
    bank_encrypted: bool = False


@dataclass(frozen=True)
class AudioPackageIndex:
    file_size: int
    banks: tuple[AudioPackageBank, ...]
    media: tuple[AudioPackageMedia, ...]


def derive_audio_key(seed: int) -> int:
    key = ((seed & 0xFF) ^ 0x9C5A0B29) * 81861667
    key &= 0xFFFFFFFF
    for shift in (8, 16, 24):
        key = (key ^ ((seed >> shift) & 0xFF)) * 81861667
        key &= 0xFFFFFFFF
    return key


def decrypt_audio_bytes(data: bytearray, seed: int, *, data_offset: int = 0) -> None:
    key_index = (seed + (data_offset >> 2)) & 0xFFFFFFFF
    pos = 0
    alignment = data_offset & 3
    if alignment:
        key = derive_audio_key(key_index)
        count = min(4 - alignment, len(data))
        for index in range(count):
            data[pos] ^= (key >> ((alignment + index) * 8)) & 0xFF
            pos += 1
        key_index = (key_index + 1) & 0xFFFFFFFF
    while pos + 4 <= len(data):
        value = int.from_bytes(data[pos : pos + 4], "little") ^ derive_audio_key(key_index)
        data[pos : pos + 4] = value.to_bytes(4, "little")
        pos += 4
        key_index = (key_index + 1) & 0xFFFFFFFF
    if pos < len(data):
        key = derive_audio_key(key_index)
        for index in range(len(data) - pos):
            data[pos + index] ^= (key >> (index * 8)) & 0xFF


def parse_audio_package_bytes(payload: bytes, label: str = "PCK") -> AudioPackageIndex:
    return parse_audio_package(
        lambda offset, size: payload[offset : offset + size],
        len(payload),
        label,
    )


def parse_audio_package(
    read_range: ReadRange,
    file_size: int,
    label: str = "PCK",
) -> AudioPackageIndex:
    probe = read_range(0, min(file_size, 28))
    if len(probe) < 24 or probe[:4] not in {b"AKPK", b":)xD"}:
        raise ValueError(f"invalid AKPK header: {label}")
    header_size = struct.unpack_from("<I", probe, 4)[0]
    read_size = header_size + 8 if probe[:4] == b":)xD" else header_size
    if read_size < 24 or read_size > file_size:
        raise ValueError(f"invalid AKPK header size: {label}")
    header = bytearray(read_range(0, read_size))
    if header[:4] == b":)xD":
        decrypt_audio_bytes(memoryview(header)[12 : 12 + header_size - 4], header_size)
        header[:4] = b"AKPK"
        header[8:12] = (1).to_bytes(4, "little")

    language_size, banks_size, sounds_size = struct.unpack_from("<III", header, 12)
    has_externals = language_size + banks_size + sounds_size + 0x10 < header_size
    externals_size = struct.unpack_from("<I", header, 24)[0] if has_externals else 0
    start = 28 if has_externals else 24
    languages = _parse_languages(header, start, language_size)
    banks = []
    media = []
    pos = start + language_size
    bank_rows = _parse_sector(header, pos, banks_size, languages, externals=False)
    pos += banks_size
    sound_rows = _parse_sector(header, pos, sounds_size, languages, externals=False)
    pos += sounds_size
    external_rows = (
        _parse_sector(header, pos, externals_size, languages, externals=True)
        if externals_size
        else ()
    )

    for bank_id, offset, size, language in bank_rows:
        raw = bytearray(read_range(offset, size))
        encrypted = raw[:4] != b"BKHD"
        if encrypted:
            decrypt_audio_bytes(raw, bank_id)
        bank_payload = bytes(raw)
        graph = parse_soundbank(bank_id, bank_payload)
        banks.append(AudioPackageBank(bank_id, offset, size, language, encrypted, graph))
        for media_id, media_offset, media_size in _embedded_media(bank_payload):
            media.append(
                AudioPackageMedia(
                    media_id,
                    offset + media_offset,
                    media_size,
                    "bank",
                    language,
                    bank_id,
                    offset,
                    size,
                    media_offset,
                    encrypted,
                )
            )
    for media_id, offset, size, language in sound_rows:
        media.append(AudioPackageMedia(media_id, offset, size, "sound", language))
    for media_id, offset, size, language in external_rows:
        media.append(AudioPackageMedia(media_id, offset, size, "external", language))
    return AudioPackageIndex(file_size, tuple(banks), tuple(media))


def _parse_languages(data: bytes, start: int, size: int) -> dict[int, str]:
    if size < 4 or start + size > len(data):
        raise ValueError("invalid AKPK language sector")
    count = struct.unpack_from("<I", data, start)[0]
    result = {}
    pos = start + 4
    for _ in range(count):
        if pos + 8 > start + size:
            raise ValueError("truncated AKPK language row")
        name_offset, language_id = struct.unpack_from("<II", data, pos)
        name_start = start + name_offset
        raw = data[name_start : min(start + size, name_start + 64)]
        if len(raw) >= 2 and (raw[0] == 0 or raw[1] == 0):
            name = raw.decode("utf-16-le", errors="strict").split("\0", 1)[0]
        else:
            name = raw.decode("utf-8", errors="strict").split("\0", 1)[0]
        if not name:
            raise ValueError("AKPK language row has an empty name")
        result[language_id] = name
        pos += 8
    return result


def _parse_sector(
    data: bytes,
    start: int,
    size: int,
    languages: dict[int, str],
    *,
    externals: bool,
) -> tuple[tuple[int, int, int, str | None], ...]:
    if size == 0:
        return ()
    if start + size > len(data) or size < 4:
        raise ValueError("invalid AKPK file sector")
    count = struct.unpack_from("<I", data, start)[0]
    if count == 0:
        return ()
    if (size - 4) % count:
        raise ValueError("AKPK file sector rows have inconsistent sizes")
    entry_size = (size - 4) // count
    if entry_size not in {20, 24}:
        raise ValueError(f"unsupported AKPK file row size: {entry_size}")
    rows = []
    pos = start + 4
    for _ in range(count):
        file_low = struct.unpack_from("<I", data, pos)[0]
        cursor = pos + 4
        file_high = None
        if entry_size == 24 and externals:
            file_high = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
        block_size = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        if entry_size == 24 and not externals:
            file_size = struct.unpack_from("<Q", data, cursor)[0]
            cursor += 8
        else:
            file_size = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
        offset = struct.unpack_from("<I", data, cursor)[0]
        language_id = struct.unpack_from("<I", data, cursor + 4)[0]
        if block_size:
            offset *= block_size
        file_id = (file_high << 32 | file_low) if file_high is not None else file_low
        rows.append((file_id, offset, file_size, languages.get(language_id)))
        pos += entry_size
    return tuple(rows)


def _embedded_media(payload: bytes) -> tuple[tuple[int, int, int], ...]:
    chunks = {}
    pos = 0
    while pos < len(payload):
        if pos + 8 > len(payload):
            raise ValueError("truncated SoundBank chunk header")
        name = payload[pos : pos + 4]
        size = struct.unpack_from("<I", payload, pos + 4)[0]
        start = pos + 8
        end = start + size
        if end > len(payload):
            raise ValueError("SoundBank chunk exceeds bank boundary")
        chunks[name] = (start, payload[start:end])
        pos = end
    if b"DIDX" not in chunks or b"DATA" not in chunks:
        return ()
    didx_start, didx = chunks[b"DIDX"]
    data_start, data = chunks[b"DATA"]
    if len(didx) % 12:
        raise ValueError("SoundBank DIDX row size is invalid")
    rows = []
    for pos in range(0, len(didx), 12):
        media_id, offset, size = struct.unpack_from("<III", didx, pos)
        if offset + size > len(data):
            raise ValueError(f"SoundBank media {media_id} exceeds DATA chunk")
        rows.append((media_id, data_start + offset, size))
    return tuple(rows)
