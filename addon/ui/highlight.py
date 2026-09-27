# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — UI Highlighting (4 levels: area, tab, region, panel)

import bpy
import gpu
import time
import json
import math
from gpu_extras.batch import batch_for_shader

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_highlight_handlers = []      # list of (space_cls_name, handler_ref, region_type)
_highlight_start_time = 0.0
HIGHLIGHT_DURATION = 2.0      # seconds
_redraw_timer = None          # bpy.app.timers handle

# Panel monkey-patch state
_active_panel_id = None
_panel_prepend_fn = None

# (Properties tab coordinate lookup removed — now uses dynamic region highlight)

# Known Blender space type → Python class name
SPACE_CLASS_MAP = {
    'VIEW_3D':           'SpaceView3D',
    'PROPERTIES':        'SpaceProperties',
    'OUTLINER':          'SpaceOutliner',
    'NODE_EDITOR':       'SpaceNodeEditor',
    'DOPESHEET_EDITOR':  'SpaceDopeSheetEditor',
    'GRAPH_EDITOR':      'SpaceGraphEditor',
    'IMAGE_EDITOR':      'SpaceImageEditor',
    'SEQUENCE_EDITOR':   'SpaceSequenceEditor',
    'TEXT_EDITOR':       'SpaceTextEditor',
    'TIMELINE':          'SpaceDopeSheetEditor',
}

# Region within an editor → Blender region type string
REGION_TARGET_MAP = {
    "header":       "HEADER",
    "toolbar":      "TOOLS",
    "tool_header":  "TOOL_HEADER",
    "sidebar":      "UI",
    "n_panel":      "UI",
}

# Injectable class map — panels, headers, and menus that support prepend/append
# The key is the target name used in AI highlight responses.
INJECTABLE_CLASS_MAP = {
    # --- Panels (Properties editor) ---
    "modifier_add_button":    "DATA_PT_modifiers",
    "material_slots":         "MATERIAL_PT_context_material",
    "render_settings":        "RENDER_PT_render",
    "output_settings":        "OUTPUT_PT_output",
    "transform_panel":        "OBJECT_PT_transform",

    # --- Headers ---
    "view3d_header":          "VIEW3D_HT_header",
    "properties_header":      "PROPERTIES_HT_header",
    "outliner_header":        "OUTLINER_HT_header",

    # --- Menus (3D Viewport) ---
    "add_menu":               "VIEW3D_MT_add",
    "mesh_add_menu":          "VIEW3D_MT_mesh_add",
    "curve_add_menu":         "VIEW3D_MT_curve_add",
    "surface_add_menu":       "VIEW3D_MT_surface_add",
    "light_add_menu":         "VIEW3D_MT_light_add",
    "object_menu":            "VIEW3D_MT_object",
    "select_menu":            "VIEW3D_MT_select_object",
    "view_menu":              "VIEW3D_MT_view",

    # --- Menus (Node/Shader Editor) ---
    "shader_add_menu":        "NODE_MT_add",
}


# ---------------------------------------------------------------------------
# Redraw timer — keeps the pulsing animation alive
# ---------------------------------------------------------------------------

def _redraw_timer_tick():
    """Called by bpy.app.timers every 0.05s to force redraws during highlight."""
    global _redraw_timer

    elapsed = time.time() - _highlight_start_time
    if elapsed > HIGHLIGHT_DURATION:
        # Time's up — clean up handlers and stop the timer
        clear_all_highlights()
        return None  # returning None unregisters the timer

    # Tag all areas for redraw so the pulsing animation stays smooth
    _tag_redraw()
    return 0.05  # call again in 50ms


def _start_redraw_timer():
    """Start the periodic redraw timer if not already running."""
    global _redraw_timer
    if _redraw_timer is None:
        _redraw_timer = True
        bpy.app.timers.register(_redraw_timer_tick, first_interval=0.05)


def _stop_redraw_timer():
    """Stop the redraw timer."""
    global _redraw_timer
    if _redraw_timer is not None:
        try:
            if bpy.app.timers.is_registered(_redraw_timer_tick):
                bpy.app.timers.unregister(_redraw_timer_tick)
        except Exception:
            pass
        _redraw_timer = None


# ---------------------------------------------------------------------------
# GPU draw callback — pulsing amber overlay
# ---------------------------------------------------------------------------

def _draw_highlight_overlay():
    """Factory that returns a draw handler callback — clean amber glow, no text."""

    def _draw():
        global _highlight_start_time

        elapsed = time.time() - _highlight_start_time
        if elapsed > HIGHLIGHT_DURATION:
            return  # will be cleaned up by the timer

        # Pulsing alpha: sin wave → 0..1 range, mapped to 0..0.55
        pulse = (math.sin(elapsed * 6.0) + 1.0) / 2.0
        alpha = pulse * 0.55

        # Use bpy.context to get region info
        region = bpy.context.region
        if not region:
            return
        w = region.width
        h = region.height

        # Draw filled quad
        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        verts = ((0, 0), (w, 0), (w, h), (0, h))
        batch = batch_for_shader(shader, 'TRIS', {"pos": verts},
                                 indices=((0, 1, 2), (0, 2, 3)))
        shader.bind()
        shader.uniform_float("color", (1.0, 0.65, 0.0, alpha * 0.3))
        batch.draw(shader)

        # Draw border
        border = 3
        border_verts = (
            (0, 0), (w, 0), (w, h), (0, h),
            (border, border), (w - border, border),
            (w - border, h - border), (border, h - border),
        )
        border_indices = (
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
        )
        batch_b = batch_for_shader(shader, 'TRIS', {"pos": border_verts},
                                   indices=border_indices)
        shader.uniform_float("color", (1.0, 0.65, 0.0, alpha))
        batch_b.draw(shader)

        gpu.state.blend_set('NONE')

    return _draw


