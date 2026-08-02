"""Embedded Blender UI for switching one multi-slot Endfield action."""

import json
import math

import bpy
from bpy.props import EnumProperty


ACTION_NAMES_KEY = "endfield_action_names"


def exported_action_names(scene):
    try:
        names = json.loads(scene.get(ACTION_NAMES_KEY, "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(names, list):
        return []
    return [
        name
        for name in names
        if isinstance(name, str) and bpy.data.actions.get(name)
    ]


def action_items(_self, context):
    return [
        (name, name, "")
        for name in exported_action_names(context.scene)
    ]


def animatable_ids():
    yield from bpy.data.objects
    yield from bpy.data.shape_keys


def assigned_slot_targets():
    targets = {}
    for target in animatable_ids():
        animation_data = target.animation_data
        if animation_data is None or animation_data.action_slot is None:
            continue
        targets[animation_data.action_slot.identifier] = target
    return targets


def target_for_slot(slot, assigned_targets):
    target = assigned_targets.get(slot.identifier)
    if target is not None and target.id_type == slot.target_id_type:
        return target

    # glTF 使用 Blender 的两字符 ID 前缀生成稳定 Slot 标识。
    collection = {
        "OBJECT": bpy.data.objects,
        "KEY": bpy.data.shape_keys,
    }.get(slot.target_id_type)
    if collection is None or len(slot.identifier) < 3:
        return None
    return collection.get(slot.identifier[2:])


def apply_action(scene, action):
    assigned_targets = assigned_slot_targets()
    bindings = []
    for slot in action.slots:
        target = target_for_slot(slot, assigned_targets)
        if target is None:
            continue
        animation_data = target.animation_data_create()
        animation_data.action = action
        animation_data.action_slot = slot
        bindings.append((target, slot))

    exported_names = set(exported_action_names(scene))
    bound_targets = {target.as_pointer() for target, _slot in bindings}
    for target in animatable_ids():
        animation_data = target.animation_data
        if (
            target.as_pointer() not in bound_targets
            and animation_data is not None
            and animation_data.action is not None
            and animation_data.action.name in exported_names
        ):
            animation_data.action = None

    scene.frame_start = math.floor(action.frame_range[0])
    scene.frame_end = math.ceil(action.frame_range[1])
    scene.frame_set(scene.frame_start)
    return bindings


class ENDAXIS_OT_apply_action(bpy.types.Operator):
    bl_idname = "endaxis.apply_action"
    bl_label = "应用动作"
    bl_description = "把所选多槽动作同步应用到骨架和所有辅助对象"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        action = bpy.data.actions.get(context.scene.endfield_action_name)
        if action is None:
            self.report({"ERROR"}, "请选择一个有效动作")
            return {"CANCELLED"}
        bindings = apply_action(context.scene, action)
        if not bindings:
            self.report({"ERROR"}, "动作没有可绑定到当前模型的 Slot")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已将动作应用到 {len(bindings)} 个部件")
        return {"FINISHED"}


class ENDAXIS_PT_action_switcher(bpy.types.Panel):
    bl_label = "终末地动作"
    bl_idname = "ENDAXIS_PT_action_switcher"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Endfield"

    def draw(self, context):
        layout = self.layout
        if not exported_action_names(context.scene):
            layout.label(text="此文件不包含动作", icon="INFO")
            return
        layout.prop(context.scene, "endfield_action_name", text="动作")
        layout.operator(ENDAXIS_OT_apply_action.bl_idname, icon="PLAY")


CLASSES = (
    ENDAXIS_OT_apply_action,
    ENDAXIS_PT_action_switcher,
)


def unregister_existing_classes():
    for name in ("ENDAXIS_PT_action_switcher", "ENDAXIS_OT_apply_action"):
        existing = getattr(bpy.types, name, None)
        if existing is not None:
            try:
                bpy.utils.unregister_class(existing)
            except RuntimeError:
                pass
    if hasattr(bpy.types.Scene, "endfield_action_name"):
        del bpy.types.Scene.endfield_action_name


def register():
    unregister_existing_classes()
    bpy.types.Scene.endfield_action_name = EnumProperty(
        name="动作",
        description="需要同步应用到模型所有部件的动作",
        items=action_items,
    )
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    unregister_existing_classes()


register()
