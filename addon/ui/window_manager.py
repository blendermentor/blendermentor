# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — UI window management

import bpy


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
    BLENDERMENTOR_OT_toggle_sidebar,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
