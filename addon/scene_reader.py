# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Scene context reader

import bpy
import json


def get_scene_context() -> dict:
    """Return a dict describing the current Blender scene state."""
    ctx = {}
    scene = bpy.context.scene
    obj = bpy.context.active_object

    ctx["active_object"] = obj.name if obj else None
    ctx["active_object_type"] = obj.type if obj else None
    ctx["mode"] = bpy.context.mode
    ctx["editor"] = (
        bpy.context.area.type if bpy.context.area else "UNKNOWN"
    )
    ctx["objects"] = [o.name for o in scene.objects]
    ctx["modifiers_on_active"] = (
        [m.type for m in obj.modifiers] if obj and hasattr(obj, "modifiers") else []
    )
    ctx["materials_on_active"] = (
        [m.name for m in obj.data.materials if m]
        if obj and hasattr(obj, "data") and hasattr(obj.data, "materials")
        else []
    )
    ctx["frame_current"] = scene.frame_current
    ctx["animation_data_exists"] = obj.animation_data is not None if obj else False

    # Collect all editor types currently visible on screen
    try:
        ctx["visible_editors"] = list(set(
            area.type for area in bpy.context.screen.areas
        ))
    except Exception:
        ctx["visible_editors"] = []

    return ctx


def get_scene_context_json() -> str:
    """Return the scene context as a formatted JSON string."""
    return json.dumps(get_scene_context(), indent=2)
