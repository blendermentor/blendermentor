# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — UI Highlighting (3 levels: area, tab, panel)

import bpy
import gpu
import blf
import time
import json
import math
from gpu_extras.batch import batch_for_shader

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_highlight_handlers = []      # list of (space_cls_name, handler_ref, region_type)
_highlight_start_time = 0.0
HIGHLIGHT_DURATION = 4.0      # seconds
_redraw_timer = None          # bpy.app.timers handle

# Panel monkey-patch state
_active_panel_id = None
_panel_prepend_fn = None

# ---------------------------------------------------------------------------
# Properties tab coordinate lookup (Level 2)
# ---------------------------------------------------------------------------

# Coordinate lookup — vertical index of each Properties tab icon
# Order is top-to-bottom for a MESH object.
PROPERTIES_TAB_INDEX = {
    "active_tool":  0,
    "render":       1,
    "output":       2,
    "view_layer":   3,
    "scene":        4,
    "world":        5,
    "collection":   6,
    "object":       7,
    "modifiers":    8,   # wrench icon
    "particles":    9,
    "physics":      10,
    "constraints":  11,
    "object_data":  12,
    "material":     13,
}

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

# Panel id → bpy.types panel class name (for Approach 3)
PANEL_CLASS_MAP = {
    "modifier_add_button":  "DATA_PT_modifiers",
    "material_slots":       "MATERIAL_PT_context_material",
    "render_settings":      "RENDER_PT_render",
    "output_settings":      "OUTPUT_PT_output",
    "transform_panel":      "OBJECT_PT_transform",
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
# GPU draw callback — pulsing amber overlay (Level 1 & 2)
# ---------------------------------------------------------------------------

def _draw_highlight_overlay(region_type_filter, label_text):
    """Factory that returns a draw handler callback (takes NO arguments)."""

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

        # Draw label text centred in the region
        if label_text:
            font_id = 0
            blf.size(font_id, 16)
            tw, th = blf.dimensions(font_id, label_text)
            x = max(4, (w - tw) / 2)
            y = max(4, (h - th) / 2)

            # Shadow
            blf.position(font_id, x + 1, y - 1, 0)
            blf.color(font_id, 0, 0, 0, alpha)
            blf.draw(font_id, label_text)

            # Foreground
            blf.position(font_id, x, y, 0)
            blf.color(font_id, 1, 1, 1, min(1.0, alpha + 0.3))
            blf.draw(font_id, label_text)

        gpu.state.blend_set('NONE')

    return _draw


# ---------------------------------------------------------------------------
# Level 2 — NAVIGATION_BAR tab highlight
# ---------------------------------------------------------------------------

def _draw_tab_highlight(tab_name):
    """Factory for a draw handler that highlights one tab icon in the nav bar."""

    def _draw():
        global _highlight_start_time

        elapsed = time.time() - _highlight_start_time
        if elapsed > HIGHLIGHT_DURATION:
            return

        tab_index = PROPERTIES_TAB_INDEX.get(tab_name.lower())
        if tab_index is None:
            return

        region = bpy.context.region
        if not region:
            return
        w = region.width
        h = region.height

        # Each tab icon ≈ 28px, stacked from top
        icon_h = 28
        top_pad = 28  # offset for header/padding at top of nav bar

        # y from top (with padding offset)
        y_top = h - top_pad - (tab_index * icon_h)
        y_bottom = y_top - icon_h

        if y_bottom < 0 or y_top > h:
            return

        pulse = (math.sin(time.time() * 8.0) + 1.0) / 2.0
        alpha = pulse * 0.7

        gpu.state.blend_set('ALPHA')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        verts = ((0, y_bottom), (w, y_bottom), (w, y_top), (0, y_top))
        batch = batch_for_shader(shader, 'TRIS', {"pos": verts},
                                 indices=((0, 1, 2), (0, 2, 3)))
        shader.bind()
        shader.uniform_float("color", (1.0, 0.5, 0.0, alpha * 0.5))
        batch.draw(shader)

        # Border
        b = 2
        bv = (
            (0, y_bottom), (w, y_bottom), (w, y_top), (0, y_top),
            (b, y_bottom + b), (w - b, y_bottom + b),
            (w - b, y_top - b), (b, y_top - b),
        )
        bi = (
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        )
        batch2 = batch_for_shader(shader, 'TRIS', {"pos": bv}, indices=bi)
        shader.uniform_float("color", (1.0, 0.5, 0.0, alpha))
        batch2.draw(shader)

        # Label — draw to the right of the tab strip
        font_id = 0
        blf.size(font_id, 13)
        label = f"◀ {tab_name.replace('_', ' ').title()}"
        tw, th = blf.dimensions(font_id, label)
        lx = w + 6
        ly = (y_top + y_bottom - th) / 2

        blf.position(font_id, lx + 1, ly - 1, 0)
        blf.color(font_id, 0, 0, 0, 0.9)
        blf.draw(font_id, label)

        blf.position(font_id, lx, ly, 0)
        blf.color(font_id, 1.0, 0.8, 0.0, 1.0)
        blf.draw(font_id, label)

        gpu.state.blend_set('NONE')

    return _draw


# ---------------------------------------------------------------------------
# Level 3 — Panel prepend/append injection
# ---------------------------------------------------------------------------

def _panel_highlight_callback(self, context):
    """Injected into a panel's draw to show a visual indicator."""
    global _active_panel_id
    if _active_panel_id:
        row = self.layout.row()
        row.alert = True
        row.label(text="◀ HERE", icon='RESTRICT_SELECT_OFF')


def _inject_panel_highlight(panel_id: str):
    """Monkey-patch a Blender panel class to show the highlight indicator."""
    global _active_panel_id, _panel_prepend_fn

    _clear_panel_highlight()

    class_name = PANEL_CLASS_MAP.get(panel_id)
    if not class_name:
        return

    panel_cls = getattr(bpy.types, class_name, None)
    if not panel_cls:
        return

    _active_panel_id = panel_id
    _panel_prepend_fn = _panel_highlight_callback
    panel_cls.prepend(_panel_prepend_fn)


def _clear_panel_highlight():
    """Remove any active panel monkey-patch."""
    global _active_panel_id, _panel_prepend_fn

    if _active_panel_id and _panel_prepend_fn:
        class_name = PANEL_CLASS_MAP.get(_active_panel_id)
        if class_name:
            panel_cls = getattr(bpy.types, class_name, None)
            if panel_cls:
                try:
                    panel_cls.remove(_panel_prepend_fn)
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
        # Level 2 — tab icon in Properties NAVIGATION_BAR
        space_cls = getattr(bpy.types, "SpaceProperties", None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_tab_highlight(target), (),
                'NAVIGATION_BAR', 'POST_PIXEL'
            )
            _highlight_handlers.append(("SpaceProperties", handler, 'NAVIGATION_BAR'))

            # Also glow the properties WINDOW with a label
            handler2 = space_cls.draw_handler_add(
                _draw_highlight_overlay('WINDOW', target.replace("_", " ").title()),
                (), 'WINDOW', 'POST_PIXEL'
            )
            _highlight_handlers.append(("SpaceProperties", handler2, 'WINDOW'))

    elif level == "panel":
        # Level 3 — panel monkey-patch
        _inject_panel_highlight(target)

        # Also glow the properties area
        space_cls_name = SPACE_CLASS_MAP.get(space, "SpaceProperties")
        space_cls = getattr(bpy.types, space_cls_name, None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_highlight_overlay('WINDOW', "Look for ◀ HERE indicator"),
                (), 'WINDOW', 'POST_PIXEL'
            )
            _highlight_handlers.append((space_cls_name, handler, 'WINDOW'))

    else:
        # Level 1 — entire editor area
        space_cls_name = SPACE_CLASS_MAP.get(space, "SpaceView3D")
        space_cls = getattr(bpy.types, space_cls_name, None)
        if space_cls:
            handler = space_cls.draw_handler_add(
                _draw_highlight_overlay('WINDOW', target.replace("_", " ").title()),
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
