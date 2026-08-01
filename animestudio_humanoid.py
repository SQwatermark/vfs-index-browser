"""将 Unity Humanoid Avatar 和 Muscle 曲线转换为普通骨骼动画。"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


HUMANOID_BONE_EXTRA = "humanoid"

_HUMAN_MASS_BONES = (
    "Hips",
    "LeftUpperLeg",
    "RightUpperLeg",
    "LeftLowerLeg",
    "RightLowerLeg",
    "LeftFoot",
    "RightFoot",
    "Spine",
    "Chest",
    "UpperChest",
    "Neck",
    "Head",
    "LeftShoulder",
    "RightShoulder",
    "LeftUpperArm",
    "RightUpperArm",
    "LeftLowerArm",
    "RightLowerArm",
    "LeftHand",
    "RightHand",
    "LeftToes",
    "RightToes",
    "LeftEye",
    "RightEye",
    "Jaw",
)

_HUMAN_SEGMENT_CHILD = {
    "Hips": "Spine",
    "Spine": "Chest",
    "Chest": "UpperChest",
    "UpperChest": "Neck",
    "Neck": "Head",
    "LeftShoulder": "LeftUpperArm",
    "LeftUpperArm": "LeftLowerArm",
    "LeftLowerArm": "LeftHand",
    "RightShoulder": "RightUpperArm",
    "RightUpperArm": "RightLowerArm",
    "RightLowerArm": "RightHand",
    "LeftUpperLeg": "LeftLowerLeg",
    "LeftLowerLeg": "LeftFoot",
    "LeftFoot": "LeftToes",
    "RightUpperLeg": "RightLowerLeg",
    "RightLowerLeg": "RightFoot",
    "RightFoot": "RightToes",
}

_BODY_CURVE_FIELDS = tuple(
    f"{prefix}.{axis}"
    for prefix, axes in (
        ("MotionT", "xyz"),
        ("MotionQ", "xyzw"),
        ("RootT", "xyz"),
        ("RootQ", "xyzw"),
    )
    for axis in axes
)

# Unity Muscle 的 X 是扭转轴，Y/Z 组成摆动。这里只列出身体 Muscle；
# 手指、IK 目标与根运动需要额外的 Avatar 求解信息，不能按同一公式处理。
_MUSCLE_AXES = {
    "Spine Front-Back": ("Spine", 2),
    "Spine Left-Right": ("Spine", 1),
    "Spine Twist Left-Right": ("Spine", 0),
    "Chest Front-Back": ("Chest", 2),
    "Chest Left-Right": ("Chest", 1),
    "Chest Twist Left-Right": ("Chest", 0),
    "UpperChest Front-Back": ("UpperChest", 2),
    "UpperChest Left-Right": ("UpperChest", 1),
    "UpperChest Twist Left-Right": ("UpperChest", 0),
    "Neck Nod Down-Up": ("Neck", 2),
    "Neck Tilt Left-Right": ("Neck", 1),
    "Neck Turn Left-Right": ("Neck", 0),
    "Head Nod Down-Up": ("Head", 2),
    "Head Tilt Left-Right": ("Head", 1),
    "Head Turn Left-Right": ("Head", 0),
    "Left Eye Down-Up": ("LeftEye", 2),
    "Left Eye In-Out": ("LeftEye", 1),
    "Right Eye Down-Up": ("RightEye", 2),
    "Right Eye In-Out": ("RightEye", 1),
    "Jaw Close": ("Jaw", 2),
    "Jaw Left-Right": ("Jaw", 1),
    "Left Upper Leg Front-Back": ("LeftUpperLeg", 2),
    "Left Upper Leg In-Out": ("LeftUpperLeg", 1),
    "Left Upper Leg Twist In-Out": ("LeftUpperLeg", 0),
    "Left Lower Leg Stretch": ("LeftLowerLeg", 2),
    "Left Lower Leg Twist In-Out": ("LeftLowerLeg", 0),
    # 终末地在标准 Unity 腿部通道中插入了脚和脚趾的额外旋转自由度。
    "Left Foot Up-Down": ("LeftFoot", 2),
    "Left Foot Twist In-Out": ("LeftFoot", 1),
    "Left Foot Twist Roll": ("LeftFoot", 0),
    "Left Toes Up-Down": ("LeftToes", 1),
    "Left Toes Left-Right": ("LeftToes", 2),
    "Left Toes Twist Roll": ("LeftToes", 0),
    "Right Upper Leg Front-Back": ("RightUpperLeg", 2),
    "Right Upper Leg In-Out": ("RightUpperLeg", 1),
    "Right Upper Leg Twist In-Out": ("RightUpperLeg", 0),
    "Right Lower Leg Stretch": ("RightLowerLeg", 2),
    "Right Lower Leg Twist In-Out": ("RightLowerLeg", 0),
    "Right Foot Up-Down": ("RightFoot", 2),
    "Right Foot Twist In-Out": ("RightFoot", 1),
    "Right Foot Twist Roll": ("RightFoot", 0),
    "Right Toes Up-Down": ("RightToes", 1),
    "Right Toes Left-Right": ("RightToes", 2),
    "Right Toes Twist Roll": ("RightToes", 0),
    "Left Shoulder Down-Up": ("LeftShoulder", 2),
    "Left Shoulder Front-Back": ("LeftShoulder", 1),
    "Left Arm Down-Up": ("LeftUpperArm", 2),
    "Left Arm Front-Back": ("LeftUpperArm", 1),
    "Left Arm Twist In-Out": ("LeftUpperArm", 0),
    "Left Forearm Stretch": ("LeftLowerArm", 2),
    "Left Forearm Twist In-Out": ("LeftLowerArm", 0),
    "Left Hand Down-Up": ("LeftHand", 2),
    "Left Hand In-Out": ("LeftHand", 1),
    "Right Shoulder Down-Up": ("RightShoulder", 2),
    "Right Shoulder Front-Back": ("RightShoulder", 1),
    "Right Arm Down-Up": ("RightUpperArm", 2),
    "Right Arm Front-Back": ("RightUpperArm", 1),
    "Right Arm Twist In-Out": ("RightUpperArm", 0),
    "Right Forearm Stretch": ("RightLowerArm", 2),
    "Right Forearm Twist In-Out": ("RightLowerArm", 0),
    "Right Hand Down-Up": ("RightHand", 2),
    "Right Hand In-Out": ("RightHand", 1),
}

_TWIST_SOLVE_PAIRS = (
    ("LeftLowerArm", "LeftHand", "foreArmTwist"),
    ("LeftUpperArm", "LeftLowerArm", "armTwist"),
    ("RightLowerArm", "RightHand", "foreArmTwist"),
    ("RightUpperArm", "RightLowerArm", "armTwist"),
    ("LeftLowerLeg", "LeftFoot", "legTwist"),
    ("LeftUpperLeg", "LeftLowerLeg", "upperLegTwist"),
    ("RightLowerLeg", "RightFoot", "legTwist"),
    ("RightUpperLeg", "RightLowerLeg", "upperLegTwist"),
)


def compute_humanoid_body_orientation(
    left_hip: list[float],
    right_hip: list[float],
    left_shoulder: list[float],
    right_shoulder: list[float],
) -> list[float]:
    """按 Unity 的髋/肩定义计算当前姿态的 Body frame。"""

    hip_center = _vector_scale(_vector_add(left_hip, right_hip), 0.5)
    shoulder_center = _vector_scale(
        _vector_add(left_shoulder, right_shoulder),
        0.5,
    )
    up = _normalized_vector(_vector_subtract(shoulder_center, hip_center))
    right = _normalized_vector(
        _vector_add(
            _vector_subtract(right_hip, left_hip),
            _vector_subtract(right_shoulder, left_shoulder),
        )
    )
    forward = _normalized_vector(_vector_cross(right, up))
    # 再正交化一次，避免输入骨架的左右向量与上方向并非严格垂直。
    right = _normalized_vector(_vector_cross(up, forward))
    return _quaternion_from_basis(right, up, forward)


def annotate_humanoid_bones(
    document: dict[str, Any],
    avatar_payloads: Iterable[Mapping[str, Any]],
) -> int:
    """把与模型骨架最匹配的 Avatar Muscle Referential 写入骨骼 extras。"""

    skeletons = [
        skeleton
        for skeleton in document.get("skeletons", [])
        if isinstance(skeleton, dict)
    ]
    candidates = []
    for payload in avatar_payloads:
        profile = _extract_avatar_profile(payload)
        if profile is None:
            continue
        for skeleton in skeletons:
            bones_by_name = {
                bone.get("name"): bone
                for bone in skeleton.get("bones", [])
                if isinstance(bone, dict) and isinstance(bone.get("name"), str)
            }
            matched = set(profile["bones"]) & set(bones_by_name)
            if matched:
                candidates.append((len(matched), profile, skeleton, bones_by_name))
    if not candidates:
        return 0

    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return 0

    attached = 0
    _, profile, skeleton, bones_by_name = candidates[0]
    skeleton.setdefault("extras", {})[HUMANOID_BONE_EXTRA] = profile["avatar"]
    nodes_by_name = {
        node.get("name"): node
        for node in document.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("name"), str)
    }
    for bone_name, metadata in profile["bones"].items():
        bone = bones_by_name.get(bone_name)
        node = nodes_by_name.get(bone_name)
        if bone is None and node is None:
            continue
        if bone is not None:
            bone.setdefault("extras", {})[HUMANOID_BONE_EXTRA] = metadata
        if node is not None:
            node.setdefault("extras", {})[HUMANOID_BONE_EXTRA] = metadata
        attached += 1
    return attached


def bake_humanoid_rotation_tracks(
    document: Mapping[str, Any],
    float_curves: Iterable[Mapping[str, Any]],
    timelines: list[list[float]],
    *,
    excluded_target_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], set[int]]:
    """把可识别的身体 Muscle 曲线烘焙为局部四元数轨道。"""

    resolved = _resolve_humanoid_skeleton(document)
    if resolved is None:
        return [], set()
    _, bones_by_human_name = resolved

    grouped: dict[str, dict[int, tuple[int, list[float], int]]] = defaultdict(dict)
    consumed = set()
    curves = list(float_curves)
    for index, curve in enumerate(curves):
        binding = _MUSCLE_AXES.get(curve.get("propertyName"))
        if binding is None:
            continue
        human_bone, axis = binding
        if human_bone not in bones_by_human_name:
            continue
        timeline_index = curve.get("timeline")
        values = _scalar_curve_values(curve.get("values"))
        if (
            not isinstance(timeline_index, int)
            or not 0 <= timeline_index < len(timelines)
            or values is None
            or len(values) != len(timelines[timeline_index])
        ):
            raise ValueError(f"invalid Humanoid muscle curve {curve.get('propertyName')!r}")
        if axis in grouped[human_bone]:
            raise ValueError(f"duplicate Humanoid muscle curve {curve.get('propertyName')!r}")
        grouped[human_bone][axis] = (timeline_index, values, index)

    tracks = []
    excluded = excluded_target_ids or set()
    output_bones = {
        human_bone: bones_by_human_name[human_bone]
        for human_bone in grouped
        if isinstance(bones_by_human_name[human_bone].get("id"), str)
        and bones_by_human_name[human_bone]["id"] not in excluded
    }
    active_grouped = {
        human_bone: grouped[human_bone]
        for human_bone in output_bones
    }
    times = sorted({
        time
        for axis_curves in active_grouped.values()
        for timeline_index, _, _ in axis_curves.values()
        for time in timelines[timeline_index]
    })
    if not times:
        return [], consumed
    timeline_index = _reuse_or_append_timeline(timelines, times)
    rows_by_bone: dict[str, list[list[float]]] = {
        human_bone: [] for human_bone in output_bones
    }
    for time in times:
        muscles_by_bone = {
            human_bone: _sample_bone_muscles(axis_curves, timelines, time, consumed)
            for human_bone, axis_curves in active_grouped.items()
        }
        rotations = {
            human_bone: _muscles_to_rotation(
                bones_by_human_name[human_bone]["extras"][HUMANOID_BONE_EXTRA],
                muscles,
            )
            for human_bone, muscles in muscles_by_bone.items()
        }
        _apply_twist_solve(rotations, muscles_by_bone, bones_by_human_name)
        for human_bone in output_bones:
            rows_by_bone[human_bone].append(rotations[human_bone])

    for human_bone, bone in output_bones.items():
        bone = bones_by_human_name[human_bone]
        target_id = bone.get("id")
        tracks.append(
            {
                "targetId": target_id,
                "property": "rotation",
                "timeline": timeline_index,
                "values": _make_quaternions_continuous(rows_by_bone[human_bone]),
            }
        )
    return tracks, consumed


def bake_humanoid_body_tracks(
    document: Mapping[str, Any],
    float_curves: Iterable[Mapping[str, Any]],
    timelines: list[list[float]],
    rotation_tracks: Iterable[Mapping[str, Any]],
    *,
    excluded_rotation_target_ids: set[str] | None = None,
    excluded_translation_target_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], set[int]]:
    """把 Unity Body 曲线恢复为 Hips 的局部位移和旋转轨道。"""

    resolved = _resolve_humanoid_skeleton(document)
    if resolved is None:
        return [], set()
    skeleton, bones_by_human_name = resolved
    avatar = skeleton.get("extras", {}).get(HUMANOID_BONE_EXTRA)
    hips = bones_by_human_name.get("Hips")
    if not isinstance(avatar, Mapping) or not isinstance(hips, Mapping):
        return [], set()
    hips_id = hips.get("id")
    if not isinstance(hips_id, str):
        return [], set()

    body_curves = _collect_body_curves(float_curves, timelines)
    if body_curves is None:
        return [], set()
    curves_by_name, consumed = body_curves
    times = sorted({
        time
        for _, _, timeline_index in curves_by_name.values()
        for time in timelines[timeline_index]
    })
    if not times:
        return [], set()

    rotations_by_target = {
        track.get("targetId"): track
        for track in rotation_tracks
        if track.get("property") == "rotation"
        and isinstance(track.get("targetId"), str)
    }
    root_transform = avatar.get("rootTransform")
    reference_offset = avatar.get("centerOfMassOffset")
    human_scale = avatar.get("humanScale")
    masses = avatar.get("boneMasses")
    if (
        not isinstance(root_transform, Mapping)
        or not isinstance(reference_offset, list)
        or len(reference_offset) != 3
        or not isinstance(human_scale, (int, float))
        or not isinstance(masses, Mapping)
    ):
        return [], set()

    root_rotation = root_transform.get("rotation")
    if not isinstance(root_rotation, list) or len(root_rotation) != 4:
        return [], set()

    hips_translations = []
    hips_rotations = []
    for time in times:
        body_translation, body_rotation = _sample_body_pose(
            curves_by_name,
            timelines,
            time,
        )
        world = _evaluate_skeleton_pose(
            document,
            skeleton,
            rotations_by_target,
            timelines,
            time,
        )
        positions = {
            human_name: world[bone["id"]][0]
            for human_name, bone in bones_by_human_name.items()
            if bone.get("id") in world
        }
        required = (
            "LeftUpperLeg",
            "RightUpperLeg",
            "LeftUpperArm",
            "RightUpperArm",
        )
        if any(name not in positions for name in required):
            return [], set()
        current_body_rotation = compute_humanoid_body_orientation(
            positions["LeftUpperLeg"],
            positions["RightUpperLeg"],
            positions["LeftUpperArm"],
            positions["RightUpperArm"],
        )
        desired_body_rotation = _normalized_quaternion(
            _quaternion_multiply(body_rotation, root_rotation)
        )
        correction = _normalized_quaternion(
            _quaternion_multiply(
                desired_body_rotation,
                _quaternion_inverse(current_body_rotation),
            )
        )
        current_center = _compute_mass_center(positions, masses)
        desired_center = _vector_add(
            _vector_scale(body_translation, float(human_scale)),
            _rotate_vector(body_rotation, reference_offset),
        )
        translation_delta = _vector_subtract(
            desired_center,
            _rotate_vector(correction, current_center),
        )
        hips_position, hips_rotation = world[hips_id]
        hips_translations.append(
            _vector_add(_rotate_vector(correction, hips_position), translation_delta)
        )
        hips_rotations.append(
            _normalized_quaternion(_quaternion_multiply(correction, hips_rotation))
        )

    output_timeline = _reuse_or_append_timeline(timelines, times)
    tracks = []
    if hips_id not in (excluded_translation_target_ids or set()):
        tracks.append({
            "targetId": hips_id,
            "property": "translation",
            "timeline": output_timeline,
            "values": hips_translations,
        })
    if hips_id not in (excluded_rotation_target_ids or set()):
        tracks.append({
            "targetId": hips_id,
            "property": "rotation",
            "timeline": output_timeline,
            "values": _make_quaternions_continuous(hips_rotations),
        })
    return tracks, consumed if tracks else set()


def _sample_bone_muscles(axis_curves, timelines, time, consumed):
    muscles = [0.0, 0.0, 0.0]
    for axis, (source_timeline, values, curve_index) in axis_curves.items():
        muscles[axis] = _sample_linear(timelines[source_timeline], values, time)
        consumed.add(curve_index)
    return muscles


def _apply_twist_solve(rotations, muscles_by_bone, bones_by_human_name):
    for parent_name, child_name, factor_name in _TWIST_SOLVE_PAIRS:
        if parent_name not in rotations or child_name not in rotations:
            continue
        metadata = bones_by_human_name[parent_name]["extras"][HUMANOID_BONE_EXTRA]
        factor = metadata["twistFactors"][factor_name]
        parent_old = rotations[parent_name]
        child_old = rotations[child_name]
        parent_muscles = muscles_by_bone[parent_name]
        parent_new = _muscles_to_rotation(
            metadata,
            [parent_muscles[0] * factor, parent_muscles[1], parent_muscles[2]],
        )
        delta = _quaternion_multiply(_quaternion_inverse(parent_old), parent_new)
        rotations[parent_name] = parent_new
        rotations[child_name] = _normalized_quaternion(
            _quaternion_multiply(_quaternion_inverse(delta), child_old)
        )


def _resolve_humanoid_skeleton(document):
    candidates = []
    for skeleton in document.get("skeletons", []):
        if not isinstance(skeleton, Mapping):
            continue
        bones = {}
        for bone in skeleton.get("bones", []):
            humanoid = bone.get("extras", {}).get(HUMANOID_BONE_EXTRA)
            if isinstance(humanoid, Mapping) and isinstance(humanoid.get("humanBone"), str):
                bones[humanoid["humanBone"]] = bone
        for node in document.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            humanoid = node.get("extras", {}).get(HUMANOID_BONE_EXTRA)
            if isinstance(humanoid, Mapping) and isinstance(humanoid.get("humanBone"), str):
                bones[humanoid["humanBone"]] = node
        if bones:
            candidates.append((len(bones), skeleton, bones))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    return candidates[0][1], candidates[0][2]


def _collect_body_curves(float_curves, timelines):
    result = {}
    consumed = set()
    for index, curve in enumerate(float_curves):
        name = curve.get("propertyName")
        if name not in _BODY_CURVE_FIELDS:
            continue
        timeline_index = curve.get("timeline")
        values = _scalar_curve_values(curve.get("values"))
        if (
            name in result
            or not isinstance(timeline_index, int)
            or not 0 <= timeline_index < len(timelines)
            or values is None
            or len(values) != len(timelines[timeline_index])
        ):
            raise ValueError(f"invalid Humanoid Body curve {name!r}")
        result[name] = (index, values, timeline_index)
        consumed.add(index)
    if set(result) != set(_BODY_CURVE_FIELDS):
        return None
    return result, consumed


def _sample_body_pose(curves, timelines, time):
    def sample(prefix, axes):
        return [
            _sample_linear(timelines[curves[f"{prefix}.{axis}"][2]], curves[f"{prefix}.{axis}"][1], time)
            for axis in axes
        ]

    motion_translation = sample("MotionT", "xyz")
    motion_rotation = _normalized_quaternion(sample("MotionQ", "xyzw"))
    root_translation = sample("RootT", "xyz")
    root_rotation = _normalized_quaternion(sample("RootQ", "xyzw"))
    inverse_motion = _quaternion_inverse(motion_rotation)
    return (
        _rotate_vector(
            inverse_motion,
            _vector_subtract(root_translation, motion_translation),
        ),
        _normalized_quaternion(_quaternion_multiply(inverse_motion, root_rotation)),
    )


def _evaluate_skeleton_pose(document, skeleton, rotation_tracks, timelines, time):
    source_nodes = document.get("nodes") or skeleton.get("bones", [])
    bones = {
        bone.get("id"): bone
        for bone in source_nodes
        if isinstance(bone, Mapping) and isinstance(bone.get("id"), str)
    }
    world = {}

    def evaluate(bone_id):
        if bone_id in world:
            return world[bone_id]
        bone = bones[bone_id]
        transform = bone.get("transform", {})
        translation = transform.get("translation", [0.0, 0.0, 0.0])
        rotation = transform.get("rotation", [0.0, 0.0, 0.0, 1.0])
        track = rotation_tracks.get(bone_id)
        if isinstance(track, Mapping):
            timeline_index = track.get("timeline")
            values = track.get("values")
            if (
                isinstance(timeline_index, int)
                and 0 <= timeline_index < len(timelines)
                and isinstance(values, list)
                and len(values) == len(timelines[timeline_index])
            ):
                rotation = _sample_quaternion(timelines[timeline_index], values, time)
        parent_id = bone.get("parentId")
        if parent_id in bones:
            parent_position, parent_rotation = evaluate(parent_id)
            position = _vector_add(parent_position, _rotate_vector(parent_rotation, translation))
            rotation = _normalized_quaternion(
                _quaternion_multiply(parent_rotation, rotation)
            )
        else:
            position = [float(value) for value in translation]
            rotation = _normalized_quaternion(rotation)
        world[bone_id] = (position, rotation)
        return world[bone_id]

    for bone_id in bones:
        evaluate(bone_id)
    return world


def _sample_quaternion(times, values, time):
    components = [
        _sample_linear(times, [row[index] for row in values], time)
        for index in range(4)
    ]
    return _normalized_quaternion(components)


def _compute_mass_center(positions, masses):
    weighted = [0.0, 0.0, 0.0]
    total = 0.0
    for human_bone, mass in masses.items():
        if not isinstance(mass, (int, float)) or mass <= 0 or human_bone not in positions:
            continue
        start = positions[human_bone]
        child_name = _HUMAN_SEGMENT_CHILD.get(human_bone)
        end = positions.get(child_name, start)
        center = _vector_scale(_vector_add(start, end), 0.5)
        weighted = _vector_add(weighted, _vector_scale(center, float(mass)))
        total += float(mass)
    if total <= 0.0:
        raise ValueError("Humanoid Avatar has no usable bone mass")
    return _vector_scale(weighted, 1.0 / total)


def _extract_avatar_profile(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    avatar = payload.get("m_Avatar")
    description = payload.get("m_HumanDescription")
    human = avatar.get("m_Human") if isinstance(avatar, Mapping) else None
    skeleton = human.get("m_Skeleton") if isinstance(human, Mapping) else None
    pose = human.get("m_SkeletonPose") if isinstance(human, Mapping) else None
    tos = payload.get("m_TOS")
    descriptions = description.get("m_Human") if isinstance(description, Mapping) else None
    if not all(isinstance(value, Mapping) for value in (avatar, human, skeleton, pose, tos)):
        return None
    if not isinstance(descriptions, list):
        return None

    nodes = skeleton.get("m_Node")
    ids = skeleton.get("m_ID")
    axes = skeleton.get("m_AxesArray")
    poses = pose.get("m_X")
    if not all(isinstance(value, list) for value in (nodes, ids, axes, poses)):
        return None

    human_names = {
        item.get("m_BoneName"): item.get("m_HumanName")
        for item in descriptions
        if isinstance(item, Mapping)
        and isinstance(item.get("m_BoneName"), str)
        and isinstance(item.get("m_HumanName"), str)
    }
    result = {}
    twist_factors = {
        "armTwist": float(human.get("m_ArmTwist", 0.5)),
        "foreArmTwist": float(human.get("m_ForeArmTwist", 0.5)),
        "upperLegTwist": float(human.get("m_UpperLegTwist", 0.5)),
        "legTwist": float(human.get("m_LegTwist", 0.5)),
    }
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping) or index >= len(ids) or index >= len(poses):
            continue
        path = tos.get(str(ids[index]))
        bone_name = path.rsplit("/", 1)[-1] if isinstance(path, str) else None
        human_name = human_names.get(bone_name)
        axis_index = node.get("m_AxesId")
        if human_name is None or not isinstance(axis_index, int) or not 0 <= axis_index < len(axes):
            continue
        axis = axes[axis_index]
        limit = axis.get("m_Limit") if isinstance(axis, Mapping) else None
        if not isinstance(limit, Mapping):
            continue
        try:
            result[bone_name] = {
                "humanBone": human_name,
                "preRotation": _vector(axis["m_PreQ"], "XYZW"),
                "postRotation": _vector(axis["m_PostQ"], "XYZW"),
                "axisSign": _vector(axis["m_Sgn"], "XYZ"),
                "limitMin": _vector(limit["m_Min"], "XYZ"),
                "limitMax": _vector(limit["m_Max"], "XYZ"),
                "referenceTranslation": _vector(poses[index]["t"], "XYZ"),
                "referenceRotation": _vector(poses[index]["q"], "XYZW"),
                "axisLength": float(axis.get("m_Length", 0.0)),
                "twistFactors": twist_factors,
            }
        except (KeyError, TypeError, ValueError):
            continue
    root = human.get("m_RootX")
    scale = human.get("m_Scale")
    masses = human.get("m_HumanBoneMass")
    if (
        not result
        or not isinstance(root, Mapping)
        or not isinstance(scale, (int, float))
        or not isinstance(masses, list)
        or len(masses) != len(_HUMAN_MASS_BONES)
        or not all(isinstance(value, (int, float)) for value in masses)
    ):
        return None
    try:
        bone_masses = {
            name: float(mass)
            for name, mass in zip(_HUMAN_MASS_BONES, masses)
        }
        avatar_metadata = {
            "rootTransform": {
                "translation": _vector(root["t"], "XYZ"),
                "rotation": _vector(root["q"], "XYZW"),
            },
            "humanScale": float(scale),
            "boneMasses": bone_masses,
        }
        reference_positions = _avatar_human_bone_positions(human)
        if reference_positions:
            avatar_metadata["centerOfMassOffset"] = _vector_subtract(
                _compute_mass_center(reference_positions, bone_masses),
                avatar_metadata["rootTransform"]["translation"],
            )
    except (KeyError, TypeError, ValueError):
        return None
    return {"avatar": avatar_metadata, "bones": result}


def _avatar_human_bone_positions(human):
    skeleton = human.get("m_Skeleton")
    pose = human.get("m_SkeletonPose")
    nodes = skeleton.get("m_Node") if isinstance(skeleton, Mapping) else None
    transforms = pose.get("m_X") if isinstance(pose, Mapping) else None
    bone_indexes = human.get("m_HumanBoneIndex")
    if not all(isinstance(value, list) for value in (nodes, transforms, bone_indexes)):
        return {}
    world = {}

    def evaluate(index):
        if index in world:
            return world[index]
        if not 0 <= index < len(nodes) or index >= len(transforms):
            raise ValueError("Avatar skeleton index is out of range")
        node = nodes[index]
        transform = transforms[index]
        local_position = _vector(transform["t"], "XYZ")
        local_rotation = _vector(transform["q"], "XYZW")
        parent_index = node.get("m_ParentId")
        if isinstance(parent_index, int) and parent_index >= 0:
            parent_position, parent_rotation = evaluate(parent_index)
            position = _vector_add(
                parent_position,
                _rotate_vector(parent_rotation, local_position),
            )
            rotation = _normalized_quaternion(
                _quaternion_multiply(parent_rotation, local_rotation)
            )
        else:
            position = local_position
            rotation = _normalized_quaternion(local_rotation)
        world[index] = (position, rotation)
        return world[index]

    result = {}
    for human_name, index in zip(_HUMAN_MASS_BONES, bone_indexes):
        if isinstance(index, int) and index >= 0:
            result[human_name] = evaluate(index)[0]
    return result


def _muscles_to_rotation(metadata: Mapping[str, Any], muscles: list[float]) -> list[float]:
    minimum = metadata["limitMin"]
    maximum = metadata["limitMax"]
    sign = metadata["axisSign"]
    angles = [
        sign[index]
        * muscle
        * (maximum[index] if muscle >= 0.0 else -minimum[index])
        for index, muscle in enumerate(muscles)
    ]
    # Unity Humanoid 使用特定的 swing-twist 参数化，不能用普通轴角近似。
    tx, ty, tz = (math.tan(angle * 0.5) for angle in angles)
    swing_twist = _normalized_quaternion(
        [tx, ty + tx * tz, tz - tx * ty, 1.0]
    )
    rotation = _quaternion_multiply(
        _quaternion_multiply(
            metadata["preRotation"],
            swing_twist,
        ),
        _quaternion_inverse(metadata["postRotation"]),
    )
    return _normalized_quaternion(rotation)


def _make_quaternions_continuous(rows):
    result = []
    for row in rows:
        current = _normalized_quaternion(row)
        if result and sum(left * right for left, right in zip(result[-1], current)) < 0.0:
            current = [-value for value in current]
        result.append(current)
    return result


def _normalized_quaternion(value):
    length = math.sqrt(sum(component * component for component in value))
    if not length:
        raise ValueError("zero-length quaternion")
    return [component / length for component in value]


def _sample_linear(times: list[float], values: list[float], time: float) -> float:
    position = bisect.bisect_left(times, time)
    if position <= 0:
        return values[0]
    if position >= len(times):
        return values[-1]
    left_time = times[position - 1]
    right_time = times[position]
    if right_time == left_time:
        return values[position]
    weight = (time - left_time) / (right_time - left_time)
    return values[position - 1] * (1.0 - weight) + values[position] * weight


def _reuse_or_append_timeline(timelines: list[list[float]], times: list[float]) -> int:
    for index, timeline in enumerate(timelines):
        if timeline == times:
            return index
    timelines.append(times)
    return len(timelines) - 1


def _scalar_curve_values(value: Any) -> list[float] | None:
    if not isinstance(value, list):
        return None
    result = []
    for item in value:
        if isinstance(item, (int, float)):
            result.append(float(item))
        elif (
            isinstance(item, list)
            and len(item) == 1
            and isinstance(item[0], (int, float))
        ):
            result.append(float(item[0]))
        else:
            return None
    return result


def _vector(value: Mapping[str, Any], fields: str) -> list[float]:
    result = [value[field] for field in fields]
    if not all(isinstance(item, (int, float)) for item in result):
        raise ValueError("vector contains a non-number")
    return [float(item) for item in result]


def _vector_add(left, right):
    return [a + b for a, b in zip(left, right)]


def _vector_subtract(left, right):
    return [a - b for a, b in zip(left, right)]


def _vector_scale(value, scale):
    return [component * scale for component in value]


def _vector_cross(left, right):
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _normalized_vector(value):
    length = math.sqrt(sum(component * component for component in value))
    if length < 1e-8:
        raise ValueError("Humanoid Body frame contains a degenerate axis")
    return [component / length for component in value]


def _quaternion_from_basis(right, up, forward):
    # Basis vectors are matrix columns in Unity's right/up/forward convention.
    m00, m10, m20 = right
    m01, m11, m21 = up
    m02, m12, m22 = forward
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = [
            (m21 - m12) / scale,
            (m02 - m20) / scale,
            (m10 - m01) / scale,
            scale * 0.25,
        ]
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        quaternion = [
            scale * 0.25,
            (m01 + m10) / scale,
            (m02 + m20) / scale,
            (m21 - m12) / scale,
        ]
    elif m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        quaternion = [
            (m01 + m10) / scale,
            scale * 0.25,
            (m12 + m21) / scale,
            (m02 - m20) / scale,
        ]
    else:
        scale = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        quaternion = [
            (m02 + m20) / scale,
            (m12 + m21) / scale,
            scale * 0.25,
            (m10 - m01) / scale,
        ]
    return _normalized_quaternion(quaternion)


def _quaternion_multiply(left: list[float], right: list[float]) -> list[float]:
    x1, y1, z1, w1 = left
    x2, y2, z2, w2 = right
    return [
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ]


def _quaternion_inverse(value: list[float]) -> list[float]:
    length_squared = sum(component * component for component in value)
    return [
        -value[0] / length_squared,
        -value[1] / length_squared,
        -value[2] / length_squared,
        value[3] / length_squared,
    ]


def _rotate_vector(rotation, value):
    vector_quaternion = [value[0], value[1], value[2], 0.0]
    rotated = _quaternion_multiply(
        _quaternion_multiply(rotation, vector_quaternion),
        _quaternion_inverse(rotation),
    )
    return rotated[:3]