# (Level 2 pixel-math tab highlight removed — now uses NAVIGATION_BAR region overlay)


# ---------------------------------------------------------------------------
# Level 3 — Panel / Header / Menu prepend injection
# ---------------------------------------------------------------------------

def _panel_highlight_callback(self, context):
    """Injected into a panel/menu draw — subtle alert separator, no text."""
    global _active_panel_id
    if _active_panel_id:
        row = self.layout.row()
        row.alert = True
        row.separator()


def _inject_panel_highlight(panel_id: str):
    """Monkey-patch a Blender panel, header, or menu class to show the highlight indicator."""
    global _active_panel_id, _panel_prepend_fn

    _clear_panel_highlight()

    class_name = INJECTABLE_CLASS_MAP.get(panel_id)
    if not class_name:
        return

    target_cls = getattr(bpy.types, class_name, None)
    if not target_cls:
        return

    _active_panel_id = panel_id
    _panel_prepend_fn = _panel_highlight_callback
    target_cls.prepend(_panel_prepend_fn)


def _clear_panel_highlight():
    """Remove any active panel / header / menu monkey-patch."""
    global _active_panel_id, _panel_prepend_fn

    if _active_panel_id and _panel_prepend_fn:
        class_name = INJECTABLE_CLASS_MAP.get(_active_panel_id)
        if class_name:
            target_cls = getattr(bpy.types, class_name, None)
            if target_cls:
                try:
                    target_cls.remove(_panel_prepend_fn)
                except Exception:
                    pass

    _active_panel_id = None
    _panel_prepend_fn = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def trigger_highlight(highlight_data, context=None):
    """
    Activate a highlight based on the structured highlight dict.

    highlight_data: { "level": str, "space": str, "target": str } or None
    """
    clear_all_highlights()

    if not highlight_data:
        return

    global _highlight_start_time, _highlight_handlers
    _highlight_start_time = time.time()

    level = highlight_data.get("level", "area")
    space = highlight_data.get("space", "VIEW_3D")
    target = highlight_data.get("target", "")

    if level == "tab":
        # Level 2 — highlight the entire NAVIGATION_BAR region (no hardcoded pixel math)
        space_cls = getattr(bpy.types, "SpaceProperties", None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_highlight_overlay(), (),
                'NAVIGATION_BAR', 'POST_PIXEL'
            )
            _highlight_handlers.append(("SpaceProperties", handler, 'NAVIGATION_BAR'))

    elif level == "region":
        # Highlight a specific region within an editor (header, toolbar, sidebar)
        region_type = REGION_TARGET_MAP.get(target.lower(), "HEADER")
        space_cls_name = SPACE_CLASS_MAP.get(space, "SpaceView3D")
        space_cls = getattr(bpy.types, space_cls_name, None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_highlight_overlay(),
                (), region_type, 'POST_PIXEL'
            )
            _highlight_handlers.append((space_cls_name, handler, region_type))

    elif level == "panel":
        # Panel / header / menu monkey-patch injection
        _inject_panel_highlight(target)

        # Smart companion overlay — varies by target type
        class_name = INJECTABLE_CLASS_MAP.get(target, "")
        space_cls_name = SPACE_CLASS_MAP.get(space, "SpaceProperties")
        space_cls = getattr(bpy.types, space_cls_name, None)

        if space_cls and class_name:
            if "_MT_" in class_name:
                # Menu target → highlight just the HEADER region
                handler = space_cls.draw_handler_add(
                    _draw_highlight_overlay(),
                    (), 'HEADER', 'POST_PIXEL'
                )
                _highlight_handlers.append((space_cls_name, handler, 'HEADER'))
            elif "_HT_" in class_name:
                # Header target → no companion overlay needed
                pass
            else:
                # Panel target (_PT_) → glow the editor window
                handler = space_cls.draw_handler_add(
                    _draw_highlight_overlay(),
                    (), 'WINDOW', 'POST_PIXEL'
                )
                _highlight_handlers.append((space_cls_name, handler, 'WINDOW'))

    else:
        # Level 1 — entire editor area
        space_cls_name = SPACE_CLASS_MAP.get(space, "SpaceView3D")
        space_cls = getattr(bpy.types, space_cls_name, None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_highlight_overlay(),
                (), 'WINDOW', 'POST_PIXEL'
            )
            _highlight_handlers.append((space_cls_name, handler, 'WINDOW'))

    # Tag areas for redraw + start continuous redraw timer
    _tag_redraw()
    _start_redraw_timer()


def clear_all_highlights():
    """Remove all active GPU handlers and panel patches."""
    global _highlight_handlers

    _stop_redraw_timer()

    for space_cls_name, handler, region_type in _highlight_handlers:
        space_cls = getattr(bpy.types, space_cls_name, None)
        if space_cls:
            try:
                space_cls.draw_handler_remove(handler, region_type)
            except Exception:
                pass

    _highlight_handlers.clear()
    _clear_panel_highlight()
    _tag_redraw()


def trigger_highlight_from_json(highlight_json_str: str, context=None):
    """Parse a JSON string and trigger the appropriate highlight."""
    if not highlight_json_str:
        clear_all_highlights()
        return

    try:
        data = json.loads(highlight_json_str)
    except json.JSONDecodeError:
        clear_all_highlights()
        return

    trigger_highlight(data, context)


def _tag_redraw():
    """Tag all screen areas for redraw."""
    try:
        screen = bpy.context.screen
        if screen:
            for area in screen.areas:
                area.tag_redraw()
    except Exception:
        pass
