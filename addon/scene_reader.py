# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Scene context reader (modular, tool-based)
#
# Provides a minimal base context sent with every AI request, plus
# individual tool functions the AI can call on-demand to fetch
# additional scene details only when needed.

import bpy
import json
import urllib.request
import urllib.error
import ssl
import gzip
import re
import platform
import os
import mathutils
import threading
import queue
import time
import copy


# ---------------------------------------------------------------------------
# Authoritative context resolution helpers (thread-safe)
# ---------------------------------------------------------------------------

def _get_active_object(context=None):
    """Return the active object robustly across main and background worker threads."""
    ctx = context or bpy.context
    active = getattr(ctx, "active_object", None)
    if active is not None:
        return active
    scene = getattr(ctx, "scene", None) or (bpy.data.scenes[0] if len(bpy.data.scenes) > 0 else None)
    if scene and hasattr(scene, "view_layers") and len(scene.view_layers) > 0:
        vl = scene.view_layers[0]
        active = getattr(getattr(vl, "objects", None), "active", None)
        if active is not None:
            return active
    return None


def _get_selected_objects(context=None):
    """Return the list of selected objects robustly across main and background threads."""
    ctx = context or bpy.context
    selected = getattr(ctx, "selected_objects", None)
    if selected is not None:
        return list(selected)
    scene = getattr(ctx, "scene", None) or (bpy.data.scenes[0] if len(bpy.data.scenes) > 0 else None)
    if scene:
        return [o for o in getattr(scene, "objects", []) if getattr(o, "select_get", lambda: False)()]
    return []


def _resolve_interaction_mode(obj=None, context=None) -> str:
    """Return the canonical Blender interaction mode string (e.g. 'EDIT_MESH', 'OBJECT', 'SCULPT').

    Thread-safe: computes mode from the object data-block when called in background threads
    where bpy.context.mode is detached/defaults to 'OBJECT'.
    """
    if obj is None:
        obj = _get_active_object(context)
    if obj is None:
        return "OBJECT"

    raw_mode = getattr(obj, "mode", "OBJECT")
    if raw_mode == "EDIT":
        obj_type = getattr(obj, "type", "")
        if obj_type == "MESH":
            return "EDIT_MESH"
        elif obj_type == "CURVE":
            return "EDIT_CURVE"
        elif obj_type == "SURFACE":
            return "EDIT_SURFACE"
        elif obj_type == "FONT":
            return "EDIT_TEXT"
        elif obj_type == "ARMATURE":
            return "EDIT_ARMATURE"
        elif obj_type == "METABALL":
            return "EDIT_METABALL"
        elif obj_type == "LATTICE":
            return "EDIT_LATTICE"
        elif "GPENCIL" in obj_type or "GREASE" in obj_type:
            return "EDIT_GPENCIL"
        return "EDIT"
    elif raw_mode == "VERTEX_PAINT":
        return "PAINT_VERTEX"
    elif raw_mode == "WEIGHT_PAINT":
        return "PAINT_WEIGHT"
    elif raw_mode == "TEXTURE_PAINT":
        return "PAINT_TEXTURE"

    return raw_mode


# ---------------------------------------------------------------------------
# Minimal base context (sent with every request)
# ---------------------------------------------------------------------------

def get_basic_context() -> dict:
    """Return a rich yet compact dict with the scene essentials so the AI can answer immediately."""
    ctx = {}
    scene = bpy.context.scene
    obj = _get_active_object()

    # Environment & Version (guarantees accurate version-matched guidance)
    ctx["blender_version"] = bpy.app.version_string
    ctx["platform"] = "macOS" if platform.system() == "Darwin" else platform.system()

    # Active scene essentials
    ctx["active_object"] = obj.name if obj else None
    ctx["active_object_type"] = obj.type if obj else None
    ctx["mode"] = _resolve_interaction_mode(obj)
    ctx["render_engine"] = scene.render.engine

    # Selection state (with each object's individual mode)
    selected = _get_selected_objects()
    ctx["selected_objects"] = [
        {"name": o.name, "type": o.type, "mode": getattr(o, "mode", "OBJECT")}
        for o in selected
    ]
    ctx["selection_count"] = len(selected)

    # Active object details (modifiers, materials, transforms, mesh topology)
    if obj:
        obj_info = {
            "mode": getattr(obj, "mode", "OBJECT"),
            "scale": [round(s, 4) for s in obj.scale],
            "scale_applied": all(abs(s - 1.0) < 0.001 for s in obj.scale),
            "visible": obj.visible_get(),
        }
        if hasattr(obj, "dimensions"):
            obj_info["dimensions"] = [round(d, 4) for d in obj.dimensions]

        # Existing modifiers on active object (with key params for common types)
        if hasattr(obj, "modifiers"):
            mod_list = []
            for i, m in enumerate(obj.modifiers):
                mod_entry = {"index": i, "name": m.name, "type": m.type,
                             "show_viewport": m.show_viewport}
                try:
                    if m.type == 'MIRROR':
                        mod_entry["use_axis"] = list(m.use_axis)
                    elif m.type == 'SUBSURF':
                        mod_entry["levels"] = m.levels
                    elif m.type == 'BEVEL':
                        mod_entry["width"] = round(m.width, 6)
                        mod_entry["segments"] = m.segments
                    elif m.type == 'BOOLEAN':
                        mod_entry["operation"] = m.operation
                    elif m.type == 'ARRAY':
                        mod_entry["count"] = m.count
                except Exception:
                    pass
                mod_list.append(mod_entry)
            obj_info["modifiers"] = mod_list

        # Materials on active object
        if hasattr(obj, "data") and hasattr(obj.data, "materials"):
            obj_info["materials"] = [m.name for m in obj.data.materials if m]

        # Mesh topology count (fresh in Edit Mode)
        if obj.type == 'MESH' and obj.data:
            if getattr(obj, "mode", "") == 'EDIT':
                try:
                    obj.update_from_editmode()
                except Exception:
                    pass
            obj_info["vertex_count"] = len(obj.data.vertices)
            obj_info["face_count"] = len(obj.data.polygons)

        ctx["active_object_details"] = obj_info
    else:
        ctx["active_object_details"] = None

    # Viewport state & shading mode
    viewport_info = {
        "shading_type": "SOLID",
        "show_overlays": True,
        "show_gizmo": True,
    }
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                space = area.spaces.active
                if space and space.type == 'VIEW_3D':
                    viewport_info["shading_type"] = space.shading.type
                    viewport_info["show_overlays"] = space.overlay.show_overlays
                    viewport_info["show_gizmo"] = space.show_gizmo
                    break
    except Exception:
        pass
    ctx["viewport_state"] = viewport_info

    # Active tool in 3D Viewport
    try:
        tool = bpy.context.workspace.tools.from_space_view3d_mode(
            bpy.context.mode, create=False
        )
        ctx["active_tool"] = tool.idname if tool else None
    except Exception:
        ctx["active_tool"] = None

    # Collect all editor types currently visible on screen
    try:
        ctx["visible_editors"] = list(set(
            area.type for area in bpy.context.screen.areas
        ))
    except Exception:
        ctx["visible_editors"] = []

    ctx["objects"] = [o.name for o in scene.objects]
    ctx["frame_current"] = scene.frame_current

    return ctx


def get_basic_context_json() -> str:
    """Return the basic scene context as a formatted JSON string."""
    return json.dumps(get_basic_context(), indent=2)


# ---------------------------------------------------------------------------
# On-demand tool functions (called by AI via tool_use / functionCall)
# ---------------------------------------------------------------------------

def get_render_settings() -> dict:
    """Return render engine, device, resolution, and sample settings."""
    scene = bpy.context.scene
    render = scene.render
    result = {
        "render_engine": render.engine,
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
    }

    # Cycles-specific settings
    if render.engine == 'CYCLES':
        cycles = scene.cycles
        result["cycles_device"] = cycles.device  # 'CPU' or 'GPU'
        result["cycles_samples"] = cycles.samples
        result["cycles_preview_samples"] = cycles.preview_samples
        result["cycles_use_denoising"] = cycles.use_denoising

    # EEVEE-specific settings
    elif render.engine == 'BLENDER_EEVEE_NEXT':
        eevee = scene.eevee
        result["eevee_samples"] = getattr(eevee, 'taa_render_samples', None)
        result["eevee_use_bloom"] = getattr(eevee, 'use_bloom', None)
        result["eevee_use_ssr"] = getattr(eevee, 'use_ssr', None)

    return result


def get_viewport_state() -> dict:
    """Return the active 3D viewport's shading, overlays, and gizmo state."""
    result = {
        "shading_type": None,
        "shading_light": None,
        "shading_color_type": None,
        "show_overlays": None,
        "show_gizmo": None,
    }

    # Find the active VIEW_3D space (resolves robustly in background threads)
    try:
        screen = getattr(bpy.context, "screen", None)
        if not screen:
            wm = getattr(bpy.context, "window_manager", None) or (bpy.data.window_managers[0] if len(bpy.data.window_managers) > 0 else None)
            if wm and len(wm.windows) > 0:
                screen = wm.windows[0].screen
        if screen:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    space = area.spaces.active
                    if space and space.type == 'VIEW_3D':
                        shading = space.shading
                        result["shading_type"] = shading.type  # WIREFRAME, SOLID, MATERIAL, RENDERED
                        result["shading_light"] = shading.light  # STUDIO, MATCAP, FLAT
                        result["shading_color_type"] = shading.color_type  # MATERIAL, SINGLE, OBJECT, RANDOM, VERTEX, TEXTURE
                        result["show_overlays"] = space.overlay.show_overlays
                        result["show_gizmo"] = space.show_gizmo
                        break
    except Exception:
        pass

    return result


