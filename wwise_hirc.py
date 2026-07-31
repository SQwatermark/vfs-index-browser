"""Minimal, evidence-preserving Wwise HIRC graph parser for Endfield banks."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable


HIRC_OBJECT_KINDS = {
    2: "sound",
    3: "action",
    4: "event",
    5: "randomSequenceContainer",
    6: "switchContainer",
    7: "actorMixer",
    9: "blendContainer",
    10: "musicSegment",
    11: "musicTrack",
    12: "musicSwitchContainer",
    13: "musicPlaylistContainer",
}


class WwiseFormatError(ValueError):
    """A bank does not satisfy the structural contract used by this parser."""


def normalize_wwise_id(value: int) -> int:
    """Interpret signed game-side int32 references as Wwise uint32 IDs."""

    if not isinstance(value, int) or not -(1 << 31) <= value < (1 << 32):
        raise ValueError("Wwise ID must fit signed int32 or uint32")
    return value & 0xFFFFFFFF


@dataclass(frozen=True)
class WwiseObject:
    bank_id: int
    object_type: int
    object_id: int
    payload_offset: int
    payload_size: int
    payload: bytes

    @property
    def kind(self) -> str:
        return HIRC_OBJECT_KINDS.get(self.object_type, f"type{self.object_type}")


@dataclass(frozen=True)
class WwiseRelation:
    bank_id: int
    source_kind: str
    source_id: int
    relation: str
    target_kind: str
    target_id: int
    confidence: str
    evidence: str


@dataclass(frozen=True)
class WwiseDiagnostic:
    bank_id: int
    object_type: int
    object_kind: str
    object_id: int
    message: str


@dataclass(frozen=True)
class WwiseBankGraph:
    bank_id: int
    objects: tuple[WwiseObject, ...]
    relations: tuple[WwiseRelation, ...]
    diagnostics: tuple[WwiseDiagnostic, ...] = ()

    def media_ids_for_event(self, event_id: int) -> tuple[int, ...]:
        """Traverse all known graph edges from an event to physical media IDs."""

        edges: dict[tuple[str, int], list[tuple[str, int]]] = {}
        for relation in self.relations:
            edges.setdefault(
                (relation.source_kind, relation.source_id), []
            ).append((relation.target_kind, relation.target_id))
        pending = [("event", normalize_wwise_id(event_id))]
        visited = set()
        media = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for target in edges.get(current, ()):
                if target[0] == "media":
                    media.add(target[1])
                else:
                    pending.append(target)
        return tuple(sorted(media))


def parse_soundbank(bank_id: int, payload: bytes) -> WwiseBankGraph:
    chunks = _bank_chunks(payload)
    hirc = chunks.get(b"HIRC")
    if hirc is None:
        return WwiseBankGraph(bank_id, (), ())
    objects = _parse_hirc_objects(bank_id, hirc[0], hirc[1])
    relations, diagnostics = _extract_relations(bank_id, objects)
    return WwiseBankGraph(bank_id, objects, relations, diagnostics)


def _bank_chunks(payload: bytes) -> dict[bytes, tuple[bytes, int]]:
    chunks = {}
    pos = 0
    while pos < len(payload):
        if pos + 8 > len(payload):
            raise WwiseFormatError("truncated SoundBank chunk header")
        name = payload[pos : pos + 4]
        size = struct.unpack_from("<I", payload, pos + 4)[0]
        start = pos + 8
        end = start + size
        if end > len(payload):
            raise WwiseFormatError(f"SoundBank chunk {name!r} exceeds bank boundary")
        if name in chunks:
            raise WwiseFormatError(f"duplicate SoundBank chunk: {name!r}")
        chunks[name] = (payload[start:end], start)
        pos = end
    return chunks


def _parse_hirc_objects(
    bank_id: int,
    hirc: bytes,
    chunk_payload_offset: int,
) -> tuple[WwiseObject, ...]:
    if len(hirc) < 4:
        raise WwiseFormatError("HIRC chunk has no object count")
    count = struct.unpack_from("<I", hirc, 0)[0]
    pos = 4
    objects = []
    for index in range(count):
        if pos + 5 > len(hirc):
            raise WwiseFormatError(f"HIRC object {index} header is truncated")
        object_type = hirc[pos]
        size = struct.unpack_from("<I", hirc, pos + 1)[0]
        start = pos + 5
        end = start + size
        if size < 4 or end > len(hirc):
            raise WwiseFormatError(f"HIRC object {index} has an invalid size")
        object_id = struct.unpack_from("<I", hirc, start)[0]
        objects.append(
            WwiseObject(
                bank_id=bank_id,
                object_type=object_type,
                object_id=object_id,
                payload_offset=chunk_payload_offset + start,
                payload_size=size,
                payload=hirc[start:end],
            )
        )
        pos = end
    if pos != len(hirc):
        raise WwiseFormatError(f"HIRC contains {len(hirc) - pos} trailing bytes")
    return tuple(objects)


def _extract_relations(
    bank_id: int,
    objects: Iterable[WwiseObject],
) -> tuple[tuple[WwiseRelation, ...], tuple[WwiseDiagnostic, ...]]:
    object_list = tuple(objects)
    kinds = {item.object_id: item.kind for item in object_list}
    relations = []
    diagnostics = []
    for item in object_list:
        try:
            if item.object_type == 4:
                relations.extend(_event_relations(item, kinds))
            elif item.object_type == 3:
                relations.extend(_action_relations(item, kinds))
            elif item.object_type == 2:
                relations.extend(_sound_relations(item))
            elif item.object_type == 5:
                relations.extend(_random_container_relations(item, kinds))
            elif item.object_type == 7:
                relations.extend(_actor_mixer_relations(item, kinds))
        except WwiseFormatError as error:
            diagnostics.append(
                WwiseDiagnostic(
                    bank_id=item.bank_id,
                    object_type=item.object_type,
                    object_kind=item.kind,
                    object_id=item.object_id,
                    message=str(error),
                )
            )
    return tuple(relations), tuple(diagnostics)


def _relation(
    source: WwiseObject,
    relation: str,
    target_kind: str,
    target_id: int,
    evidence: str,
) -> WwiseRelation:
    return WwiseRelation(
        bank_id=source.bank_id,
        source_kind=source.kind,
        source_id=source.object_id,
        relation=relation,
        target_kind=target_kind,
        target_id=target_id,
        confidence="structural",
        evidence=evidence,
    )


def _event_relations(
    item: WwiseObject,
    kinds: dict[int, str],
) -> list[WwiseRelation]:
    count, pos = _read_varuint(item.payload, 4)
    end = pos + count * 4
    if end != len(item.payload):
        raise WwiseFormatError(f"event {item.object_id} action list is malformed")
    result = []
    for index in range(count):
        target = struct.unpack_from("<I", item.payload, pos + index * 4)[0]
        result.append(_relation(item, "triggers", kinds.get(target, "action"), target, "event.actionList"))
    return result


def _action_relations(
    item: WwiseObject,
    kinds: dict[int, str],
) -> list[WwiseRelation]:
    if len(item.payload) < 10:
        raise WwiseFormatError(f"action {item.object_id} is truncated")
    target = struct.unpack_from("<I", item.payload, 6)[0]
    return [_relation(item, "targets", kinds.get(target, "object"), target, "action.idExt")]


def _sound_relations(item: WwiseObject) -> list[WwiseRelation]:
    if len(item.payload) < 13:
        raise WwiseFormatError(f"sound {item.object_id} source data is truncated")
    media_id = struct.unpack_from("<I", item.payload, 9)[0]
    return [_relation(item, "usesMedia", "media", media_id, "sound.sourceID")]


def _random_container_relations(
    item: WwiseObject,
    kinds: dict[int, str],
) -> list[WwiseRelation]:
    candidates = []
    payload = item.payload
    for pos in range(4, len(payload) - 5):
        count = struct.unpack_from("<I", payload, pos)[0]
        children_end = pos + 4 + count * 4
        if not 0 < count <= 4096 or children_end + 2 > len(payload):
            continue
        children = tuple(
            struct.unpack_from("<I", payload, pos + 4 + index * 4)[0]
            for index in range(count)
        )
        if any(child not in kinds for child in children):
            continue
        playlist_count = struct.unpack_from("<H", payload, children_end)[0]
        if playlist_count != count or children_end + 2 + playlist_count * 8 != len(payload):
            continue
        candidates.append(children)
    if len(candidates) != 1:
        raise WwiseFormatError(
            f"random/sequence container {item.object_id} has {len(candidates)} structural child lists"
        )
    return [
        _relation(item, "contains", kinds[child], child, "randomSequence.children")
        for child in candidates[0]
    ]


def _actor_mixer_relations(
    item: WwiseObject,
    kinds: dict[int, str],
) -> list[WwiseRelation]:
    payload = item.payload
    candidates = []
    for pos in range(4, len(payload) - 3):
        count = struct.unpack_from("<I", payload, pos)[0]
        if not 0 < count <= 4096 or pos + 4 + count * 4 != len(payload):
            continue
        children = tuple(
            struct.unpack_from("<I", payload, pos + 4 + index * 4)[0]
            for index in range(count)
        )
        if all(child in kinds for child in children):
            candidates.append(children)
    if len(candidates) != 1:
        raise WwiseFormatError(
            f"actor mixer {item.object_id} has {len(candidates)} structural child lists"
        )
    return [
        _relation(item, "contains", kinds[child], child, "actorMixer.children")
        for child in candidates[0]
    ]


def _read_varuint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(5):
        if offset >= len(data):
            raise WwiseFormatError("truncated Wwise variable-length integer")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset
    raise WwiseFormatError("Wwise variable-length integer exceeds 5 bytes")
