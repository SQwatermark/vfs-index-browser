"""Render the opened character blend from a fixed three-quarter review angle."""

from __future__ import annotations

from pathlib import Path

import bpy
from mathutils import Vector


camera = bpy.context.scene.camera
assert camera is not None, "The generated scene has no preview camera"

camera.location = (-1.15, -2.4, 1.55)
target = Vector((0.0, 0.0, 1.25))
camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.lens = 72

scene = bpy.context.scene
scene.render.resolution_x = 760
scene.render.resolution_y = 760
scene.render.resolution_percentage = 100
scene.render.filepath = str(
    Path(bpy.data.filepath).parent / "pelica-embedded-three-quarter.png"
)
bpy.ops.render.render(write_still=True)