def get_object_details(object_name: str) -> dict:
    """Return detailed info about a specific object (modifiers, materials, etc.)."""
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return {"error": f"Object '{object_name}' not found in scene."}

    raw_mode = getattr(obj, "mode", "OBJECT")
    is_edit_mode = raw_mode == "EDIT" or (obj.type == 'MESH' and getattr(obj.data, "is_editmode", False))

    result = {
        "name": obj.name,
        "type": obj.type,
        "mode": raw_mode,
        "is_edit_mode": is_edit_mode,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "visible": obj.visible_get(),
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
    }

    # Modifiers (with key diagnostic parameters inline)
    if hasattr(obj, "modifiers"):
        mod_list = []
        for i, m in enumerate(obj.modifiers):
            mod_info = {
                "index": i,
                "name": m.name,
                "type": m.type,
                "show_viewport": m.show_viewport,
                "show_render": m.show_render,
                "show_in_editmode": getattr(m, "show_in_editmode", None),
            }
            # Add a few key parameters for common modifier types
            # (full details available via get_modifier_details tool)
            try:
                if m.type == 'MIRROR':
                    mod_info["use_axis"] = list(m.use_axis)
                    mod_info["use_clip"] = m.use_clip
                    mod_info["mirror_object"] = m.mirror_object.name if m.mirror_object else None
                elif m.type == 'SUBSURF':
                    mod_info["levels"] = m.levels
                    mod_info["render_levels"] = m.render_levels
                elif m.type == 'BEVEL':
                    mod_info["width"] = round(m.width, 6)
                    mod_info["segments"] = m.segments
                    mod_info["limit_method"] = m.limit_method
                elif m.type == 'BOOLEAN':
                    mod_info["operation"] = m.operation
                    mod_info["object"] = m.object.name if m.object else None
                    mod_info["solver"] = getattr(m, "solver", None)
                elif m.type == 'ARRAY':
                    mod_info["count"] = m.count
                    mod_info["use_relative_offset"] = m.use_relative_offset
                elif m.type == 'SOLIDIFY':
                    mod_info["thickness"] = round(m.thickness, 6)
                    mod_info["offset"] = round(m.offset, 6)
                elif m.type == 'SHRINKWRAP':
                    mod_info["target"] = m.target.name if m.target else None
                    mod_info["wrap_method"] = m.wrap_method
            except Exception:
                pass
            mod_list.append(mod_info)
        result["modifiers"] = mod_list

    # Materials
    if hasattr(obj, "data") and hasattr(obj.data, "materials"):
        result["materials"] = [m.name for m in obj.data.materials if m]

    # Constraints
    if hasattr(obj, "constraints"):
        result["constraints"] = [
            {"name": c.name, "type": c.type, "mute": c.mute}
            for c in obj.constraints
        ]

    # Mesh-specific details (flush in Edit Mode for consistent vertex counts)
    if obj.type == 'MESH' and obj.data:
        if raw_mode == 'EDIT':
            try:
                obj.update_from_editmode()
            except Exception:
                pass
        mesh = obj.data
        result["vertex_count"] = len(mesh.vertices)
        result["face_count"] = len(mesh.polygons)
        result["has_uv_map"] = len(mesh.uv_layers) > 0
        result["uv_maps"] = [uv.name for uv in mesh.uv_layers]
        result["has_vertex_groups"] = len(obj.vertex_groups) > 0
        result["vertex_groups"] = [vg.name for vg in obj.vertex_groups]
        result["has_shape_keys"] = obj.data.shape_keys is not None

    # Animation overview
    if obj.animation_data:
        anim = obj.animation_data
        anim_summary = {
            "has_animation_data": True,
            "action_name": anim.action.name if anim.action else None,
            "has_drivers": len(getattr(anim, "drivers", [])) > 0,
            "has_nla_tracks": len(getattr(anim, "nla_tracks", [])) > 0,
        }
        try:
            fcs = _get_object_fcurves(obj)
            if fcs:
                anim_summary["animated_properties"] = sorted(list(set(fc.data_path for fc in fcs)))
                interps = set()
                for fc in fcs:
                    for kp in fc.keyframe_points:
                        interps.add(kp.interpolation)
                anim_summary["interpolations_used"] = sorted(list(interps))
                anim_summary["total_fcurves"] = len(fcs)
        except Exception:
            pass
        result["animation"] = anim_summary
        result["has_animation_data"] = True
        result["action_name"] = anim_summary["action_name"]
    else:
        result["has_animation_data"] = False
        result["action_name"] = None
        result["animation"] = None

    return result


# ---------------------------------------------------------------------------
# Animation inspection (cross-version: Blender 4.0-4.3 legacy & 4.4+/5.x layered/slotted)
# ---------------------------------------------------------------------------

def _channel_name(data_path: str, array_index: int) -> str:
    """Generate a clean, readable channel name like 'Location X' or 'Rotation Y'."""
    axis_map = {0: 'X', 1: 'Y', 2: 'Z', 3: 'W'}
    if data_path in ('location', 'rotation_euler', 'scale', 'delta_location', 'delta_rotation_euler', 'delta_scale'):
        prefix = data_path.replace('_euler', '').capitalize()
        axis = axis_map.get(array_index, str(array_index))
        return f"{prefix} {axis}"
    return f"{data_path}[{array_index}]"


def _extract_fcurves_from_action(action, slot=None, data_path: str = None) -> list:
    """Extract F-curves from an action across Blender versions.
    
    Supports:
    - Blender 4.0 - 4.3 legacy actions (direct action.fcurves)
    - Blender 4.4+ / 5.0+ Slotted & Layered actions (action.layers -> strip.channelbags)
    """
    if not action:
        return []
    fcurves = []
    seen = set()

    def _add_fc(fc):
        key = (fc.data_path, fc.array_index)
        if key not in seen:
            if data_path is None or fc.data_path == data_path:
                fcurves.append(fc)
                seen.add(key)

    # 1. Legacy action (Blender 4.0-4.3, or legacy unslotted action in Blender 5.x)
    if hasattr(action, "fcurves") and len(action.fcurves) > 0:
        for fc in action.fcurves:
            _add_fc(fc)

    # 2. Slotted / Layered action (Blender 4.4+, 5.0+)
    if hasattr(action, "layers"):
        for layer in getattr(action, "layers", []):
            for strip in getattr(layer, "strips", []):
                # If slot is provided, try direct slot channelbag first
                if slot and hasattr(strip, "channelbag"):
                    try:
                        cb = strip.channelbag(slot)
                        if cb and hasattr(cb, "fcurves"):
                            for fc in cb.fcurves:
                                _add_fc(fc)
                    except Exception:
                        pass
                # Also inspect channelbags collection
                if hasattr(strip, "channelbags"):
                    for cb in strip.channelbags:
                        cb_slot = getattr(cb, "slot", None)
                        if slot is None or cb_slot == slot:
                            for fc in getattr(cb, "fcurves", []):
                                _add_fc(fc)
    return fcurves


def _get_object_fcurves(obj, data_path: str = None) -> list:
    """Retrieve all active F-curves for an object, checking active action and NLA tracks."""
    if not obj or not getattr(obj, "animation_data", None):
        return []
    anim = obj.animation_data
    slot = getattr(anim, "action_slot", None)

    # 1. Active action
    if anim.action:
        fcs = _extract_fcurves_from_action(anim.action, slot, data_path)
        if fcs:
            return fcs

    # 2. NLA tracks (if active action is None or use_nla is True)
    if getattr(anim, "use_nla", False) or (anim.action is None and hasattr(anim, "nla_tracks")):
        for track in getattr(anim, "nla_tracks", []):
            if track.mute:
                continue
            for strip in getattr(track, "strips", []):
                if strip.mute or not strip.action:
                    continue
                fcs = _extract_fcurves_from_action(strip.action, slot, data_path)
                if fcs:
                    return fcs

    return []


