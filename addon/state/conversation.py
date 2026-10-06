# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Conversation and quest state management
#
# State is dual-registered:
# 1. On bpy.types.WindowManager: immune to scene undo/redo resets and viewport mesh edits.
# 2. On bpy.types.Scene: persists with .blend file saves and provides immediate access for UI panels.
# An undo_post handler automatically ensures that whenever Blender undoes a scene action
# (like adding or modifying an object), the active quest steps and chat history are instantly
# restored from WindowManager.

import bpy
from bpy.app.handlers import persistent


class ChatMessage(bpy.types.PropertyGroup):
    """A single chat message (user or AI)."""
    text: bpy.props.StringProperty(name="Text", default="")
    is_user: bpy.props.BoolProperty(name="Is User", default=True)


class StepItem(bpy.types.PropertyGroup):
    """One step in the AI's structured response."""
    instruction: bpy.props.StringProperty(name="Instruction", default="")
    description: bpy.props.StringProperty(name="Description", default="")
    is_done: bpy.props.BoolProperty(name="Done", default=False)
    # Highlight data stored as a JSON string (parsed at runtime)
    highlight_json: bpy.props.StringProperty(name="Highlight JSON", default="")
    # Blender UI icon identifier (e.g. 'MODIFIER', 'MOD_SUBSURF', 'MATERIAL')
    icon: bpy.props.StringProperty(name="Icon", default="NONE")


# ------------------------------------------------------------------
# Registration helpers
# ------------------------------------------------------------------

_classes = (ChatMessage, StepItem)

_suppress_message_update = False
_is_syncing = False


def sync_scene_to_wm(scene=None):
    """Mirror scene state to WindowManager."""
    global _is_syncing
    if _is_syncing:
        return
    _is_syncing = True
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if not wm:
            return
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        if not scene:
            return

        # Copy steps
        wm.bm_steps.clear()
        for s in scene.bm_steps:
            item = wm.bm_steps.add()
            item.instruction = s.instruction
            item.description = s.description
            item.is_done = s.is_done
            item.highlight_json = s.highlight_json
            item.icon = s.icon

        # Copy chat history
        wm.bm_chat_history.clear()
        for m in scene.bm_chat_history:
            item = wm.bm_chat_history.add()
            item.text = m.text
            item.is_user = m.is_user

        # Copy primitives
        wm.bm_current_step = scene.bm_current_step
        wm.bm_followup_step = scene.bm_followup_step
        wm.bm_last_ai_response = scene.bm_last_ai_response
        wm.bm_status_message = scene.bm_status_message
        wm.bm_is_processing = scene.bm_is_processing
        wm.bm_youtube_query = scene.bm_youtube_query
        wm.bm_remote_mic_active = scene.bm_remote_mic_active
        wm.bm_remote_mic_send = scene.bm_remote_mic_send
        wm.bm_remote_mic_abort = scene.bm_remote_mic_abort
    finally:
        _is_syncing = False


def sync_wm_to_scene(scene=None):
    """Restore scene state from WindowManager (e.g. after undo/redo or scene reload)."""
    global _is_syncing, _suppress_message_update
    if _is_syncing:
        return
    _is_syncing = True
    _suppress_message_update = True
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if not wm:
            return
        if scene is None:
            scene = getattr(bpy.context, "scene", None)
        if not scene:
            return

        # Restore steps if WM has steps or scene had steps wiped
        if len(wm.bm_steps) > 0 or len(scene.bm_steps) > 0:
            scene.bm_steps.clear()
            for s in wm.bm_steps:
                item = scene.bm_steps.add()
                item.instruction = s.instruction
                item.description = s.description
                item.is_done = s.is_done
                item.highlight_json = s.highlight_json
                item.icon = s.icon

        # Restore chat history
        if len(wm.bm_chat_history) > 0 or len(scene.bm_chat_history) > 0:
            scene.bm_chat_history.clear()
            for m in wm.bm_chat_history:
                item = scene.bm_chat_history.add()
                item.text = m.text
                item.is_user = m.is_user

        # Restore primitives
        scene.bm_current_step = wm.bm_current_step
        scene.bm_followup_step = wm.bm_followup_step
        scene.bm_last_ai_response = wm.bm_last_ai_response
        scene.bm_status_message = wm.bm_status_message
        scene.bm_is_processing = wm.bm_is_processing
        scene.bm_youtube_query = wm.bm_youtube_query
        scene.bm_remote_mic_active = wm.bm_remote_mic_active
        scene.bm_remote_mic_send = wm.bm_remote_mic_send
        scene.bm_remote_mic_abort = wm.bm_remote_mic_abort
    finally:
        _suppress_message_update = False
        _is_syncing = False


@persistent
def on_undo_post(scene, dummy=None):
    """Automatically triggered after any user Undo (Cmd+Z or operator adjustments).

    Restores the mentor's quest steps and chat history from WindowManager so they are
    never erased when the user undoes actions on the 3D scene.
    """
    try:
        sync_wm_to_scene(scene)
    except Exception as e:
        print(f"[BlenderMentor] Error in undo_post handler: {e}")


@persistent
def on_load_post(dummy1, dummy2=None):
    """Triggered after loading a .blend file. Initializes WindowManager from the saved Scene."""
    try:
        scene = getattr(bpy.context, "scene", None)
        if scene and (len(scene.bm_steps) > 0 or len(scene.bm_chat_history) > 0):
            sync_scene_to_wm(scene)
    except Exception as e:
        print(f"[BlenderMentor] Error in load_post handler: {e}")


