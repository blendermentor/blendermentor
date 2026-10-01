# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — AI-powered Blender teaching assistant
#
# This addon provides an in-Blender chat interface that teaches users
# how to use Blender through step-by-step guidance with UI highlighting.
# It never performs actions on the user's behalf.

bl_info = {
    "name": "BlenderMentor",
    "author": "BlenderMentor Contributors",
    "version": (0, 4, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > BlenderMentor (also supports floating window & docked split)",
    "description": "AI-powered teaching assistant that guides you through Blender with step-by-step instructions and UI highlighting",
    "category": "3D View",
}

from . import preferences
from .state import conversation
from .ui import chat_panel
from .ui import window_manager
from . import server


def register():
    conversation.register_properties()
    preferences.register()
    window_manager.register()
    chat_panel.register()
    server.start_server(port=8765)


def unregister():
    server.stop_server()
    chat_panel.unregister()
    window_manager.unregister()
    preferences.unregister()
    conversation.unregister_properties()