def get_animation_details(object_name: str, data_path: str = None) -> dict:
    """Return detailed animation data: F-curves, keyframe frames, values, interpolation, easing, handles, F-curve modifiers, and drivers.

    Supports both legacy actions (Blender 4.0-4.3) and layered/slotted actions (Blender 4.4+/5.x).
    """
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return {"error": f"Object '{object_name}' not found in scene."}

    if not obj.animation_data:
        return {
            "object_name": object_name,
            "has_animation_data": False,
            "diagnostic_summary": f"Object '{object_name}' has no animation data or keyframes assigned.",
        }

    anim = obj.animation_data
    action = anim.action
    action_name = action.name if action else None
    action_type = "layered" if (action and hasattr(action, "layers")) else ("legacy" if (action and hasattr(action, "fcurves")) else None)
    slot_name = getattr(anim.action_slot, "name_display", None) if getattr(anim, "action_slot", None) else None

    # Retrieve matching F-curves using cross-version extractor
    all_fcurves = _get_object_fcurves(obj, None)
    all_animated_paths = sorted(list(set(fc.data_path for fc in all_fcurves)))

    if data_path:
        target_fcurves = [fc for fc in all_fcurves if fc.data_path == data_path]
    else:
        target_fcurves = all_fcurves

    # Check if object has animation data but lacks the requested data_path
    if data_path and not target_fcurves:
        return {
            "object_name": object_name,
            "has_animation_data": True,
            "action_name": action_name,
            "requested_data_path": data_path,
            "error": f"No keyframes found for property '{data_path}' on object '{object_name}'.",
            "available_animated_properties": all_animated_paths,
            "diagnostic_summary": (
                f"Object '{object_name}' has an active action ('{action_name}'), but no keyframes exist for '{data_path}'. "
                f"Available animated properties are: {all_animated_paths}."
            )
        }

    fcurves_data = []
    observed_interpolations = set()
    stepped_channels = []
    linear_channels = []
    bezier_channels = []
    active_fc_modifiers = []

    for fc in target_fcurves:
        ch_name = _channel_name(fc.data_path, fc.array_index)

        # F-curve modifiers (e.g. Stepped, Cycles, Noise)
        fc_mods = []
        for m in getattr(fc, "modifiers", []):
            if m.mute:
                continue
            mod_item = {
                "type": m.type,
                "is_active": getattr(m, "is_active", True),
            }
            if m.type == 'STEPPED':
                mod_item["frame_step"] = getattr(m, "frame_step", None)
                mod_item["frame_offset"] = getattr(m, "frame_offset", None)
                active_fc_modifiers.append(f"{ch_name} has active Stepped modifier (step={mod_item['frame_step']})")
            elif m.type == 'CYCLES':
                mod_item["mode_before"] = getattr(m, "mode_before", None)
                mod_item["mode_after"] = getattr(m, "mode_after", None)
            elif m.type == 'NOISE':
                mod_item["scale"] = round(getattr(m, "scale", 1.0), 4)
                mod_item["strength"] = round(getattr(m, "strength", 1.0), 4)
            fc_mods.append(mod_item)

        kps = fc.keyframe_points
        kp_list = []
        for i, kp in enumerate(kps):
            if i >= 50:  # Cap at 50 keyframes per channel to keep token usage sane
                kp_list.append({"note": f"... [{len(kps) - 50} additional keyframes truncated]"})
                break

            frame_val = round(kp.co[0], 2) if abs(kp.co[0] - round(kp.co[0])) < 0.001 else round(kp.co[0], 4)
            interp = kp.interpolation
            observed_interpolations.add(interp)

            if interp == 'CONSTANT' and ch_name not in stepped_channels:
                stepped_channels.append(ch_name)
            elif interp == 'LINEAR' and ch_name not in linear_channels:
                linear_channels.append(ch_name)
            elif interp == 'BEZIER' and ch_name not in bezier_channels:
                bezier_channels.append(ch_name)

            kp_entry = {
                "frame": frame_val,
                "value": round(kp.co[1], 4),
                "interpolation": interp,
                "easing": kp.easing,
            }

            if interp == 'BEZIER':
                kp_entry["handle_left_type"] = kp.handle_left_type
                kp_entry["handle_right_type"] = kp.handle_right_type
                kp_entry["handle_left"] = [round(kp.handle_left[0], 2), round(kp.handle_left[1], 4)]
                kp_entry["handle_right"] = [round(kp.handle_right[0], 2), round(kp.handle_right[1], 4)]

            kp_list.append(kp_entry)

        fc_info = {
            "channel": ch_name,
            "data_path": fc.data_path,
            "array_index": fc.array_index,
            "mute": fc.mute,
            "lock": fc.lock,
            "keyframe_count": len(kps),
            "keyframes": kp_list,
        }
        if fc_mods:
            fc_info["fcurve_modifiers"] = fc_mods
        fcurves_data.append(fc_info)

    # Inspect drivers if any
    drivers_data = []
    if hasattr(anim, "drivers"):
        for d in anim.drivers:
            if data_path is None or d.data_path == data_path:
                drv_info = {
                    "channel": _channel_name(d.data_path, d.array_index),
                    "data_path": d.data_path,
                    "array_index": d.array_index,
                    "mute": d.mute,
                }
                if hasattr(d, "driver"):
                    drv_info["type"] = d.driver.type
                    drv_info["expression"] = d.driver.expression
                    drv_info["is_valid"] = d.driver.is_valid
                drivers_data.append(drv_info)

    # Synthesize evidence-based diagnostic summary
    summary_parts = []
    summary_parts.append(f"Action '{action_name or 'None'}' ({action_type or 'unslotted'}) on '{object_name}'.")

    if active_fc_modifiers:
        summary_parts.append(f"CRITICAL: Active F-curve modifier(s) detected: {'; '.join(active_fc_modifiers)}.")

    if 'CONSTANT' in observed_interpolations:
        summary_parts.append(
            f"OBSERVED: CONSTANT interpolation on channel(s) {stepped_channels}. "
            f"Constant interpolation holds each keyframe value static until the next frame, producing stepped / choppy jumps instead of continuous motion."
        )
    elif 'LINEAR' in observed_interpolations and 'BEZIER' not in observed_interpolations:
        summary_parts.append(
            f"OBSERVED: LINEAR interpolation on channel(s) {linear_channels}. "
            f"Motion travels at constant speed between keyframes with abrupt corner transitions and no smooth acceleration/deceleration (no easing)."
        )
    elif 'BEZIER' in observed_interpolations:
        summary_parts.append(
            f"OBSERVED: BEZIER interpolation on channel(s) {bezier_channels} with smooth curve transitions."
        )

    result = {
        "object_name": object_name,
        "has_animation_data": True,
        "action_name": action_name,
        "action_type": action_type,
        "action_slot": slot_name,
        "animated_properties": all_animated_paths,
        "fcurves_inspected": len(fcurves_data),
        "fcurves": fcurves_data,
        "diagnostic_summary": " ".join(summary_parts),
    }
    if drivers_data:
        result["drivers"] = drivers_data
    return result


# ---------------------------------------------------------------------------
# Modifier detail inspector (reads type-specific parameters via RNA)
# ---------------------------------------------------------------------------

def _serialize_modifier_value(value, depth: int = 0):
    """Convert a bpy property value to a JSON-serializable type."""
    if depth > 3:
        return str(value)
    if value is None:
        return None
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    # Object/data-block reference (has .name and is a bpy ID type)
    if hasattr(value, "name") and hasattr(value, "bl_rna"):
        return {"reference": value.name, "type": type(value).__name__}
    # bpy_prop_array, Vector, Color, Euler, Quaternion, list, tuple, etc.
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        try:
            items = []
            for i, v in enumerate(value):
                if i >= 50:  # Prevent huge array dumps
                    items.append("... [truncated]")
                    break
                items.append(_serialize_modifier_value(v, depth + 1))
            return items
        except (TypeError, ValueError, Exception):
            return str(value)
    return str(value)


def get_modifier_details(object_name: str, modifier_name: str) -> dict:
    """Return the type-specific configuration parameters of a modifier.

    Uses Blender's RNA introspection to dynamically read all parameters
    specific to the modifier type (e.g. Mirror axis, Bevel width,
    Subdivision levels) without hardcoding property names per type.
    """
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return {"error": f"Object '{object_name}' not found in scene."}

    mod = obj.modifiers.get(modifier_name)
    if not mod:
        available = [m.name for m in obj.modifiers]
        return {
            "error": f"Modifier '{modifier_name}' not found on '{object_name}'.",
            "available_modifiers": available,
        }

    # Basic modifier metadata
    mod_list = list(obj.modifiers)
    result = {
        "object_name": object_name,
        "modifier_name": mod.name,
        "modifier_type": mod.type,
        "modifier_index": mod_list.index(mod),
        "modifier_count": len(mod_list),
        "show_viewport": mod.show_viewport,
        "show_render": mod.show_render,
        "show_in_editmode": getattr(mod, "show_in_editmode", None),
        "show_on_cage": getattr(mod, "show_on_cage", None),
    }

    # Modifier stack context: list other modifiers and their order
    result["modifier_stack"] = [
        {"index": i, "name": m.name, "type": m.type}
        for i, m in enumerate(mod_list)
    ]

    # Collect base Modifier property identifiers to exclude
    base_prop_ids = set()
    try:
        for p in bpy.types.Modifier.bl_rna.properties:
            base_prop_ids.add(p.identifier)
    except Exception:
        base_prop_ids = {
            "name", "type", "show_viewport", "show_render",
            "show_in_editmode", "show_expanded", "is_active",
            "show_on_cage", "use_apply_on_spline", "is_override_data",
            "rna_type",
        }

    # Read type-specific parameters via RNA introspection
    parameters = {}
    for prop in mod.bl_rna.properties:
        pid = prop.identifier
        if pid in base_prop_ids:
            continue
        if pid.startswith("bl_") or pid == "rna_type":
            continue
        try:
            value = getattr(mod, pid)
            parameters[pid] = _serialize_modifier_value(value)
        except Exception:
            continue

    result["parameters"] = parameters
    return result


# ---------------------------------------------------------------------------
# Mesh geometry summary (lightweight diagnostic data)
# ---------------------------------------------------------------------------

