"""Print Blender action ownership and pose-bone curve counts for diagnostics."""

from __future__ import annotations

import json

import bpy


def action_channelbags(action):
    channelbags = []
    for layer in action.layers:
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                channelbags.append(channelbag)
    return channelbags


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
for item in bpy.data.objects:
    if item.type != "ARMATURE":
        continue
    animation_data = item.animation_data
    objects.append(
        {
            "name": item.name,
            "boneCount": len(item.data.bones),
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
            "objects": objects,
            "actionSwitchProbes": action_switch_probes,
            "poseMotionProbes": pose_motion_probes,
            "actions": actions,
        }
    )
)
