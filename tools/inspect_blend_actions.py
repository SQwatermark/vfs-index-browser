"""Print Blender action ownership and pose-bone curve counts for diagnostics."""

from __future__ import annotations

import json

import bpy
from mathutils import Vector


def action_channelbags(action):
    channelbags = []
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                channelbags.append(channelbag)
    return channelbags


def scene_mesh_bounds():
    corners = [
        item.matrix_world @ Vector(corner)
        for item in bpy.context.scene.objects
        if item.type == "MESH" and not item.hide_get()
        for corner in item.bound_box
    ]
    if not corners:
        return None
    minimum = Vector(map(min, zip(*corners)))
    maximum = Vector(map(max, zip(*corners)))
    return {
        "minimum": list(minimum),
        "maximum": list(maximum),
        "size": list(maximum - minimum),
    }


actions = []
for action in bpy.data.actions:
    channelbags = action_channelbags(action)
    curves = [curve for channelbag in channelbags for curve in channelbag.fcurves]
    slot_summaries = []
    for channelbag in channelbags:
        slot_curves = list(channelbag.fcurves)
        slot_summaries.append(
            {
                "slot": channelbag.slot.identifier,
                "curveCount": len(slot_curves),
                "poseCurveCount": sum(
                    curve.data_path.startswith('pose.bones["')
                    for curve in slot_curves
                ),
                "sampleCurvePaths": [
                    curve.data_path for curve in slot_curves[:3]
                ],
            }
        )
    actions.append(
        {
            "name": action.name,
            "slots": [slot.identifier for slot in action.slots],
            "curveCount": len(curves),
            "poseCurveCount": sum(
                curve.data_path.startswith('pose.bones["') for curve in curves
            ),
            "objectCurveCount": sum(
                not curve.data_path.startswith('pose.bones["') for curve in curves
            ),
            "sampleCurvePaths": [curve.data_path for curve in curves[:5]],
            "largestSlots": sorted(
                slot_summaries,
                key=lambda item: item["curveCount"],
                reverse=True,
            )[:5],
        }
    )

objects = []
action_switch_probes = []
pose_motion_probes = []
orientation_probes = []
scene = bpy.context.scene
scene.frame_set(scene.frame_start)
animated_bounds = scene_mesh_bounds()
active_actions = {}
for item in bpy.data.objects:
    if item.type != "ARMATURE":
        continue
    animation_data = item.animation_data
    active_actions[item.name] = animation_data.action if animation_data else None
    objects.append(
        {
            "name": item.name,
            "boneCount": len(item.data.bones),
            "location": list(item.location),
            "rotationEuler": list(item.rotation_euler),
            "scale": list(item.scale),
            "activeAction": (
                animation_data.action.name
                if animation_data is not None and animation_data.action is not None
                else None
            ),
            "activeSlot": (
                animation_data.action_slot.identifier
                if animation_data is not None and animation_data.action_slot is not None
                else None
            ),
        }
    )
    if animation_data is not None and animation_data.action is not None:
        start, end = animation_data.action.frame_range
        middle = start + ((end - start) * 0.5)
        bpy.context.scene.frame_set(round(start))
        start_matrices = {
            bone.name: tuple(value for row in bone.matrix_basis for value in row)
            for bone in item.pose.bones
        }
        bpy.context.scene.frame_set(round(middle))
        changed_bones = [
            bone.name
            for bone in item.pose.bones
            if any(
                abs(left - right) > 1e-7
                for left, right in zip(
                    start_matrices[bone.name],
                    (value for row in bone.matrix_basis for value in row),
                )
            )
        ]
        pose_motion_probes.append(
            {
                "object": item.name,
                "action": animation_data.action.name,
                "start": start,
                "middle": middle,
                "changedBoneCount": len(changed_bones),
                "sampleChangedBones": changed_bones[:10],
            }
        )
    if animation_data is not None and len(bpy.data.actions) > 1:
        original_action = animation_data.action
        expected_slot = f"OB{item.name}"
        target_action = next(
            action
            for action in bpy.data.actions
            if action != original_action
            and any(slot.identifier == expected_slot for slot in action.slots)
        )
        animation_data.action = target_action
        action_switch_probes.append(
            {
                "object": item.name,
                "action": target_action.name,
                "selectedSlot": (
                    animation_data.action_slot.identifier
                    if animation_data.action_slot is not None
                    else None
                ),
            }
        )
        animation_data.action = original_action

primary_armature = max(
    (item for item in bpy.data.objects if item.type == "ARMATURE"),
    key=lambda item: len(item.data.bones),
    default=None,
)
if primary_armature is not None and primary_armature.animation_data is not None:
    original_action = primary_armature.animation_data.action
    expected_slot = f"OB{primary_armature.name}"
    for action in bpy.data.actions:
        if not any(slot.identifier == expected_slot for slot in action.slots):
            continue
        primary_armature.animation_data.action = action
        scene.frame_set(round(action.frame_range[0]))
        world_up = primary_armature.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
        orientation_probes.append(
            {
                "action": action.name,
                "rotationQuaternion": list(primary_armature.rotation_quaternion),
                "worldUp": list(world_up.normalized()),
            }
        )
    primary_armature.animation_data.action = original_action
    scene.frame_set(scene.frame_start)

for item in bpy.data.objects:
    if item.type == "ARMATURE" and item.animation_data is not None:
        item.animation_data.action = None
scene.frame_set(scene.frame_start)
rest_bounds = scene_mesh_bounds()
for item in bpy.data.objects:
    if item.type == "ARMATURE" and item.animation_data is not None:
        item.animation_data.action = active_actions[item.name]
scene.frame_set(scene.frame_start)

print(
    "ENDAXIS_BLEND_ACTION_REPORT="
    + json.dumps(
        {
            "activeObject": (
                bpy.context.view_layer.objects.active.name
                if bpy.context.view_layer.objects.active is not None
                else None
            ),
            "selectedObjects": [item.name for item in bpy.context.selected_objects],
            "animatedBounds": animated_bounds,
            "restBounds": rest_bounds,
            "objects": objects,
            "actionSwitchProbes": action_switch_probes,
            "poseMotionProbes": pose_motion_probes,
            "orientationProbes": orientation_probes,
            "actions": actions,
        }
    )
)