def get_mesh_geometry_summary(object_name: str) -> dict:
    """Return a lightweight geometry summary for diagnostic reasoning.

    Includes the mesh bounding box and centroid in object-local space and world space,
    whether vertices touch each axis plane (critical for Mirror/Screw),
    custom normals status, loose geometry counts, and non-manifold edge
    count (capped at 100K vertices for performance).
    Flushes Edit Mode changes if the object is currently being edited.
    """
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return {"error": f"Object '{object_name}' not found in scene."}
    if obj.type != 'MESH':
        return {"error": f"Object '{object_name}' is type '{obj.type}', not a mesh."}
    if not obj.data:
        return {"error": f"Object '{object_name}' has no mesh data."}

    mesh = obj.data
    is_edit_mode = (getattr(obj, "mode", "") == 'EDIT' or getattr(mesh, "is_editmode", False))
    if is_edit_mode:
        try:
            obj.update_from_editmode()
        except Exception:
            pass

    verts = mesh.vertices
    n = len(verts)

    if n == 0:
        return {
            "object_name": object_name,
            "mode": _resolve_interaction_mode(obj),
            "vertex_count": 0,
            "error": "Mesh has no vertices.",
            "is_edit_mode": is_edit_mode,
        }

    # Bounding box in object-local space computed directly from vertex coordinates
    # (obj.bound_box is not updated by Blender while remaining in Edit Mode)
    bb_min_local = [min(v.co[i] for v in verts) for i in range(3)]
    bb_max_local = [max(v.co[i] for v in verts) for i in range(3)]

    # World-space bounding box computed by transforming the 8 local bbox corners by matrix_world
    corners_local = [
        mathutils.Vector((
            bb_min_local[0] if (i & 1) == 0 else bb_max_local[0],
            bb_min_local[1] if (i & 2) == 0 else bb_max_local[1],
            bb_min_local[2] if (i & 4) == 0 else bb_max_local[2]
        ))
        for i in range(8)
    ]
    corners_world = [obj.matrix_world @ c for c in corners_local]
    bb_min_world = [min(c[j] for c in corners_world) for j in range(3)]
    bb_max_world = [max(c[j] for c in corners_world) for j in range(3)]

    # Centroid (average vertex position in local space and world space)
    cx, cy, cz = 0.0, 0.0, 0.0
    for v in verts:
        cx += v.co.x
        cy += v.co.y
        cz += v.co.z
    centroid_local = [round(cx / n, 6), round(cy / n, 6), round(cz / n, 6)]
    centroid_world_vec = obj.matrix_world @ mathutils.Vector((cx / n, cy / n, cz / n))
    centroid_world = [round(centroid_world_vec[j], 6) for j in range(3)]

    # Check if any vertex sits on each axis plane in local space (within a small tolerance)
    # This is directly diagnostic for Mirror (vertices should touch the mirror plane)
    tol = 0.0001
    has_verts_on_x0 = any(abs(v.co.x) < tol for v in verts)
    has_verts_on_y0 = any(abs(v.co.y) < tol for v in verts)
    has_verts_on_z0 = any(abs(v.co.z) < tol for v in verts)

    result = {
        "object_name": object_name,
        "mode": _resolve_interaction_mode(obj),
        "is_edit_mode": is_edit_mode,
        "vertex_count": n,
        "face_count": len(mesh.polygons),
        "edge_count": len(mesh.edges),
        "bounding_box_min": [round(v, 6) for v in bb_min_local],
        "bounding_box_max": [round(v, 6) for v in bb_max_local],
        "bounding_box_local_min": [round(v, 6) for v in bb_min_local],
        "bounding_box_local_max": [round(v, 6) for v in bb_max_local],
        "bounding_box_world_min": [round(v, 6) for v in bb_min_world],
        "bounding_box_world_max": [round(v, 6) for v in bb_max_world],
        "dimensions_local": [round(bb_max_local[i] - bb_min_local[i], 6) for i in range(3)],
        "dimensions_world": [round(bb_max_world[i] - bb_min_world[i], 6) for i in range(3)],
        "centroid_local": centroid_local,
        "centroid_world": centroid_world,
        "has_vertices_on_x_plane": has_verts_on_x0,
        "has_vertices_on_y_plane": has_verts_on_y0,
        "has_vertices_on_z_plane": has_verts_on_z0,
        "object_location": [round(v, 6) for v in obj.location],
        "object_scale": [round(v, 6) for v in obj.scale],
        "scale_applied": all(abs(s - 1.0) < 0.001 for s in obj.scale),
    }

    # Custom normals (safe across Blender 4.0 / 4.1 / 4.2+)
    result["has_custom_normals"] = getattr(mesh, "has_custom_normals", False)

    # Loose vertices and edges (vertices/edges not connected to any face)
    loose_verts = 0
    loose_edges = 0
    if n <= 200000:  # skip for very high-poly meshes
        face_vert_set = set()
        for poly in mesh.polygons:
            face_vert_set.update(poly.vertices)
        loose_verts = n - len(face_vert_set)

        # Loose edges: edges where neither vertex belongs to any polygon face
        loose_edges = sum(
            1 for e in mesh.edges
            if e.vertices[0] not in face_vert_set and e.vertices[1] not in face_vert_set
        )
        result["loose_vertices"] = loose_verts
        result["loose_edges"] = loose_edges
    else:
        result["loose_vertices"] = "skipped (high-poly mesh)"
        result["loose_edges"] = "skipped (high-poly mesh)"

    # Non-manifold edge count (edges not shared by exactly 2 faces)
    if n <= 100000:
        try:
            import bmesh
            bm = bmesh.new()
            bm.from_mesh(mesh)
            non_manifold = sum(1 for e in bm.edges if not e.is_manifold)
            result["non_manifold_edges"] = non_manifold
            bm.free()
        except Exception:
            result["non_manifold_edges"] = "could not compute"
    else:
        result["non_manifold_edges"] = "skipped (high-poly mesh)"

    return result


def get_selection_info() -> dict:
    """Return info about the current selection (all selected objects).

    When in Edit Mode on a mesh object, also inspects sub-element selection:
    active mesh_select_mode (vertex/edge/face), selected and total counts
    for vertices, edges, and faces, and flags if no sub-elements are selected.
    Works robustly across main and background threads.
    """
    selected = _get_selected_objects()
    active = _get_active_object()

    mode_str = _resolve_interaction_mode(active)
    active_obj_mode = getattr(active, "mode", "OBJECT") if active else "OBJECT"
    is_edit_mode = active_obj_mode == 'EDIT'

    result = {
        "mode": mode_str,
        "active_object": active.name if active else None,
        "active_object_mode": active_obj_mode,
        "is_edit_mode": is_edit_mode,
        "selected_objects": [
            {"name": o.name, "type": o.type, "mode": getattr(o, "mode", "OBJECT")} for o in selected
        ],
        "selection_count": len(selected),
    }

    # Inspect sub-element selection when in Edit Mode on a mesh object
    if is_edit_mode and active and active.type == 'MESH' and active.data:
        try:
            active.update_from_editmode()
        except Exception:
            pass
        mesh = active.data

        ts = getattr(bpy.context, "tool_settings", None)
        if not ts:
            scene = getattr(bpy.context, "scene", None) or (bpy.data.scenes[0] if len(bpy.data.scenes) > 0 else None)
            ts = getattr(scene, "tool_settings", None)

        sel_mode_tuple = getattr(ts, "mesh_select_mode", (True, False, False)) if ts else (True, False, False)
        active_select_modes = []
        if len(sel_mode_tuple) > 0 and sel_mode_tuple[0]:
            active_select_modes.append("VERTEX")
        if len(sel_mode_tuple) > 1 and sel_mode_tuple[1]:
            active_select_modes.append("EDGE")
        if len(sel_mode_tuple) > 2 and sel_mode_tuple[2]:
            active_select_modes.append("FACE")

        vert_sel = getattr(mesh, "total_vert_sel", 0)
        edge_sel = getattr(mesh, "total_edge_sel", 0)
        face_sel = getattr(mesh, "total_face_sel", 0)
        has_sub_element_selection = (vert_sel > 0 or edge_sel > 0 or face_sel > 0)

        # Check if selection matches the currently active mesh select mode
        mode_has_selection = False
        if "VERTEX" in active_select_modes and vert_sel > 0:
            mode_has_selection = True
        if "EDGE" in active_select_modes and edge_sel > 0:
            mode_has_selection = True
        if "FACE" in active_select_modes and face_sel > 0:
            mode_has_selection = True

        sub_selection = {
            "mesh_select_mode": active_select_modes,
            "selected_vertices": vert_sel,
            "total_vertices": len(mesh.vertices),
            "selected_edges": edge_sel,
            "total_edges": len(mesh.edges),
            "selected_faces": face_sel,
            "total_faces": len(mesh.polygons),
            "has_sub_element_selection": has_sub_element_selection,
            "active_mode_has_selection": mode_has_selection,
        }
        result["sub_element_selection"] = sub_selection
        if not has_sub_element_selection:
            result["selection_warning"] = (
                f"Active object '{active.name}' is in Edit Mode, but NO mesh elements (vertices/edges/faces) are currently selected. "
                "Any modeling operation (extrude, bevel, inset, delete, assign material) requires selecting elements first (e.g. press 'A' to select all)."
            )
        elif not mode_has_selection:
            mode_str = "/".join(active_select_modes).lower()
            result["selection_warning"] = (
                f"Active object '{active.name}' is in {mode_str} select mode, but 0 {mode_str} elements are selected."
            )

    return result


