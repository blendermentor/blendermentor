# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Floating / Docked UI window management

import bpy
import time


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

# Track which windows were created by our pop-out operator
_popout_window_ids = set()

# Track docked area (we store the area reference per-screen)
_docked_area_tag = "BLENDERMENTOR_DOCKED"


# ---------------------------------------------------------------------------
# Operator: Pop Out (floating window)
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_popout(bpy.types.Operator):
    bl_idname = "blendermentor.popout"
    bl_label = "Pop Out BlenderMentor"
    bl_description = (
        "Open BlenderMentor in a separate floating window — "
        "great for dual-monitor setups"
    )

    def execute(self, context):
        # Remember current window count to detect the new one
        old_windows = set(id(w) for w in context.window_manager.windows)

        # Create a new window via duplicate-window (this gives us a full
        # copy of the current workspace; we then strip it down)
        bpy.ops.wm.window_new()

        # Find the new window
        new_win = None
        for w in context.window_manager.windows:
            if id(w) not in old_windows:
                new_win = w
                break

        if not new_win:
            self.report({'WARNING'}, "Could not create floating window.")
            return {'CANCELLED'}

        _popout_window_ids.add(id(new_win))

        # In the new window, find the first VIEW_3D area and
        # open the N-panel sidebar (which hosts our BlenderMentor tab)
        for area in new_win.screen.areas:
            if area.type == 'VIEW_3D':
                # Ensure sidebar is open
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.show_region_ui = True
                break

        # Resize the new window to a comfortable mentor size
        # (narrower, taller — like a chat window)
        new_win.width = 480
        new_win.height = 800

        self.report({'INFO'}, "BlenderMentor opened in floating window.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Dock Right (split the viewport)
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_dock(bpy.types.Operator):
    bl_idname = "blendermentor.dock"
    bl_label = "Dock BlenderMentor"
    bl_description = (
        "Split the 3D Viewport to create a dedicated BlenderMentor panel "
        "docked to the right"
    )

    def execute(self, context):
        area = context.area
        if not area or area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Run this from a 3D Viewport.")
            return {'CANCELLED'}

        # Check if already docked (avoid double-split)
        if context.scene.get("bm_is_docked", False):
            self.report({'INFO'}, "Already docked. Use undock first.")
            return {'CANCELLED'}

        # Split the current area vertically at ~70% from the left
        # The new area appears on the right
        override = context.copy()
        override['area'] = area

        # We need to use the area_split with a factor
        # factor=0.7 means the original keeps 70%, new gets 30%
        try:
            with context.temp_override(**override):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.7)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to split area: {e}")
            return {'CANCELLED'}

        # The new area is the last one in the screen's area list
        # Find the new VIEW_3D area that appeared (it's the rightmost one)
        screen = context.window.screen
        view3d_areas = [a for a in screen.areas if a.type == 'VIEW_3D']

        if len(view3d_areas) < 2:
            self.report({'WARNING'}, "Split didn't create a new area.")
            return {'CANCELLED'}

        # The new area is the one with the smallest width (or the rightmost)
        # After a VERTICAL split, the new area is typically the last VIEW_3D
        new_area = view3d_areas[-1]

        # Open the sidebar in the new area
        for space in new_area.spaces:
            if space.type == 'VIEW_3D':
                space.show_region_ui = True
                # Optionally hide the toolbar/header to save space
                space.show_region_toolbar = False
                break

        # Mark as docked in scene properties
        context.scene["bm_is_docked"] = True

        self.report({'INFO'}, "BlenderMentor docked to the right.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Undock (join the docked area back)
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_undock(bpy.types.Operator):
    bl_idname = "blendermentor.undock"
    bl_label = "Undock BlenderMentor"
    bl_description = "Close the docked BlenderMentor panel and reclaim the space"

    def execute(self, context):
        if not context.scene.get("bm_is_docked", False):
            self.report({'INFO'}, "Not currently docked.")
            return {'CANCELLED'}

        # Find VIEW_3D areas — we want to join the smaller one into the larger
        screen = context.window.screen
        view3d_areas = [a for a in screen.areas if a.type == 'VIEW_3D']

        if len(view3d_areas) < 2:
            context.scene["bm_is_docked"] = False
            self.report({'INFO'}, "No docked area found to close.")
            return {'CANCELLED'}

        # Sort by width — the narrower one is likely our docked panel
        view3d_areas.sort(key=lambda a: a.width)
        narrow = view3d_areas[0]

        # Join by changing it to the same type as the neighbor
        # Unfortunately Blender doesn't have a reliable "join areas" operator
        # from Python. The best we can do is close the sidebar and
        # let the user manually join areas (Ctrl+click drag the border).
        # OR we can use area_join with overrides.
        try:
            # Find an adjacent area to join with
            wider = view3d_areas[-1]

            override = context.copy()
            override['area'] = wider

            with context.temp_override(**override):
                bpy.ops.screen.area_join(
                    cursor=(narrow.x + narrow.width // 2,
                            narrow.y + narrow.height // 2)
                )
        except Exception:
            # Fallback: just hide sidebar in the narrow area
            for space in narrow.spaces:
                if space.type == 'VIEW_3D':
                    space.show_region_ui = False
                    break
            self.report({'INFO'},
                        "Sidebar hidden. Drag area border to fully close.")

        context.scene["bm_is_docked"] = False
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: Toggle sidebar (convenience)
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_toggle_sidebar(bpy.types.Operator):
    bl_idname = "blendermentor.toggle_sidebar"
    bl_label = "Toggle BlenderMentor Sidebar"
    bl_description = "Show or hide the BlenderMentor sidebar in this viewport"

    def execute(self, context):
        space = context.space_data
        if space and space.type == 'VIEW_3D':
            space.show_region_ui = not space.show_region_ui
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    BLENDERMENTOR_OT_popout,
    BLENDERMENTOR_OT_dock,
    BLENDERMENTOR_OT_undock,
    BLENDERMENTOR_OT_toggle_sidebar,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
