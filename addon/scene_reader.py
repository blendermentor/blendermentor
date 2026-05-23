# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Scene context reader (modular, tool-based)
#
# Provides a minimal base context sent with every AI request, plus
# individual tool functions the AI can call on-demand to fetch
# additional scene details only when needed.

import bpy
import json


# ---------------------------------------------------------------------------
# Minimal base context (sent with every request)
# ---------------------------------------------------------------------------

def get_basic_context() -> dict:
    """Return a lightweight dict with only the essentials the AI always needs."""
    ctx = {}
    scene = bpy.context.scene
    obj = bpy.context.active_object

    ctx["active_object"] = obj.name if obj else None
    ctx["active_object_type"] = obj.type if obj else None
    ctx["mode"] = bpy.context.mode

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

    # Find the active VIEW_3D space
    try:
        for area in bpy.context.screen.areas:
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

    result = {
        "name": obj.name,
        "type": obj.type,
        "location": list(obj.location),
        "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale),
        "visible": obj.visible_get(),
        "hide_viewport": obj.hide_viewport,
        "hide_render": obj.hide_render,
    }

    # Modifiers
    if hasattr(obj, "modifiers"):
        result["modifiers"] = [
            {"name": m.name, "type": m.type, "show_viewport": m.show_viewport,
             "show_render": m.show_render}
            for m in obj.modifiers
        ]

    # Materials
    if hasattr(obj, "data") and hasattr(obj.data, "materials"):
        result["materials"] = [m.name for m in obj.data.materials if m]

    # Constraints
    if hasattr(obj, "constraints"):
        result["constraints"] = [
            {"name": c.name, "type": c.type, "mute": c.mute}
            for c in obj.constraints
        ]

    # Mesh-specific details
    if obj.type == 'MESH' and obj.data:
        mesh = obj.data
        result["vertex_count"] = len(mesh.vertices)
        result["face_count"] = len(mesh.polygons)
        result["has_uv_map"] = len(mesh.uv_layers) > 0
        result["uv_maps"] = [uv.name for uv in mesh.uv_layers]
        result["has_vertex_groups"] = len(obj.vertex_groups) > 0
        result["vertex_groups"] = [vg.name for vg in obj.vertex_groups]
        result["has_shape_keys"] = obj.data.shape_keys is not None

    # Animation
    result["has_animation_data"] = obj.animation_data is not None
    if obj.animation_data and obj.animation_data.action:
        result["action_name"] = obj.animation_data.action.name

    return result


def get_selection_info() -> dict:
    """Return info about the current selection (all selected objects)."""
    selected = bpy.context.selected_objects
    active = bpy.context.active_object

    result = {
        "active_object": active.name if active else None,
        "selected_objects": [
            {"name": o.name, "type": o.type} for o in selected
        ],
        "selection_count": len(selected),
    }

    return result


def get_active_tool_info() -> dict:
    """Return the currently active tool in the workspace."""
    result = {
        "active_tool": None,
        "mode": bpy.context.mode,
    }

    try:
        tool = bpy.context.workspace.tools.from_space_view3d_mode(
            bpy.context.mode, create=False
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
    try:
        import mathutils
        # Provide bpy and mathutils in the globals for the eval
        # Only eval is used (not exec) which limits to single expressions
        result = eval(expression, {"bpy": bpy, "mathutils": mathutils}, {})
        return {"result": str(result)}
    except Exception as e:
        return {"error": str(e)}


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
    "get_selection_info": {
        "function": get_selection_info,
        "description": "Get the full list of currently selected objects and which one is active. Call this when the user asks about selection, parenting, joining objects, or any operation that depends on what is selected.",
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
    "evaluate_python_expression": {
        "function": evaluate_python_expression,
        "description": "Evaluate an arbitrary Python expression using the Blender Python API (bpy) to inspect scene data that is not covered by other tools. Ensure the expression is a single valid Python expression (not statements). Do not attempt to modify the scene.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The python expression to evaluate, e.g., 'bpy.context.scene.render.engine' or '[n.name for n in bpy.context.active_object.data.materials[0].node_tree.nodes]'.",
                }
            },
            "required": ["expression"]
        }
    }
}


def execute_tool(tool_name: str, arguments: dict) -> dict:
    """Execute a registered tool by name with the given arguments.

    Returns the tool result as a dict, or an error dict if the tool
    doesn't exist or raises an exception.
    """
    tool_entry = TOOL_REGISTRY.get(tool_name)
    if not tool_entry:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        return tool_entry["function"](**arguments)
    except Exception as e:
        return {"error": f"Tool '{tool_name}' failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Legacy compatibility (used by dev tools panel)
# ---------------------------------------------------------------------------

def get_scene_context() -> dict:
    """Legacy: Return a combined context dict (for dev tools inspector)."""
    ctx = get_basic_context()
    obj = bpy.context.active_object
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