def get_material_details(object_name: str, slot_index: int = None, material_name: str = None) -> dict:
    """Inspect material slots, face assignments, shader node trees, Material Output surface connections,
    Principled BSDF parameters, and missing image textures for an object.

    Returns concise structured diagnostic data.
    """
    obj = bpy.data.objects.get(object_name)
    if not obj:
        return {"error": f"Object '{object_name}' not found in scene."}

    # If in Edit Mode on a mesh, flush edits so polygon.material_index is fresh
    if getattr(obj, "mode", "") == 'EDIT' and obj.type == 'MESH' and obj.data:
        try:
            obj.update_from_editmode()
        except Exception:
            pass

    slots_info = []
    face_counts = {}
    detected_issues = []

    # Calculate face assignments per slot index if mesh
    if obj.type == 'MESH' and obj.data:
        for p in obj.data.polygons:
            face_counts[p.material_index] = face_counts.get(p.material_index, 0) + 1

    total_slots = len(obj.material_slots)

    for idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        faces_assigned = face_counts.get(idx, 0) if face_counts else 0
        slot_data = {
            "slot_index": idx,
            "slot_name": slot.name,
            "material_name": mat.name if mat else None,
            "link": slot.link,
            "assigned_faces": faces_assigned,
        }
        if mat is None:
            detected_issues.append(f"Material slot {idx} is empty (no material data-block assigned).")
        elif total_slots > 1 and faces_assigned == 0 and obj.type == 'MESH' and len(obj.data.polygons) > 0:
            detected_issues.append(f"Material slot {idx} ('{mat.name}') is assigned to 0 faces.")
        slots_info.append(slot_data)

    if total_slots == 0:
        detected_issues.append(f"Object '{object_name}' has no material slots.")

    # Determine materials to inspect in detail
    target_mats = []
    if slot_index is not None:
        if 0 <= slot_index < len(obj.material_slots):
            s_mat = obj.material_slots[slot_index].material
            if s_mat:
                target_mats.append(s_mat)
            else:
                detected_issues.append(f"Requested slot index {slot_index} has no material assigned.")
        else:
            return {
                "object_name": object_name,
                "error": f"Slot index {slot_index} out of range (object has {len(obj.material_slots)} slots)."
            }
    elif material_name:
        m = bpy.data.materials.get(material_name)
        if m:
            target_mats.append(m)
        else:
            return {
                "object_name": object_name,
                "error": f"Material '{material_name}' not found in blend file."
            }
    else:
        # Inspect unique materials assigned to slots (cap at 4 to conserve context)
        seen_names = set()
        for slot in obj.material_slots:
            if slot.material and slot.material.name not in seen_names:
                seen_names.add(slot.material.name)
                target_mats.append(slot.material)
                if len(target_mats) >= 4:
                    break

    materials_detail = []
    for mat in target_mats:
        mat_entry = {
            "material_name": mat.name,
            "has_nodes": mat.node_tree is not None,
        }

        if not mat.node_tree:
            mat_entry["note"] = "Material does not use nodes or node tree is missing."
            materials_detail.append(mat_entry)
            continue

        nodes = mat.node_tree.nodes

        # 1. Collect all node-to-node links in structured format
        node_links = []
        for lk in mat.node_tree.links:
            if not getattr(lk, "is_valid", True):
                continue
            node_links.append({
                "from_node": lk.from_node.name,
                "from_node_type": lk.from_node.type,
                "from_socket": lk.from_socket.name,
                "to_node": lk.to_node.name,
                "to_node_type": lk.to_node.type,
                "to_socket": lk.to_socket.name,
            })
        mat_entry["node_links"] = node_links

        # 2. Find Material Output node and verify Surface socket connection
        output_node = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and getattr(n, "is_active_output", True)), None)
        if not output_node:
            output_node = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)

        if not output_node:
            mat_entry["output_status"] = "Missing Material Output node"
            mat_entry["surface_connected"] = False
            detected_issues.append(f"Material '{mat.name}' is missing a Material Output node.")
        else:
            surf_sock = output_node.inputs.get("Surface")
            if not surf_sock or not surf_sock.is_linked:
                mat_entry["surface_connected"] = False
                detected_issues.append(
                    f"Material '{mat.name}' has NO shader connected to the 'Surface' socket of '{output_node.name}'. "
                    "The object will render pitch black or unshaded."
                )
            else:
                mat_entry["surface_connected"] = True
                link = surf_sock.links[0]
                mat_entry["surface_shader"] = {
                    "node_name": link.from_node.name,
                    "node_type": link.from_node.type,
                    "socket_name": link.from_socket.name,
                }

        # 3. Inspect Principled BSDF node if present
        principled = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        bsdf_info = None
        if principled:
            bsdf_info = {
                "node_name": principled.name,
                "node_type": principled.type,
            }

            key_socket_specs = [
                ("Base Color", "base_color"),
                ("Metallic", "metallic"),
                ("Roughness", "roughness"),
                ("Alpha", "alpha"),
                ("Normal", "normal"),
                ("Transmission Weight", "transmission"),
                ("Transmission", "transmission"),
                ("Emission Color", "emission_color"),
                ("Emission", "emission_color"),
                ("Emission Strength", "emission_strength"),
            ]

            inputs_info = {}
            for socket_name, key_id in key_socket_specs:
                if key_id in inputs_info:
                    continue
                sock = principled.inputs.get(socket_name)
                if not sock:
                    continue

                is_linked = sock.is_linked
                linked_from = None
                if is_linked and sock.links:
                    src_lk = sock.links[0]
                    linked_from = {
                        "node_name": src_lk.from_node.name,
                        "node_type": src_lk.from_node.type,
                        "socket_name": src_lk.from_socket.name,
                    }

                def_val = None
                if hasattr(sock, "default_value"):
                    dv = sock.default_value
                    if hasattr(dv, "__iter__"):
                        def_val = [round(float(c), 3) for c in dv[:4]]
                    elif isinstance(dv, (int, float)):
                        def_val = round(float(dv), 3)

                inputs_info[key_id] = {
                    "socket_name": socket_name,
                    "is_linked": is_linked,
                    "linked_from": linked_from,
                    "default_value": def_val,
                }

                # Populate backward-compatible keys in bsdf_info
                if is_linked and linked_from:
                    bsdf_info[key_id] = f"linked to {linked_from['node_name']}.{linked_from['socket_name']}"
                else:
                    bsdf_info[key_id] = def_val

            bsdf_info["inputs"] = inputs_info

            # Detect Alpha = 0 issue
            alpha_item = inputs_info.get("alpha")
            if alpha_item and not alpha_item["is_linked"] and alpha_item["default_value"] is not None and alpha_item["default_value"] < 0.001:
                detected_issues.append(f"Material '{mat.name}' Principled BSDF Alpha is 0.0 (object may appear transparent/invisible).")

            mat_entry["principled_bsdf"] = bsdf_info

        # 4. Check Image Texture nodes and socket connections
        img_nodes = [n for n in nodes if n.type == 'TEX_IMAGE']
        textures_info = []
        image_color_to_base_color = False

        if img_nodes:
            for img_node in img_nodes:
                img = img_node.image

                # Outgoing links from Color
                color_sock = img_node.outputs.get("Color")
                color_linked = bool(color_sock and color_sock.is_linked)
                color_destinations = []
                if color_linked and color_sock:
                    for lk in color_sock.links:
                        dest = {
                            "to_node": lk.to_node.name,
                            "to_node_type": lk.to_node.type,
                            "to_socket": lk.to_socket.name,
                        }
                        color_destinations.append(dest)
                        if principled and lk.to_node == principled and lk.to_socket.name == "Base Color":
                            image_color_to_base_color = True

                # Outgoing links from Alpha
                alpha_sock = img_node.outputs.get("Alpha")
                alpha_linked = bool(alpha_sock and alpha_sock.is_linked)
                alpha_destinations = []
                if alpha_linked and alpha_sock:
                    for lk in alpha_sock.links:
                        alpha_destinations.append({
                            "to_node": lk.to_node.name,
                            "to_node_type": lk.to_node.type,
                            "to_socket": lk.to_socket.name,
                        })

                tex_entry = {
                    "node_name": img_node.name,
                    "color_output_connected": color_linked,
                    "color_destinations": color_destinations,
                    "alpha_output_connected": alpha_linked,
                    "alpha_destinations": alpha_destinations,
                }

                if not img:
                    tex_entry["status"] = "No image assigned"
                    tex_entry["image_name"] = None
                    detected_issues.append(f"Image Texture node '{img_node.name}' in material '{mat.name}' has no image assigned.")
                else:
                    filepath = img.filepath
                    is_packed = getattr(img, "packed_file", None) is not None
                    abs_filepath = bpy.path.abspath(filepath) if filepath else ""
                    file_missing = (not is_packed) and bool(filepath) and not os.path.exists(abs_filepath)
                    has_data = getattr(img, "has_data", False)
                    img_size = list(img.size) if hasattr(img, "size") else None

                    tex_entry["image_name"] = img.name
                    tex_entry["filepath"] = filepath
                    tex_entry["is_packed"] = is_packed
                    tex_entry["file_missing"] = file_missing
                    tex_entry["has_data"] = has_data
                    tex_entry["image_size"] = img_size

                    if file_missing:
                        tex_entry["status"] = "FILE MISSING (renders pink/magenta)"
                        detected_issues.append(
                            f"Image Texture node '{img_node.name}' in material '{mat.name}' references missing file '{filepath}'. In Blender, missing textures display as solid magenta/pink."
                        )
                    elif not has_data:
                        tex_entry["status"] = "NO_DATA (image datablock has no pixel data loaded)"
                    else:
                        tex_entry["status"] = "OK"

                textures_info.append(tex_entry)

            mat_entry["image_textures"] = textures_info

        mat_entry["image_texture_connected_to_base_color"] = image_color_to_base_color

        # 5. Distinguish confirmed facts from conditional outcomes
        observations = []
        possible_issues = []

        if mat_entry.get("surface_connected"):
            surf_name = mat_entry.get("surface_shader", {}).get("node_name", "Shader")
            if principled and img_nodes:
                bc_val = bsdf_info.get("inputs", {}).get("base_color", {}).get("default_value", [0.8, 0.8, 0.8])
                first_tex_name = textures_info[0]["node_name"] if textures_info else "Image Texture"
                first_img_name = textures_info[0].get("image_name") or "unnamed"
                textures_without_data = [t for t in textures_info if not t.get("has_data") or t.get("file_missing")]

                if image_color_to_base_color:
                    observations.append(
                        f"Image Texture Color output is connected to Principled BSDF Base Color, and {surf_name} is connected to Material Output."
                    )
                    if textures_without_data:
                        t_nodata = textures_without_data[0]
                        possible_issues.append(
                            f"Image Texture '{t_nodata['node_name']}' is connected to Base Color, but image '{t_nodata.get('image_name')}' "
                            f"has no pixel data loaded ({t_nodata['status']}). A valid image must be assigned for texture to appear."
                        )
                else:
                    # Confirmed Fact (direct cause of current untextured render)
                    obs = (
                        f"Confirmed cause: {surf_name} is connected to Material Output, but Image Texture '{first_tex_name}' ({first_img_name}) "
                        f"Color output is NOT connected to Principled BSDF Base Color (Base Color is unlinked with default {bc_val}). "
                        f"This missing link explains why the object currently displays a flat default color instead of the image texture."
                    )
                    observations.append(obs)
                    possible_issues.append(
                        f"Image Texture '{first_tex_name}' exists in material '{mat.name}', but its Color output is disconnected from Base Color."
                    )

                    # Conditional Outcome (secondary finding if reconnected)
                    if textures_without_data:
                        t_nodata = textures_without_data[0]
                        sec_obs = (
                            f"Secondary finding (conditional): Referenced image '{t_nodata.get('image_name')}' currently has no pixel data loaded ({t_nodata['status']}). "
                            f"While connecting Color to Base Color is required, doing so alone may not display a meaningful texture until a valid image file is opened or generated."
                        )
                        observations.append(sec_obs)
                        possible_issues.append(
                            f"Image '{t_nodata.get('image_name')}' in '{t_nodata['node_name']}' has no pixel data loaded ({t_nodata['status']}). Once reconnected, a valid image must also be assigned."
                        )
            elif principled and not img_nodes:
                bc_val = bsdf_info.get("inputs", {}).get("base_color", {}).get("default_value", [0.8, 0.8, 0.8])
                observations.append(
                    f"Material '{mat.name}' uses uniform Base Color {bc_val} with no Image Texture nodes."
                )

        mat_entry["observations"] = observations
        mat_entry["possible_issues"] = possible_issues

        materials_detail.append(mat_entry)

    # Diagnostic synthesis
    all_possible = []
    all_observations = []
    for m in materials_detail:
        all_possible.extend(m.get("possible_issues", []))
        all_observations.extend(m.get("observations", []))

    if detected_issues:
        diag_summary = "Issues detected: " + "; ".join(detected_issues)
    elif all_possible:
        diag_summary = "; ".join(all_observations) + " Potential issue: " + "; ".join(all_possible)
    elif total_slots == 0:
        diag_summary = f"Object '{object_name}' has no material slots or materials assigned."
    else:
        diag_summary = f"Object '{object_name}' has {total_slots} material slot(s) with valid assignments and connected shaders."
        if all_observations:
            diag_summary += " " + "; ".join(all_observations)

    return {
        "object_name": object_name,
        "material_slots_count": total_slots,
        "material_slots": slots_info,
        "materials": materials_detail,
        "detected_issues": detected_issues,
        "possible_issues": all_possible,
        "observations": all_observations,
        "diagnostic_summary": diag_summary,
    }