def on_message_update(self, context):
    global _suppress_message_update
    if _suppress_message_update:
        return
    # Never auto-send while remote voice dictation is active
    if getattr(self, "bm_remote_mic_active", False):
        return
    if not self.bm_input_text.strip():
        return

    # Schedule the operator execution to avoid context lock during property update
    def run_op():
        scene = getattr(bpy.context, "scene", None)
        if not scene:
            return None
        if getattr(scene, "bm_remote_mic_active", False):
            return None
        if not scene.bm_input_text.strip():
            return None
        bpy.ops.blendermentor.send_message('EXEC_DEFAULT')
        return None

    bpy.app.timers.register(run_op, first_interval=0.01)


def register_properties():
    for cls in _classes:
        bpy.utils.register_class(cls)

    # 1. Register on WindowManager (session storage immune to scene undo/redo)
    bpy.types.WindowManager.bm_chat_history = bpy.props.CollectionProperty(type=ChatMessage)
    bpy.types.WindowManager.bm_steps = bpy.props.CollectionProperty(type=StepItem)
    bpy.types.WindowManager.bm_current_step = bpy.props.IntProperty(
        name="Current Step", default=0, min=0
    )
    bpy.types.WindowManager.bm_input_text = bpy.props.StringProperty(
        name="Message", default=""
    )
    bpy.types.WindowManager.bm_followup_step = bpy.props.IntProperty(
        name="Follow-up Step", default=-1,
        description="Step index the user is asking about (-1 = fresh question)"
    )
    bpy.types.WindowManager.bm_last_ai_response = bpy.props.StringProperty(
        name="Last AI Response", default="",
        description="Raw JSON of the last AI response (for follow-up context)"
    )
    bpy.types.WindowManager.bm_status_message = bpy.props.StringProperty(
        name="Status Message", default="",
        description="Current AI processing status (e.g. 'Checking Render Settings...')"
    )
    bpy.types.WindowManager.bm_is_processing = bpy.props.BoolProperty(
        name="Is Processing", default=False,
        description="True while the AI is working in the background"
    )
    bpy.types.WindowManager.bm_youtube_query = bpy.props.StringProperty(
        name="YouTube Query", default="",
        description="YouTube query generated by the AI for the steps"
    )
    bpy.types.WindowManager.bm_remote_mic_active = bpy.props.BoolProperty(
        name="Remote Mic Active", default=False,
        description="True while microphone is listening in browser"
    )
    bpy.types.WindowManager.bm_remote_mic_send = bpy.props.BoolProperty(
        name="Remote Mic Send", default=False,
        description="Flag signaling browser to finalize and send dictation"
    )
    bpy.types.WindowManager.bm_remote_mic_abort = bpy.props.BoolProperty(
        name="Remote Mic Abort", default=False,
        description="Flag signaling browser to cancel/abort dictation without sending (Alt + Down Arrow)"
    )

    # 2. Register on Scene (persists with file, supports N-Panel layouts)
    bpy.types.Scene.bm_chat_history = bpy.props.CollectionProperty(type=ChatMessage)
    bpy.types.Scene.bm_steps = bpy.props.CollectionProperty(type=StepItem)
    bpy.types.Scene.bm_current_step = bpy.props.IntProperty(
        name="Current Step", default=0, min=0
    )
    bpy.types.Scene.bm_input_text = bpy.props.StringProperty(
        name="Message", default="", update=on_message_update
    )
    bpy.types.Scene.bm_followup_step = bpy.props.IntProperty(
        name="Follow-up Step", default=-1,
        description="Step index the user is asking about (-1 = fresh question)"
    )
    bpy.types.Scene.bm_last_ai_response = bpy.props.StringProperty(
        name="Last AI Response", default="",
        description="Raw JSON of the last AI response (for follow-up context)"
    )
    bpy.types.Scene.bm_status_message = bpy.props.StringProperty(
        name="Status Message", default="",
        description="Current AI processing status (e.g. 'Checking Render Settings...')"
    )
    bpy.types.Scene.bm_is_processing = bpy.props.BoolProperty(
        name="Is Processing", default=False,
        description="True while the AI is working in the background"
    )
    bpy.types.Scene.bm_youtube_query = bpy.props.StringProperty(
        name="YouTube Query", default="",
        description="YouTube query generated by the AI for the steps"
    )
    bpy.types.Scene.bm_remote_mic_active = bpy.props.BoolProperty(
        name="Remote Mic Active", default=False,
        description="True while microphone is listening in browser"
    )
    bpy.types.Scene.bm_remote_mic_send = bpy.props.BoolProperty(
        name="Remote Mic Send", default=False,
        description="Flag signaling browser to finalize and send dictation"
    )
    bpy.types.Scene.bm_remote_mic_abort = bpy.props.BoolProperty(
        name="Remote Mic Abort", default=False,
        description="Flag signaling browser to cancel/abort dictation without sending (Alt + Down Arrow)"
    )

    # Handlers for undo preservation and file load
    if on_undo_post not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(on_undo_post)
    if on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)


def unregister_properties():
    if on_undo_post in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(on_undo_post)
    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)

    props = [
        "bm_remote_mic_abort",
        "bm_remote_mic_send",
        "bm_remote_mic_active",
        "bm_youtube_query",
        "bm_is_processing",
        "bm_status_message",
        "bm_last_ai_response",
        "bm_followup_step",
        "bm_input_text",
        "bm_current_step",
        "bm_steps",
        "bm_chat_history",
    ]
    for prop in props:
        try:
            delattr(bpy.types.WindowManager, prop)
        except Exception:
            pass
        try:
            delattr(bpy.types.Scene, prop)
        except Exception:
            pass

    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
