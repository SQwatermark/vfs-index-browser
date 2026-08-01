"""Parse and bake Endfield's skeletal facial-morph assets.

Dialog ``morphanim`` clips animate transient controller objects rather than
model bones.  Their sibling ``morphanimso`` assets name those controllers,
while a character-specific ``SkeletalMorphAvatarDataSO`` maps each controller
to absolute local poses for the facial skeleton.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class SkeletalMorphError(ValueError):
    """Raised when a skeletal-morph asset does not match the known layout."""


@dataclass(frozen=True)
class BonePose:
    name_hash: int
    bone_id: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]


@dataclass(frozen=True)
class MorphMapping:
    kind: str
    identifier: int
    name_hash: int
    tag_hash: int
    part_type: int
    bones: tuple[BonePose, ...]


@dataclass(frozen=True)
class CurveKey:
    time: float
    value: float
    in_slope: float
    out_slope: float
    weighted_mode: int
    in_weight: float
    out_weight: float


@dataclass(frozen=True)
class MorphCurve:
    control_name: str
    keys: tuple[CurveKey, ...]
    pre_infinity: int
    post_infinity: int
    rotation_order: int


@dataclass(frozen=True)
class MorphClip:
    name: str
    duration: float
    additive: bool
    override: bool
    pause_auto_blink: bool
    curves: tuple[MorphCurve, ...]


@dataclass(frozen=True)
class MorphAvatar:
    name: str
    base_poses: tuple[BonePose, ...]
    bone_names: tuple[str, ...]
    mapping_names: tuple[str, ...]
    mappings: tuple[MorphMapping, ...]
    blend_shape_mapping: tuple[tuple[int, int], ...]


def is_dialog_morph_animation_path(path: str) -> bool:
    normalized = path.replace("\\", "/").casefold()
    return "/morphanim/" in normalized and normalized.endswith(".anim")


def morph_clip_asset_path(animation_path: str) -> str:
    normalized = animation_path.replace("\\", "/")
    replaced = re.sub("/morphanim/", "/morphanimso/", normalized, flags=re.IGNORECASE)
    if replaced == normalized or not replaced.casefold().endswith(".anim"):
        raise SkeletalMorphError(f"not a dialog morph animation path: {animation_path!r}")
    return replaced[:-5] + ".asset"


def morph_avatar_asset_name(model_path: str) -> str:
    normalized = model_path.replace("\\", "/")
    patterns = (
        r"/chr_\d+_([^/]+?)_postmodel\.prefab$",
        r"/data_npc_avatarmesh_([^/]+?)\.asset$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return f"data_facemorph_avatar_{match.group(1)}.asset"
    raise SkeletalMorphError(f"cannot infer facial-morph avatar from {model_path!r}")


class _Reader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def _read(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.payload):
            raise SkeletalMorphError(f"unexpected end of data at 0x{self.offset:x}")
        value = struct.unpack_from(fmt, self.payload, self.offset)[0]
        self.offset += size
        return value

    def int32(self) -> int:
        return int(self._read("<i"))

    def int64(self) -> int:
        return int(self._read("<q"))

    def uint8(self) -> int:
        return int(self._read("<B"))

    def float32(self) -> float:
        return float(self._read("<f"))

    def align4(self) -> None:
        self.offset = (self.offset + 3) & ~3

    def aligned_bool(self) -> bool:
        value = self.uint8()
        if value not in (0, 1):
            raise SkeletalMorphError(f"invalid boolean {value} at 0x{self.offset - 1:x}")
        self.align4()
        return bool(value)

    def string(self) -> str:
        length = self.int32()
        if length < 0 or self.offset + length > len(self.payload):
            raise SkeletalMorphError(f"invalid string length {length} at 0x{self.offset - 4:x}")
        start = self.offset
        self.offset += length
        self.align4()
        try:
            return self.payload[start : start + length].decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkeletalMorphError(f"invalid UTF-8 string at 0x{start:x}") from error

    def vector3(self) -> tuple[float, float, float]:
        return (self.float32(), self.float32(), self.float32())

    def finish(self) -> None:
        if self.offset != len(self.payload):
            raise SkeletalMorphError(
                f"unexpected trailing data: {len(self.payload) - self.offset} bytes"
            )


def _read_monobehaviour_header(reader: _Reader) -> str:
    reader.int32()  # m_GameObject.m_FileID
    reader.int64()  # m_GameObject.m_PathID
    reader.aligned_bool()  # m_Enabled
    reader.int32()  # m_Script.m_FileID
    reader.int64()  # m_Script.m_PathID
    return reader.string()


def _read_bone_pose(reader: _Reader) -> BonePose:
    return BonePose(
        name_hash=reader.int32(),
        bone_id=reader.int32(),
        position=reader.vector3(),
        rotation=reader.vector3(),
        scale=reader.vector3(),
    )


def _read_array(reader: _Reader, item_reader, *, limit: int = 100_000) -> tuple:
    count = reader.int32()
    if count < 0 or count > limit:
        raise SkeletalMorphError(f"invalid array size {count} at 0x{reader.offset - 4:x}")
    return tuple(item_reader(reader) for _ in range(count))


def _read_string_array(reader: _Reader) -> tuple[str, ...]:
    return _read_array(reader, lambda source: source.string())


def parse_morph_avatar(payload: bytes) -> MorphAvatar:
    """Parse one raw ``SkeletalMorphAvatarDataSO`` MonoBehaviour payload."""

    reader = _Reader(payload)
    name = _read_monobehaviour_header(reader)
    reader.int32()  # avatar.m_FileID
    reader.int64()  # avatar.m_PathID
    reader.int32()  # prefabWithRendererHelper.m_FileID
    reader.int64()  # prefabWithRendererHelper.m_PathID
    reader.int32()  # GameplayTag.tagId
    reader.int32()  # SkeletalMorphDataType
    reader.aligned_bool()  # disableBlink
    reader.aligned_bool()  # useSpecialParams

    base_poses = _read_array(reader, _read_bone_pose, limit=10_000)
    shader_param_refs = _read_array(reader, lambda source: source.int64(), limit=1_000)
    mapping_refs = _read_array(reader, lambda source: source.int64(), limit=10_000)
    bone_names = _read_string_array(reader)
    shader_param_names = _read_string_array(reader)
    mapping_names = _read_string_array(reader)
    blend_shape_hashes = _read_array(reader, lambda source: source.int32())
    blend_shape_indexes = _read_array(reader, lambda source: source.int32())
    if len(blend_shape_hashes) != len(blend_shape_indexes):
        raise SkeletalMorphError("blend-shape morph hash map has mismatched arrays")

    registry_version = reader.int32()
    if registry_version != 2:
        raise SkeletalMorphError(
            f"unsupported managed-reference registry version {registry_version}"
        )
    reference_count = reader.int32()
    expected_count = len(shader_param_refs) + len(mapping_refs)
    if reference_count != expected_count:
        raise SkeletalMorphError(
            f"managed-reference count is {reference_count}, expected {expected_count}"
        )

    references: dict[int, tuple[str, Any]] = {}
    for _ in range(reference_count):
        rid = reader.int64()
        class_name = reader.string()
        namespace = reader.string()
        assembly = reader.string()
        if namespace != "Beyond.Gameplay.Core" or assembly != "Gameplay.Beyond":
            raise SkeletalMorphError(
                f"unexpected managed type {namespace}.{class_name} from {assembly}"
            )
        if class_name == "SkMorphShaderParamFloat":
            value = {
                "name": reader.string(),
                "rendererMask": reader.int32(),
                "defaultValue": reader.float32(),
            }
        elif class_name in {"SkeletalMorphMappingData", "SkeletalMorphShaderPropMappingData"}:
            value = MorphMapping(
                kind=(
                    "shader"
                    if class_name == "SkeletalMorphShaderPropMappingData"
                    else "bone"
                ),
                identifier=reader.int32(),
                name_hash=reader.int32(),
                tag_hash=reader.int32(),
                part_type=reader.int32(),
                bones=_read_array(reader, _read_bone_pose, limit=10_000),
            )
            if class_name == "SkeletalMorphShaderPropMappingData":
                reader.int32()  # vectorIndex
                reader.int64()  # shaderParam managed-reference RID
        else:
            raise SkeletalMorphError(f"unsupported managed type {class_name!r}")
        if rid in references:
            raise SkeletalMorphError(f"duplicate managed-reference RID {rid}")
        references[rid] = (class_name, value)
    reader.finish()

    for rid, expected_name in zip(shader_param_refs, shader_param_names, strict=True):
        entry = references.get(rid)
        if entry is None or not entry[0].startswith("SkMorphShaderParam"):
            raise SkeletalMorphError(f"shader parameter RID {rid} does not resolve")
        if entry[1]["name"].lstrip("_") != expected_name.split("__")[-1]:
            raise SkeletalMorphError(f"shader parameter RID {rid} has an unexpected name")

    mappings = []
    for index, (rid, mapping_name) in enumerate(zip(mapping_refs, mapping_names, strict=True)):
        entry = references.get(rid)
        if entry is None or not isinstance(entry[1], MorphMapping):
            raise SkeletalMorphError(f"morph mapping RID {rid} does not resolve")
        mapping = entry[1]
        if mapping.identifier != index:
            raise SkeletalMorphError(
                f"morph mapping {mapping_name!r} has id {mapping.identifier}, expected {index}"
            )
        mappings.append(mapping)

    if len(base_poses) != len(bone_names):
        raise SkeletalMorphError(
            f"base pose count {len(base_poses)} does not match bone name count {len(bone_names)}"
        )
    return MorphAvatar(
        name,
        base_poses,
        bone_names,
        mapping_names,
        tuple(mappings),
        tuple(zip(blend_shape_hashes, blend_shape_indexes, strict=True)),
    )


def _read_curve(reader: _Reader) -> MorphCurve:
    control_name = reader.string()
    keys = _read_array(
        reader,
        lambda source: CurveKey(
            time=source.float32(),
            value=source.float32(),
            in_slope=source.float32(),
            out_slope=source.float32(),
            weighted_mode=source.int32(),
            in_weight=source.float32(),
            out_weight=source.float32(),
        ),
        limit=100_000,
    )
    return MorphCurve(
        control_name=control_name,
        keys=keys,
        pre_infinity=reader.int32(),
        post_infinity=reader.int32(),
        rotation_order=reader.int32(),
    )


def parse_morph_clip(payload: bytes) -> MorphClip:
    """Parse one raw dialog ``SkeletalMorphAnimSO`` MonoBehaviour payload."""

    reader = _Reader(payload)
    name = _read_monobehaviour_header(reader)
    reader.int32()  # GameplayTag.tagId
    duration = reader.float32()
    additive = reader.aligned_bool()
    override = reader.aligned_bool()
    pause_auto_blink = reader.aligned_bool()
    groups = tuple(_read_array(reader, _read_curve, limit=10_000) for _ in range(8))
    reader.finish()
    curves = tuple(curve for group in groups for curve in group)
    if duration < 0 or any(not curve.keys for curve in curves):
        raise SkeletalMorphError("morph animation contains an invalid duration or empty curve")
    for curve in curves:
        if any(right.time < left.time for left, right in zip(curve.keys, curve.keys[1:])):
            raise SkeletalMorphError(f"morph curve {curve.control_name!r} is not time-sorted")
        if any(key.weighted_mode != 0 for key in curve.keys):
            raise SkeletalMorphError(
                f"morph curve {curve.control_name!r} uses unsupported weighted tangents"
            )
        if any(
            math.isnan(value)
            for key in curve.keys
            for value in (key.time, key.value, key.in_slope, key.out_slope)
        ):
            raise SkeletalMorphError(
                f"morph curve {curve.control_name!r} contains NaN keyframe data"
            )
        if curve.pre_infinity != 2 or curve.post_infinity != 2:
            raise SkeletalMorphError(
                f"morph curve {curve.control_name!r} uses unsupported infinity modes"
            )
    return MorphClip(name, duration, additive, override, pause_auto_blink, curves)


def _evaluate_curve(curve: MorphCurve, time: float) -> float:
    keys = curve.keys
    if time <= keys[0].time:
        return keys[0].value
    if time >= keys[-1].time:
        return keys[-1].value
    for left, right in zip(keys, keys[1:]):
        if time > right.time:
            continue
        if time == right.time:
            return right.value
        duration = right.time - left.time
        if duration <= 0:
            return right.value
        # Unity serializes constant/stepped tangents as Infinity.  The value is
        # held for the whole interval and changes only at the following key.
        if not math.isfinite(left.out_slope) or not math.isfinite(right.in_slope):
            return left.value
        position = (time - left.time) / duration
        position2 = position * position
        position3 = position2 * position
        return (
            (2 * position3 - 3 * position2 + 1) * left.value
            + (position3 - 2 * position2 + position) * duration * left.out_slope
            + (-2 * position3 + 3 * position2) * right.value
            + (position3 - position2) * duration * right.in_slope
        )
    raise AssertionError("curve interval lookup failed")


def _quaternion_multiply(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    x1, y1, z1, w1 = left
    x2, y2, z2, w2 = right
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )


def _axis_quaternion(axis: int, degrees: float) -> tuple[float, float, float, float]:
    half_angle = math.radians(degrees) * 0.5
    value = math.sin(half_angle)
    components = [0.0, 0.0, 0.0, math.cos(half_angle)]
    components[axis] = value
    return tuple(components)  # type: ignore[return-value]


def _model_quaternion(euler: tuple[float, float, float]) -> list[float]:
    # The morph configuration stores Maya-style ZYX Euler angles.  AnimeStudio's
    # model snapshot mirrors Unity's Y/Z quaternion components for the viewer.
    quaternion = _quaternion_multiply(
        _quaternion_multiply(_axis_quaternion(2, euler[2]), _axis_quaternion(1, euler[1])),
        _axis_quaternion(0, euler[0]),
    )
    x, y, z, w = quaternion
    length = math.sqrt(x * x + y * y + z * z + w * w)
    return [x / length, -y / length, -z / length, w / length]


def _normalized_quaternion(value: tuple[float, float, float, float]) -> list[float]:
    length = math.sqrt(sum(component * component for component in value))
    if not math.isfinite(length) or length <= 1e-12:
        raise SkeletalMorphError("facial morph produced an invalid quaternion")
    return [component / length for component in value]


def _quaternion_conjugate(
    value: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    x, y, z, w = value
    return (-x, -y, -z, w)


def _lerp_pose(
    base: BonePose,
    contributions: list[tuple[float, BonePose]],
    model_transform: Mapping[str, Any],
) -> tuple[list[float], list[float], list[float]]:
    def combine(attribute: str) -> tuple[float, float, float]:
        origin = getattr(base, attribute)
        return tuple(
            origin[index]
            + sum(weight * (getattr(target, attribute)[index] - origin[index]) for weight, target in contributions)
            for index in range(3)
        )

    model_position = tuple(float(value) for value in model_transform.get("translation", (0, 0, 0)))
    model_rotation = tuple(float(value) for value in model_transform.get("rotation", (0, 0, 0, 1)))
    model_scale = tuple(float(value) for value in model_transform.get("scale", (1, 1, 1)))
    if len(model_position) != 3 or len(model_rotation) != 4 or len(model_scale) != 3:
        raise SkeletalMorphError("facial bone has an invalid model transform")

    avatar_position = combine("position")
    position = [
        model_position[index] + avatar_position[index] - base.position[index]
        for index in range(3)
    ]

    avatar_base_rotation = tuple(_model_quaternion(base.rotation))
    avatar_rotation = tuple(_model_quaternion(combine("rotation")))
    rotation_delta = _quaternion_multiply(
        _quaternion_conjugate(avatar_base_rotation),
        avatar_rotation,
    )
    rotation = _normalized_quaternion(_quaternion_multiply(model_rotation, rotation_delta))

    avatar_scale = combine("scale")
    scale = []
    for index in range(3):
        if abs(base.scale[index]) <= 1e-8:
            scale.append(model_scale[index] + avatar_scale[index] - base.scale[index])
        else:
            scale.append(model_scale[index] * avatar_scale[index] / base.scale[index])
    return position, rotation, scale


def bake_morph_animation(
    document: Mapping[str, Any],
    clip: MorphClip,
    avatar: MorphAvatar,
    *,
    animation_id: str,
    source: Mapping[str, Any],
    sample_rate: float = 30.0,
) -> dict[str, Any]:
    """Expand semantic morph controls into ordinary facial-bone tracks."""

    if clip.additive:
        raise SkeletalMorphError("additive skeletal-morph clips are not supported yet")
    if sample_rate <= 0:
        raise SkeletalMorphError("sample rate must be positive")

    nodes_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for node in document.get("nodes", []):
        if isinstance(node, Mapping) and isinstance(node.get("name"), str):
            nodes_by_name.setdefault(node["name"], []).append(node)

    meshes_by_id = {
        mesh.get("id"): mesh
        for mesh in document.get("meshes", [])
        if isinstance(mesh, Mapping) and isinstance(mesh.get("id"), str)
    }

    base_by_id = {pose.bone_id: pose for pose in avatar.base_poses}
    name_by_id = {
        pose.bone_id: name
        for pose, name in zip(avatar.base_poses, avatar.bone_names, strict=True)
    }
    mapping_by_name = dict(zip(avatar.mapping_names, avatar.mappings, strict=True))
    unknown_controls = sorted(
        curve.control_name for curve in clip.curves if curve.control_name not in mapping_by_name
    )
    if unknown_controls:
        raise SkeletalMorphError(
            f"morph controls are absent from avatar mapping: {', '.join(unknown_controls)}"
        )

    frame_count = max(math.ceil(clip.duration * sample_rate), 1)
    times = [min(index / sample_rate, clip.duration) for index in range(frame_count + 1)]
    if times[-1] != clip.duration:
        times.append(clip.duration)

    affected: dict[int, list[tuple[MorphCurve, BonePose]]] = {}
    blend_shape_controls: dict[tuple[str, str], list[MorphCurve]] = {}
    blend_shape_index_by_hash = dict(avatar.blend_shape_mapping)
    unsupported_controls = []
    for curve in clip.curves:
        mapping = mapping_by_name[curve.control_name]
        if mapping.kind == "shader":
            unsupported_controls.append(curve.control_name)
            continue
        blend_shape_index = blend_shape_index_by_hash.get(mapping.name_hash)
        has_blend_shape = False
        if blend_shape_index is not None:
            candidates = []
            for node in document.get("nodes", []):
                if not isinstance(node, Mapping) or not node.get("active", True):
                    continue
                lod_level = node.get("extras", {}).get("lodLevel")
                if lod_level not in (None, 0):
                    continue
                inferred_lod = re.search(
                    r"(?:^|_)lod(\d+)(?:$|_)",
                    str(node.get("name", "")),
                    flags=re.IGNORECASE,
                )
                if inferred_lod is not None and int(inferred_lod.group(1)) != 0:
                    continue
                mesh = meshes_by_id.get(node.get("meshId"))
                shapes = mesh.get("blendShapes", []) if isinstance(mesh, Mapping) else []
                if blend_shape_index < len(shapes):
                    candidates.append((node, shapes[blend_shape_index]))
            if len(candidates) != 1:
                raise SkeletalMorphError(
                    f"control {curve.control_name!r} blend shape {blend_shape_index} "
                    f"resolves to {len(candidates)} preview mesh nodes"
                )
            node, blend_shape = candidates[0]
            target_id = node.get("id")
            shape_name = blend_shape.get("name")
            if not isinstance(target_id, str) or not isinstance(shape_name, str):
                raise SkeletalMorphError(
                    f"control {curve.control_name!r} has an invalid blend-shape target"
                )
            blend_shape_controls.setdefault((target_id, shape_name), []).append(curve)
            has_blend_shape = True
        if not mapping.bones and not has_blend_shape:
            unsupported_controls.append(curve.control_name)
        for target in mapping.bones:
            if target.bone_id not in base_by_id:
                raise SkeletalMorphError(
                    f"control {curve.control_name!r} references unknown bone id {target.bone_id}"
                )
            affected.setdefault(target.bone_id, []).append((curve, target))

    tracks = []
    for bone_id, controls in sorted(affected.items()):
        bone_name = name_by_id[bone_id]
        candidates = nodes_by_name.get(bone_name, [])
        if len(candidates) != 1:
            raise SkeletalMorphError(
                f"facial bone {bone_name!r} resolves to {len(candidates)} model nodes"
            )
        target_id = candidates[0].get("id")
        model_transform = candidates[0].get("transform", {})
        if not isinstance(target_id, str):
            raise SkeletalMorphError(f"facial bone {bone_name!r} has no stable node id")
        if not isinstance(model_transform, Mapping):
            raise SkeletalMorphError(f"facial bone {bone_name!r} has no valid transform")
        values = {"translation": [], "rotation": [], "scale": []}
        for time_value in times:
            contributions = [
                (_evaluate_curve(curve, time_value), target)
                for curve, target in controls
            ]
            position, rotation, scale = _lerp_pose(
                base_by_id[bone_id],
                contributions,
                model_transform,
            )
            values["translation"].append(position)
            values["rotation"].append(rotation)
            values["scale"].append(scale)
        for property_name, rows in values.items():
            component_count = 4 if property_name == "rotation" else 3
            if all(
                abs(row[index] - rows[0][index]) < 1e-8
                for row in rows[1:]
                for index in range(component_count)
            ):
                continue
            tracks.append(
                {
                    "targetId": target_id,
                    "property": property_name,
                    "timeline": 0,
                    "values": rows,
                }
            )

    for (target_id, shape_name), curves in sorted(blend_shape_controls.items()):
        rows = [
            [sum(_evaluate_curve(curve, time_value) for curve in curves)]
            for time_value in times
        ]
        if all(abs(row[0] - rows[0][0]) < 1e-8 for row in rows[1:]):
            continue
        tracks.append(
            {
                "targetId": target_id,
                "property": "blendShapeWeight",
                "propertyName": shape_name,
                "timeline": 0,
                "values": rows,
            }
        )

    diagnostics = [{
        "severity": "info",
        "code": (
            "ANIMATION_SKELETAL_MORPH_BAKED"
            if clip.curves
            else "ANIMATION_SKELETAL_MORPH_EMPTY"
        ),
        "message": (
            f"{len(clip.curves) - len(unsupported_controls)} skeletal-morph controls "
            f"were baked into {len(tracks)} facial tracks."
            if clip.curves
            else "The skeletal-morph asset contains no animation curves."
        ),
        "objectId": animation_id,
    }]
    if unsupported_controls:
        diagnostics.append(
            {
                "severity": "warning",
                "code": "ANIMATION_SKELETAL_MORPH_NON_BONE_UNSUPPORTED",
                "message": (
                    f"{len(unsupported_controls)} skeletal-morph controls target "
                    "shader or blend-shape state that the model viewer cannot apply yet."
                ),
                "objectId": animation_id,
                "details": {"controls": sorted(unsupported_controls)},
            }
        )

    return {
        "format": "EndfieldModelAnimation",
        "version": "1.0.0",
        "id": animation_id,
        "name": clip.name,
        "duration": clip.duration,
        "sampleRate": sample_rate,
        "timelines": [times],
        "tracks": tracks,
        "source": dict(source),
        "diagnostics": diagnostics,
    }