def get_active_tool_info() -> dict:
    """Return the currently active tool in the workspace."""
    active = _get_active_object()
    mode_str = _resolve_interaction_mode(active)

    result = {
        "active_tool": None,
        "mode": mode_str,
    }

    try:
        workspace = getattr(bpy.context, "workspace", None)
        if not workspace:
            wm = getattr(bpy.context, "window_manager", None) or (bpy.data.window_managers[0] if len(bpy.data.window_managers) > 0 else None)
            if wm and len(wm.windows) > 0:
                workspace = wm.windows[0].workspace

        if workspace:
            tool = workspace.tools.from_space_view3d_mode(
                mode_str, create=False
            )
            if tool:
                result["active_tool"] = tool.idname
    except Exception:
        pass

    return result


def get_world_and_lighting() -> dict:
    """Return world/environment and light object info."""
    scene = bpy.context.scene
    result = {}

    # World settings
    world = scene.world
    if world:
        result["world_name"] = world.name
        result["world_has_nodes"] = world.use_nodes
    else:
        result["world_name"] = None

    # Lights in the scene
    lights = []
    for obj in scene.objects:
        if obj.type == 'LIGHT' and obj.data:
            light_info = {
                "name": obj.name,
                "light_type": obj.data.type,  # POINT, SUN, SPOT, AREA
                "energy": obj.data.energy,
                "color": list(obj.data.color),
            }
            lights.append(light_info)
    return {
        "world_name": world.name if world else None,
        "world_use_nodes": world.use_nodes if world else False,
        "lights": lights
    }


def evaluate_python_expression(expression: str) -> dict:
    """Evaluate a read-only bpy python expression. Use sparingly and carefully."""
    expr = (expression or "").strip()
    if not expr:
        return {"error": "Empty expression"}

    # Security: block operators (modifies scene state) and dangerous modules/attributes
    if "bpy.ops" in expr:
        return {
            "error": "Security restriction: 'bpy.ops' operators are not permitted in evaluate_python_expression. Only read-only properties (bpy.data or bpy.context) can be inspected."
        }
    if any(blocked in expr for blocked in ["__import__", "__builtins__", "__class__", "__base__", "__subclasses__", "importlib", "subprocess", "os.", "sys.", "shutil"]):
        return {"error": "Security restriction: Unsafe attribute or import detected in expression."}

    try:
        import mathutils
        safe_builtins = {
            "len": len, "range": range, "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
            "min": min, "max": max, "sum": sum, "enumerate": enumerate, "zip": zip,
            "getattr": getattr, "hasattr": hasattr, "all": all, "any": any,
            "round": round, "abs": abs, "isinstance": isinstance, "sorted": sorted,
        }
        result = eval(expr, {"__builtins__": safe_builtins, "bpy": bpy, "mathutils": mathutils}, {})
        return {"result": str(result)}
    except Exception as e:
        return {"error": str(e)}


def check_addon_status(addon_name: str) -> dict:
    """Check if a specific addon or extension is installed and enabled in Blender."""
    try:
        import addon_utils
        clean = addon_name.lower().replace(" ", "_").replace("-", "_")
        enabled_keys = list(bpy.context.preferences.addons.keys())
        is_enabled = any(clean in k.lower() for k in enabled_keys)

        installed = False
        matching_title = ""
        for mod in addon_utils.modules():
            mod_name = getattr(mod, "__name__", "")
            info = getattr(mod, "bl_info", {})
            title = info.get("name", "")
            if clean in mod_name.lower() or clean in title.lower():
                installed = True
                matching_title = title if title else mod_name
                break

        is_ext = bpy.app.version >= (4, 2)
        pref_tab = "Get Extensions" if is_ext else "Add-ons"
        menu_path = "Blender > Preferences" if platform.system() == "Darwin" else "Edit > Preferences"

        return {
            "addon_name": addon_name,
            "matching_name": matching_title if matching_title else addon_name,
            "is_enabled": is_enabled,
            "is_installed": installed or is_enabled,
            "blender_version": bpy.app.version_string,
            "preferences_path": f"{menu_path} > {pref_tab}",
            "how_to_enable": (
                "Already enabled." if is_enabled else
                f"Open {menu_path} > {pref_tab}, search for '{matching_title or addon_name}', and enable/install it."
            )
        }
    except Exception as e:
        return {"error": f"Failed to check addon status: {str(e)}"}



# ---------------------------------------------------------------------------
# Web-based tools (called by AI to access external knowledge)
# ---------------------------------------------------------------------------

# Module-level static version initialized on the main thread during import
try:
    _STATIC_BLENDER_DOC_VERSION = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
except Exception:
    _STATIC_BLENDER_DOC_VERSION = "latest"


def _get_blender_version_string() -> str:
    """Return the Blender major.minor version string for doc URLs (main-thread cached)."""
    return _STATIC_BLENDER_DOC_VERSION


