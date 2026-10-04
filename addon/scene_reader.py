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


# ---------------------------------------------------------------------------
# Minimal base context (sent with every request)
# ---------------------------------------------------------------------------

def get_basic_context() -> dict:
    """Return a rich yet compact dict with the scene essentials so the AI can answer immediately."""
    ctx = {}
    scene = bpy.context.scene
    obj = bpy.context.active_object

    # Environment & Version (guarantees accurate version-matched guidance)
    ctx["blender_version"] = bpy.app.version_string
    ctx["platform"] = "macOS" if platform.system() == "Darwin" else platform.system()

    # Active scene essentials
    ctx["active_object"] = obj.name if obj else None
    ctx["active_object_type"] = obj.type if obj else None
    ctx["mode"] = bpy.context.mode
    ctx["render_engine"] = scene.render.engine

    # Selection state
    selected = bpy.context.selected_objects
    ctx["selected_objects"] = [{"name": o.name, "type": o.type} for o in selected]
    ctx["selection_count"] = len(selected)

    # Active object details (modifiers, materials, transforms, mesh topology)
    if obj:
        obj_info = {
            "scale": [round(s, 4) for s in obj.scale],
            "scale_applied": all(abs(s - 1.0) < 0.001 for s in obj.scale),
            "visible": obj.visible_get(),
        }
        if hasattr(obj, "dimensions"):
            obj_info["dimensions"] = [round(d, 4) for d in obj.dimensions]

        # Existing modifiers on active object
        if hasattr(obj, "modifiers"):
            obj_info["modifiers"] = [
                {"name": m.name, "type": m.type, "show_viewport": m.show_viewport}
                for m in obj.modifiers
            ]

        # Materials on active object
        if hasattr(obj, "data") and hasattr(obj.data, "materials"):
            obj_info["materials"] = [m.name for m in obj.data.materials if m]

        # Mesh topology count
        if obj.type == 'MESH' and obj.data:
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

def _get_blender_version_string() -> str:
    """Return the Blender major.minor version string for doc URLs."""
    try:
        v = bpy.app.version
        return f"{v[0]}.{v[1]}"
    except Exception:
        return "latest"


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