def search_blender_community(query: str) -> dict:
    """Search Blender Stack Exchange for community Q&A."""
    try:
        import urllib.parse
        encoded_q = urllib.parse.quote_plus(query)
        url = (
            f"https://api.stackexchange.com/2.3/search/excerpts"
            f"?order=desc&sort=relevance&q={encoded_q}"
            f"&site=blender&pagesize=5&filter=default"
        )
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={
            "Accept-Encoding": "gzip",
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            # Stack Exchange always returns gzip-compressed responses
            raw_data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw_data = gzip.decompress(raw_data)
            data = json.loads(raw_data.decode("utf-8"))

        results = []
        for item in data.get("items", [])[:5]:
            # Strip HTML highlight tags from excerpts
            excerpt = item.get("excerpt", "")
            excerpt = re.sub(r'<[^>]+>', '', excerpt)
            excerpt = excerpt.replace("&hellip;", "...").replace("&#39;", "'")
            excerpt = excerpt.replace("&quot;", '"').replace("&amp;", "&")

            result = {
                "title": item.get("title", ""),
                "excerpt": excerpt,
                "score": item.get("question_score", 0),
                "answer_count": item.get("answer_count", 0),
                "has_accepted_answer": item.get("has_accepted_answer", False),
                "link": f"https://blender.stackexchange.com/questions/{item.get('question_id', '')}",
            }
            results.append(result)

        return {
            "query": query,
            "result_count": len(results),
            "results": results,
        }
    except Exception as e:
        return {"error": f"Stack Exchange search failed: {str(e)}"}


def fetch_blender_docs(page_path: str) -> dict:
    """Fetch a specific page from the official Blender manual.

    Constructs a version-specific URL based on the user's Blender version,
    falling back to /latest/ if the version-specific page returns 404.
    """
    try:
        ctx = ssl.create_default_context()
        version = _get_blender_version_string()

        # Try version-specific URL first, then fall back to /latest/
        urls_to_try = [
            f"https://docs.blender.org/manual/en/{version}/{page_path}",
        ]
        if version != "latest":
            urls_to_try.append(
                f"https://docs.blender.org/manual/en/latest/{page_path}"
            )

        html_content = None
        used_url = None
        for url in urls_to_try:
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "BlenderMentor-Addon/1.0",
                })
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    html_content = resp.read().decode("utf-8", errors="replace")
                    used_url = url
                    break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    continue
                raise

        if not html_content:
            return {"error": f"Page not found: {page_path}"}

        # Strip HTML tags and extract readable text
        # Remove script and style elements entirely
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        # Remove navigation, header, footer elements
        text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL)
        text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL)
        # Remove all remaining HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode common HTML entities
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Truncate to ~4000 chars to keep token usage reasonable
        if len(text) > 4000:
            text = text[:4000] + "... [truncated]"

        return {
            "url": used_url,
            "blender_version": version,
            "page_path": page_path,
            "content": text,
        }
    except Exception as e:
        return {"error": f"Documentation fetch failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool registry — maps tool names to their functions + schemas
# ---------------------------------------------------------------------------

TOOL_REGISTRY = {
    "get_render_settings": {
        "function": get_render_settings,
        "description": "Get the current render engine, device (CPU/GPU), resolution, and sample settings. Call this when the user asks about rendering, performance, output quality, or why renders look a certain way.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "get_viewport_state": {
        "function": get_viewport_state,
        "description": "Get the 3D viewport shading mode (wireframe, solid, material preview, rendered), overlay visibility, and gizmo state. Call this when the user asks about how things look in the viewport, why objects appear gray/untextured, or about display issues.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "get_object_details": {
        "function": get_object_details,
        "description": "Get detailed information about a specific object including its modifiers, materials, constraints, mesh data (vertex/face count, UV maps, vertex groups, shape keys), and animation data. Call this when the user asks about a specific object's properties or has issues with modifiers, materials, or mesh data.",
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "The exact name of the Blender object to inspect.",
                },
            },
            "required": ["object_name"],
        },
    },
    "get_modifier_details": {
        "function": get_modifier_details,
        "description": "Get the specific configuration parameters of a modifier on an object (e.g. Mirror axis/clip/merge, Bevel width/segments/limit, Subdivision levels, Boolean operation/solver, Array count/offset). Call this when diagnosing why a modifier is producing unexpected results, when the user asks about a modifier's settings, or when you need to verify a modifier's configuration before recommending changes. The modifier name is available from the base context or get_object_details.",
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "The exact name of the Blender object.",
                },
                "modifier_name": {
                    "type": "string",
                    "description": "The exact name of the modifier to inspect (as shown in the modifier list).",
                },
            },
            "required": ["object_name", "modifier_name"],
        },
    },
    "get_mesh_geometry_summary": {
        "function": get_mesh_geometry_summary,
        "description": "Get a lightweight geometry summary of a mesh object: local and world bounding boxes, local/world dimensions and centroid, whether vertices touch each axis plane (X=0, Y=0, Z=0), custom normals status, loose vertices/edges, and non-manifold edge count. Automatically flushes active Edit Mode edits. Call this when diagnosing geometry-origin problems (Mirror gaps, Screw axis issues, Spin center offset), scale issues, shading artifacts, mesh cleanup, or any issue where vertex positions relative to origin or world matter.",
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "The exact name of the Blender mesh object to inspect.",
                },
            },
            "required": ["object_name"],
        },
    },
    "get_animation_details": {
        "function": get_animation_details,
        "description": "Get detailed animation data for an object: active action, channel F-curves (location, rotation, scale, custom properties), keyframe positions, values, interpolation types ('CONSTANT', 'LINEAR', 'BEZIER', easing), handles, active F-curve modifiers (e.g. Stepped, Cycles, Noise), and drivers. Call this when diagnosing animation problems such as stepped/jumpy motion, unexpected speed, missing keyframes, interpolation issues, or driver evaluation problems. Optionally scope to a specific data_path like 'location' or 'rotation_euler'.",
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "The exact name of the Blender object to inspect.",
                },
                "data_path": {
                    "type": "string",
                    "description": "Optional: filter to a specific animated property (e.g., 'location', 'rotation_euler', 'scale'). Omit to inspect all animated channels on the object.",
                },
            },
            "required": ["object_name"],
        },
    },
    "get_material_details": {
        "function": get_material_details,
        "description": "Inspect material slots, face assignments, shader node trees, node-to-node links, Material Output surface connections, Principled BSDF inputs (Base Color, Metallic, Roughness, Normal, Alpha), and Image Texture status and socket connections for an object. Call this when diagnosing material or shading problems (texture not showing, unlinked image texture, wrong material on faces, empty material slot, disconnected shader output).",
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "The exact name of the Blender object to inspect.",
                },
                "slot_index": {
                    "type": "integer",
                    "description": "Optional: index of a specific material slot to inspect (0-indexed).",
                },
                "material_name": {
                    "type": "string",
                    "description": "Optional: name of a specific material to inspect.",
                },
            },
            "required": ["object_name"],
        },
    },
    "get_selection_info": {
        "function": get_selection_info,
        "description": "Get the list of currently selected objects, active object, and when in Edit Mode, the active selection mode (vertex/edge/face) and selected element counts (vertices, edges, faces). Call this when the user asks about selection, parenting, joining objects, or why modeling tools (extrude, bevel, inset, delete) are not working in Edit Mode.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "get_active_tool_info": {
        "function": get_active_tool_info,
        "description": "Get which tool is currently active in the 3D viewport toolbar (e.g. Select Box, Move, Extrude). Call this when the user is confused about interaction behavior or cannot select/move objects.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "get_world_and_lighting": {
        "function": get_world_and_lighting,
        "description": "Get world/environment settings and all light objects in the scene with their type, energy, and color. Call this when the user asks about lighting, environment, background, or why the scene is dark.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        },
    },
    "search_blender_community": {
        "function": search_blender_community,
        "description": "Search Blender Stack Exchange for community Q&A about Blender. Use when the user's question might have been answered by the community, or when you need practical tips, workarounds, or solutions to specific problems. Always combine results with the user's scene context — never return generic answers.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g., 'how to fix normals on imported FBX')",
                },
            },
            "required": ["query"],
        },
    },
    "fetch_blender_docs": {
        "function": fetch_blender_docs,
        "description": "Fetch a specific page from the official Blender manual (version-matched to the user's Blender installation). Use when you need exact documentation for a feature, setting, or workflow. The page path follows the manual's structure.",
        "parameters": {
            "type": "object",
            "properties": {
                "page_path": {
                    "type": "string",
                    "description": "Path within the manual (e.g., 'modeling/modifiers/generate/subdivision_surface.html')",
                },
            },
            "required": ["page_path"],
        },
    },
    "evaluate_python_expression": {
        "function": evaluate_python_expression,
        "description": "Evaluate a read-only Python expression using the Blender Python API (bpy) to inspect scene data not covered by other tools. Use this to inspect modifier-specific settings, node tree connections, constraint targets, particle system parameters, armature bone data, driver expressions, or any bpy property. Must be a single expression (not statements). Must NOT modify the scene — only read properties. Examples: 'bpy.data.objects[\"Cube\"].modifiers[\"Bevel\"].angle_limit', '[n.name for n in bpy.data.materials[\"Material\"].node_tree.nodes]', 'bpy.context.scene.tool_settings.transform_pivot_point'.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A single Python expression to evaluate, e.g., 'bpy.context.scene.render.engine' or 'bpy.data.objects[\"Cube\"].modifiers[\"Mirror\"].merge_threshold'.",
                }
            },
            "required": ["expression"]
        }
    },
    "check_addon_status": {
        "function": check_addon_status,
        "description": "Check if an addon or extension (e.g. 'cell_fracture', 'node_wrangler', 'bool_tool') is installed or enabled in Blender preferences, and get exact version-matched enablement instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "addon_name": {
                    "type": "string",
                    "description": "The name or keyword of the addon/extension to check (e.g., 'cell_fracture', 'node_wrangler', 'bool_tool').",
                }
            },
            "required": ["addon_name"],
        },
    }
}


def _make_cache_key(tool_name: str, arguments: dict) -> tuple:
    """Create a deterministic hashable cache key from a tool name and arguments dictionary."""
    def _freeze(v):
        if isinstance(v, dict):
            return tuple(sorted((k2, _freeze(v2)) for k2, v2 in v.items()))
        elif isinstance(v, (list, tuple)):
            return tuple(_freeze(item) for item in v)
        return v
    sorted_args = tuple(sorted((k, _freeze(v)) for k, v in (arguments or {}).items()))
    return (tool_name, sorted_args)


def capture_scene_snapshot(turn_id: str = None) -> dict:
    """Capture a lightweight plain-Python snapshot of the scene on the main thread.

    Evaluated strictly on Blender's main thread. The returned snapshot contains only
    plain-Python primitives (dicts, lists, numbers, strings, bools, None) and NO
    live bpy objects or C references.
    """
    t0 = time.perf_counter()
    active_obj = _get_active_object()
    active_name = active_obj.name if active_obj else None

    basic_ctx = get_basic_context()

    cache = {}
    # Pre-cache scene-level tools
    try:
        cache[_make_cache_key("get_selection_info", {})] = get_selection_info()
    except Exception as e:
        cache[_make_cache_key("get_selection_info", {})] = {"error": str(e)}

    try:
        cache[_make_cache_key("get_viewport_state", {})] = get_viewport_state()
    except Exception as e:
        cache[_make_cache_key("get_viewport_state", {})] = {"error": str(e)}

    try:
        cache[_make_cache_key("get_active_tool_info", {})] = get_active_tool_info()
    except Exception as e:
        cache[_make_cache_key("get_active_tool_info", {})] = {"error": str(e)}

    try:
        cache[_make_cache_key("get_render_settings", {})] = get_render_settings()
    except Exception as e:
        cache[_make_cache_key("get_render_settings", {})] = {"error": str(e)}

    try:
        cache[_make_cache_key("get_world_and_lighting", {})] = get_world_and_lighting()
    except Exception as e:
        cache[_make_cache_key("get_world_and_lighting", {})] = {"error": str(e)}

    # Pre-cache active object tools if an active object exists
    if active_name:
        try:
            cache[_make_cache_key("get_mesh_geometry_summary", {"object_name": active_name})] = get_mesh_geometry_summary(active_name)
        except Exception as e:
            cache[_make_cache_key("get_mesh_geometry_summary", {"object_name": active_name})] = {"error": str(e)}

        try:
            cache[_make_cache_key("get_object_details", {"object_name": active_name})] = get_object_details(active_name)
        except Exception as e:
            cache[_make_cache_key("get_object_details", {"object_name": active_name})] = {"error": str(e)}

        try:
            cache[_make_cache_key("get_material_details", {"object_name": active_name})] = get_material_details(active_name)
        except Exception as e:
            cache[_make_cache_key("get_material_details", {"object_name": active_name})] = {"error": str(e)}

    # Sanitize everything to pure JSON-safe primitives
    sanitized_cache = {k: json.loads(json.dumps(v)) for k, v in cache.items()}
    sanitized_basic = json.loads(json.dumps(basic_ctx))

    t1 = time.perf_counter()
    capture_time_ms = round((t1 - t0) * 1000, 2)

    return {
        "turn_id": turn_id or "default",
        "timestamp": time.time(),
        "capture_time_ms": capture_time_ms,
        "basic_context": sanitized_basic,
        "active_object": active_name,
        "cache": sanitized_cache,
    }


class InspectionDispatcher:
    """Thread-safe queue and coordinator for main-thread scene inspection.

    Enables background worker threads to request inspection tools without
    ever calling the Blender API directly. The requests are processed on the
    main thread via a timer callback, converted to plain Python primitives,
    and returned to the waiting worker thread.
    """
    def __init__(self):
        self._queue = queue.Queue()
        self._active_turn_id = None
        self._active_snapshot = None
        self._lock = threading.Lock()
        self._is_cancelled = False

    def set_active_turn(self, turn_id: str, snapshot: dict):
        with self._lock:
            self._active_turn_id = turn_id
            self._active_snapshot = snapshot
            self._is_cancelled = False
            # Drain any leftover requests from previous turns
            while not self._queue.empty():
                try:
                    req = self._queue.get_nowait()
                    req["result"] = {"error": "Superceded by newer turn."}
                    req["event"].set()
                except queue.Empty:
                    break

    def clear_active_turn(self):
        with self._lock:
            self._is_cancelled = True
            self._active_turn_id = None
            self._active_snapshot = None
            while not self._queue.empty():
                try:
                    req = self._queue.get_nowait()
                    req["result"] = {"error": "Turn ended or cancelled."}
                    req["event"].set()
                except queue.Empty:
                    break

    def get_active_snapshot(self) -> dict:
        with self._lock:
            return self._active_snapshot

    def request_main_thread_inspection(self, tool_name: str, arguments: dict, timeout: float = 5.0) -> dict:
        """Called from worker thread to request safe tool execution on the main thread."""
        # If called on main thread directly, execute immediately
        if threading.current_thread() is threading.main_thread():
            return execute_tool_direct(tool_name, arguments)

        with self._lock:
            if self._is_cancelled:
                return {"error": "Inspection cancelled."}
            turn_id = self._active_turn_id

        req = {
            "turn_id": turn_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "event": threading.Event(),
            "result": None,
            "timestamp": time.time(),
        }
        self._queue.put(req)

        # Wait for main thread to process
        signaled = req["event"].wait(timeout=timeout)
        if not signaled:
            with self._lock:
                req["timed_out"] = True
            return {
                "error": f"Tool '{tool_name}' inspection timed out on main thread after {timeout}s.",
                "tool_name": tool_name,
            }
        return req["result"]

    def process_pending_requests(self) -> int:
        """Called periodically on Blender's MAIN THREAD (e.g. by timer)."""
        if threading.current_thread() is not threading.main_thread():
            return 0

        processed = 0
        while not self._queue.empty():
            try:
                req = self._queue.get_nowait()
            except queue.Empty:
                break

            with self._lock:
                is_cancelled = self._is_cancelled or (req["turn_id"] != self._active_turn_id) or req.get("timed_out", False)
                snapshot = self._active_snapshot

            if is_cancelled:
                req["result"] = {"error": "Request cancelled, expired, or timed out."}
                req["event"].set()
                continue

            tool_name = req["tool_name"]
            tool_args = req["arguments"]

            try:
                # Execute safely on the main thread
                raw_res = execute_tool_direct(tool_name, tool_args)
                # Sanitize to plain Python primitives
                clean_res = json.loads(json.dumps(raw_res))
                # Cache in snapshot if available
                if snapshot is not None and "error" not in clean_res:
                    cache_key = _make_cache_key(tool_name, tool_args)
                    snapshot.setdefault("cache", {})[cache_key] = copy.deepcopy(clean_res)
                req["result"] = clean_res
            except Exception as e:
                req["result"] = {"error": f"Execution on main thread failed: {str(e)}"}
            finally:
                req["event"].set()
                processed += 1

        return processed


_global_inspector = InspectionDispatcher()


def execute_tool_direct(tool_name: str, arguments: dict) -> dict:
    """Execute a registered tool directly by name on the current thread."""
    tool_entry = TOOL_REGISTRY.get(tool_name)
    if not tool_entry:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        return tool_entry["function"](**arguments)
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {str(e)}"}


def execute_tool(tool_name: str, arguments: dict, snapshot: dict = None) -> dict:
    """Execute a registered tool safely across threads with caching and main-thread dispatch.

    1. Returns immediately if result is cached in snapshot.
    2. Web tools execute pure network I/O in current thread.
    3. Main-thread calls execute directly.
    4. Worker-thread calls dispatch to main thread via _global_inspector and return plain Python.
    """
    # Pure web tools (no Blender data needed)
    if tool_name in ("search_blender_community", "fetch_blender_docs"):
        return execute_tool_direct(tool_name, arguments)

    cache_key = _make_cache_key(tool_name, arguments)
    active_snap = snapshot or _global_inspector.get_active_snapshot()

    # Check if already in snapshot cache
    if active_snap and "cache" in active_snap:
        if cache_key in active_snap["cache"]:
            return copy.deepcopy(active_snap["cache"][cache_key])

    # If running on the main thread, execute directly and cache
    if threading.current_thread() is threading.main_thread():
        res = execute_tool_direct(tool_name, arguments)
        clean_res = json.loads(json.dumps(res))
        if active_snap and "cache" in active_snap and "error" not in clean_res:
            active_snap["cache"][cache_key] = copy.deepcopy(clean_res)
        return clean_res

    # Running in a worker thread and not cached: dispatch safely to main thread
    res = _global_inspector.request_main_thread_inspection(tool_name, arguments)
    if active_snap and "cache" in active_snap and "error" not in res:
        active_snap["cache"][cache_key] = copy.deepcopy(res)
    return res


# ---------------------------------------------------------------------------
# Legacy compatibility (used by dev tools panel)
# ---------------------------------------------------------------------------

def get_scene_context() -> dict:
    """Legacy: Return a combined context dict (for dev tools inspector)."""
    ctx = get_basic_context()
    obj = _get_active_object()
    ctx["modifiers_on_active"] = (
        [m.type for m in obj.modifiers] if obj and hasattr(obj, "modifiers") else []
    )
    ctx["materials_on_active"] = (
        [m.name for m in obj.data.materials if m]
        if obj and hasattr(obj, "data") and hasattr(obj.data, "materials")
        else []
    )
    ctx["animation_data_exists"] = obj.animation_data is not None if obj else False
    return ctx


def get_scene_context_json() -> str:
    """Legacy: Return the full scene context as a formatted JSON string."""
    return json.dumps(get_scene_context(), indent=2)
