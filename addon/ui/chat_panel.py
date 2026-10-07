# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Chat panel, step navigator, and dev tools

import bpy
import bpy.utils.previews
import json
import os
import time
import textwrap
import threading

_preview_collections = {}

def get_logo_icon_id():
    pcoll = _preview_collections.get("main")
    if pcoll and "logo" in pcoll:
        return pcoll["logo"].icon_id
    return 0

def get_mic_icon_id(active: bool = False):
    pcoll = _preview_collections.get("main")
    key = "mic_active" if active else "mic"
    if pcoll and key in pcoll:
        return pcoll[key].icon_id
    return 0

from ..ai_client import ask_ai
from ..scene_reader import get_basic_context_json, get_scene_context
from ..state.conversation import sync_scene_to_wm, sync_wm_to_scene
from ..logger import (
    get_log_filepath,
    get_last_exchange,
    get_full_log,
    clear_log,
    sync_to_blender_text,
)
from .highlight import (
    trigger_highlight_from_json,
    clear_all_highlights,
    trigger_highlight,
)

# ---------------------------------------------------------------------------
# Background thread state
# ---------------------------------------------------------------------------

_worker_thread = None
_worker_result = None   # dict | Exception | None
_worker_lock = threading.Lock()


def _status_callback(message: str):
    """Called from the worker thread to update the status message.

    We use bpy.app.timers because bpy property writes from a
    non-main thread are unsafe.
    """
    def _set():
        try:
            if hasattr(bpy.context, "scene") and bpy.context.scene:
                bpy.context.scene.bm_status_message = message
                bpy.context.scene.bm_is_processing = True
            wm = getattr(bpy.context, "window_manager", None)
            if wm:
                wm.bm_status_message = message
                wm.bm_is_processing = True
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                            for r in area.regions:
                                if r.type == 'UI':
                                    r.tag_redraw()
        except Exception:
            pass
        return None
    bpy.app.timers.register(_set, first_interval=0.0)


def _ai_worker(prefs_snapshot: dict, basic_ctx: str, prompt: str):
    """Run in a background thread — calls the AI with tool-calling loop."""
    global _worker_result

    # Reconstruct a minimal prefs-like object from the snapshot
    class _PrefsProxy:
        def __init__(self, d):
            for k, v in d.items():
                setattr(self, k, v)
        def get_selected_model_id(self):
            return self.model_id

    proxy = _PrefsProxy(prefs_snapshot)

    try:
        result = ask_ai(proxy, basic_ctx, prompt,
                        status_callback=_status_callback)
        with _worker_lock:
            _worker_result = result
    except Exception as e:
        with _worker_lock:
            _worker_result = e


def _poll_worker_done():
    """Timer callback — checks if the background AI call has finished."""
    global _worker_result, _worker_thread

    with _worker_lock:
        result = _worker_result

    if result is None:
        # Still working — keep tagging 3D Viewport areas and UI sidebar regions for redraw so Thinking indicator persists live
        try:
            wm = getattr(bpy.context, "window_manager", None)
            if wm:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                            for r in area.regions:
                                if r.type == 'UI':
                                    r.tag_redraw()
        except Exception:
            pass
        return 0.15

    # Work is done — process the result on the main thread
    scene = bpy.context.scene
    scene.bm_is_processing = False
    scene.bm_status_message = ""
    wm = getattr(bpy.context, "window_manager", None)
    if wm:
        wm.bm_is_processing = False
        wm.bm_status_message = ""

    if isinstance(result, Exception):
        reply = scene.bm_chat_history.add()
        reply.text = f"❌ Error: {str(result)}"
        reply.is_user = False
        sync_scene_to_wm(scene)
    else:
        _apply_ai_response(scene, result)

    scene.bm_followup_step = -1
    sync_scene_to_wm(scene)

    # In developer mode, mirror exchange into Blender Text Editor
    try:
        addon_prefs = bpy.context.preferences.addons.get(__package__.rpartition('.')[0])
        if addon_prefs and getattr(addon_prefs.preferences, "developer_mode", False):
            sync_to_blender_text()
    except Exception as e:
        print(f"[BlenderMentor] Error syncing log in _poll_worker_done: {e}")

    # Clean up
    with _worker_lock:
        _worker_result = None
    _worker_thread = None

    # Redraw all areas across all windows
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if wm:
            for win in wm.windows:
                for area in win.screen.areas:
                    area.tag_redraw()
                    for r in area.regions:
                        if r.type == 'UI':
                            r.tag_redraw()
    except Exception:
        pass

    return None  # unregister timer


_VALID_BLENDER_ICONS = None

ICON_ALIASES = {
    # Modifiers & Tools
    "wrench": "MODIFIER",
    "modifier": "MODIFIER",
    "modifiers": "MODIFIER",
    "subsurf": "MOD_SUBSURF",
    "subdivision": "MOD_SUBSURF",
    "subdivision_surface": "MOD_SUBSURF",
    "bevel": "MOD_BEVEL",
    "mirror": "MOD_MIRROR",
    "boolean": "MOD_BOOLEAN",
    "array": "MOD_ARRAY",
    "solidify": "MOD_SOLIDIFY",
    "smooth": "MOD_SMOOTH",
    "decimate": "MOD_DECIM",
    "armature": "MOD_ARMATURE",
    "curve": "MOD_CURVE",
    "shrinkwrap": "MOD_SHRINKWRAP",
    "lattice": "MOD_LATTICE",
    "simple_deform": "MOD_SIMPLEDEFORM",

    # Properties & Editors
    "material": "MATERIAL",
    "materials": "MATERIAL",
    "texture": "TEXTURE",
    "render": "RENDER_RESULT",
    "render_settings": "RESTRICT_RENDER_OFF",
    "output": "OUTPUT",
    "output_settings": "OUTPUT",
    "world": "WORLD",
    "scene": "SCENE_DATA",
    "collection": "OUTLINER_COLLECTION",
    "outliner": "OUTLINER",
    "timeline": "TIME",
    "dopesheet": "ACTION",
    "graph_editor": "GRAPH",
    "shader_editor": "NODE",
    "compositor": "NODE_COMPOSITING",
    "properties": "PROPERTIES",
    "viewport": "VIEW3D",
    "3d_viewport": "VIEW3D",

    # Modes & Object Types
    "edit_mode": "EDITMODE_HLT",
    "edit": "EDITMODE_HLT",
    "object_mode": "OBJECT_DATAMODE",
    "object": "OBJECT_DATAMODE",
    "sculpt_mode": "SCULPTMODE_HLT",
    "sculpt": "SCULPTMODE_HLT",
    "pose_mode": "POSE_HLT",
    "camera": "CAMERA_DATA",
    "light": "LIGHT_DATA",
    "lamp": "LIGHT_DATA",
    "sun": "LIGHT_SUN",
    "mesh": "MESH_DATA",
    "cube": "MESH_CUBE",
    "sphere": "MESH_UVSPHERE",
    "cylinder": "MESH_CYLINDER",
    "plane": "MESH_PLANE",
    "monkey": "MONKEY",
    "suzanne": "MONKEY",

    # Actions & Menus
    "add": "ADD",
    "shift_a": "ADD",
    "select": "RESTRICT_SELECT_OFF",
    "select_all": "SELECT_SET",
    "delete": "TRASH",
    "remove": "TRASH",
    "undo": "LOOP_BACK",
    "redo": "LOOP_FORWARDS",
    "save": "FILE_TICK",
    "open": "FILE_FOLDER",
    "zoom": "VIEW_ZOOM",
    "pan": "VIEW_PAN",
    "hide": "HIDE_ON",
    "unhide": "HIDE_OFF",
    "keyframe": "KEYFRAME",
    "physics": "PHYSICS",
    "particles": "PARTICLES",
    "particle": "PARTICLES",
    "constraints": "CONSTRAINT",
    "constraint": "CONSTRAINT",
    "bones": "BONE_DATA",
    "cursor": "CURSOR",
    "transform": "ORIENTATION_GLOBAL",
    "move": "TRANSFORM_MOVE",
    "rotate": "TRANSFORM_ROTATE",
    "scale": "TRANSFORM_SCALE",
}


def _get_valid_blender_icons():
    """Dynamically get all valid built-in icon enum identifiers in Blender."""
    global _VALID_BLENDER_ICONS
    if _VALID_BLENDER_ICONS is None:
        try:
            _VALID_BLENDER_ICONS = {
                it.identifier
                for it in bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items
            }
        except Exception:
            _VALID_BLENDER_ICONS = set()
    return _VALID_BLENDER_ICONS


def _resolve_step_icon(raw_icon, highlight_data, instruction: str) -> str:
    """Robustly resolve a step icon to a guaranteed valid Blender icon identifier."""
    valid_icons = _get_valid_blender_icons()

    # 1. Check explicit raw_icon from AI
    if raw_icon and isinstance(raw_icon, str):
        candidate = raw_icon.strip()
        cand_upper = candidate.upper()
        if cand_upper in valid_icons:
            return cand_upper
        alias = ICON_ALIASES.get(candidate.lower())
        if alias and alias in valid_icons:
            return alias

    # 2. Check highlight target (e.g. target='modifiers' -> 'MODIFIER')
    if highlight_data and isinstance(highlight_data, dict):
        target = str(highlight_data.get("target", "")).lower()
        if target:
            alias = ICON_ALIASES.get(target)
            if alias and alias in valid_icons:
                return alias
            if target.upper() in valid_icons:
                return target.upper()

    # 3. Keyword scan in instruction
    if instruction:
        inst_lower = instruction.lower()
        for kw, icon_id in ICON_ALIASES.items():
            if kw in inst_lower:
                if icon_id in valid_icons:
                    return icon_id

    return "NONE"


def _apply_ai_response(scene, response: dict):
    """Populate steps and chat from a successful AI response dict."""
    steps = response.get("steps", [])

    if steps:
        scene.bm_steps.clear()
        scene.bm_youtube_query = response.get("youtube_search_query", "")
        for step_data in steps:
            s = scene.bm_steps.add()
            s.instruction = step_data.get("instruction", "")
            s.description = step_data.get("description", "")
            s.is_done = False
            highlight = step_data.get("highlight")
            s.highlight_json = json.dumps(highlight) if highlight else ""
            s.icon = _resolve_step_icon(step_data.get("icon"), highlight, s.instruction)


        # Determine focus step index (1-indexed from AI, or fallback to active followup step)
        focus_idx = 0
        ai_focus = response.get("focus_step_index")
        if isinstance(ai_focus, int) and 1 <= ai_focus <= len(steps):
            focus_idx = ai_focus - 1
        elif scene.bm_followup_step >= 0 and scene.bm_followup_step < len(steps):
            focus_idx = scene.bm_followup_step

        scene.bm_current_step = focus_idx

        summary = response.get("summary",
                               f"{len(steps)} steps ready — see below!")
        reply = scene.bm_chat_history.add()
        reply.text = f"💡 {summary}"
        reply.is_user = False

        scene.bm_last_ai_response = json.dumps(response)

        # Trigger highlight on the focused step
        if steps[focus_idx].get("highlight"):
            trigger_highlight(steps[focus_idx]["highlight"])
        else:
            clear_all_highlights()

        # Tag all areas for instant redraw
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        # Mirror state to WindowManager so it survives scene undo/redo resets
        sync_scene_to_wm(scene)
    else:
        reply = scene.bm_chat_history.add()
        reply.text = "The AI did not return any steps. Please try again."
        reply.is_user = False
        sync_scene_to_wm(scene)


class BLENDERMENTOR_OT_send_message(bpy.types.Operator):
    bl_idname = "blendermentor.send_message"
    bl_label = "Send"
    bl_description = "Send your message to the AI mentor"

    def execute(self, context):
        global _worker_thread, _worker_result

        scene = bpy.context.scene
        user_text = scene.bm_input_text.strip()
        if not user_text:
            return {'CANCELLED'}

        # Don't allow sending while already processing
        wm = getattr(bpy.context, "window_manager", None)
        if scene.bm_is_processing or (wm and getattr(wm, "bm_is_processing", False)):
            self.report({'WARNING'}, "Still processing — please wait.")
            return {'CANCELLED'}

        # Capture follow-up state before clearing
        followup_step = scene.bm_followup_step
        last_response = scene.bm_last_ai_response

        # Store user message (with follow-up context if applicable)
        msg = scene.bm_chat_history.add()
        if followup_step >= 0 and followup_step < len(scene.bm_steps):
            msg.text = f"[Re: Step {followup_step + 1}] {user_text}"
        else:
            msg.text = user_text
        msg.is_user = True
        scene.bm_input_text = ""

        # Immediately mirror new user chat message to WindowManager
        sync_scene_to_wm(scene)

        # Dev mode: handle test commands
        addon_prefs = context.preferences.addons.get(__package__.rpartition('.')[0])
        if addon_prefs and addon_prefs.preferences.developer_mode:
            result = _run_dev_command(scene, user_text)
            if result:
                scene.bm_followup_step = -1
                return {'FINISHED'}

        # Check for blink command (always available)
        if user_text.lower() == "blink":
            trigger_highlight({"level": "area", "space": "VIEW_3D", "target": "viewport"})
            reply = scene.bm_chat_history.add()
            reply.text = "✨ Blinking the 3D Viewport!"
            reply.is_user = False
            scene.bm_followup_step = -1
            sync_scene_to_wm(scene)
            return {'FINISHED'}

        # Get AI preferences
        if not addon_prefs:
            reply = scene.bm_chat_history.add()
            reply.text = "⚠ Please configure BlenderMentor in Edit → Preferences → Add-ons."
            reply.is_user = False
            scene.bm_followup_step = -1
            sync_scene_to_wm(scene)
            return {'FINISHED'}

        prefs = addon_prefs.preferences
        if prefs.provider != 'OLLAMA' and not prefs.api_key:
            reply = scene.bm_chat_history.add()
            reply.text = "⚠ No API key set. Go to Edit → Preferences → Add-ons → BlenderMentor."
            reply.is_user = False
            scene.bm_followup_step = -1
            sync_scene_to_wm(scene)
            return {'FINISHED'}

        # --- Launch background AI call ---
        basic_ctx = get_basic_context_json()

        # Build prompt — explicit follow-up, auto follow-up, or fresh
        if followup_step >= 0 and last_response:
            # User clicked the ❓ button on a specific step
            prompt = _build_followup_prompt(
                user_text, followup_step, last_response, basic_ctx
            )
        elif last_response:
            # Normal input but we have a previous response — let the AI
            # decide if this is a follow-up or a fresh question (rule 14)
            prompt = (
                f"PREVIOUS RESPONSE (for context — the user may or may not be "
                f"following up on this):\n{last_response}\n\n"
                f"USER'S NEW MESSAGE:\n\"{user_text}\""
            )
        else:
            prompt = user_text

        # Snapshot prefs so the thread doesn't touch bpy objects
        prefs_snapshot = {
            "provider": prefs.provider,
            "api_key": prefs.api_key,
            "model_id": prefs.get_selected_model_id(),
            "ollama_host": getattr(prefs, 'ollama_host', 'http://localhost:11434'),
            "enable_web_search": getattr(prefs, 'enable_web_search', True),
            "developer_mode": getattr(prefs, 'developer_mode', False),
        }

        # Mark processing state on both Scene and WindowManager
        scene.bm_is_processing = True
        scene.bm_status_message = "Thinking..."
        if wm:
            wm.bm_is_processing = True
            wm.bm_status_message = "Thinking..."

        # Mirror state to WindowManager so /api/state gets it instantly
        sync_scene_to_wm(scene)

        # Immediately tag all View3D areas and UI regions to draw the status indicator
        try:
            if wm:
                for win in wm.windows:
                    for area in win.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                            for r in area.regions:
                                if r.type == 'UI':
                                    r.tag_redraw()
        except Exception:
            pass

        with _worker_lock:
            _worker_result = None

        _worker_thread = threading.Thread(
            target=_ai_worker,
            args=(prefs_snapshot, basic_ctx, prompt),
            daemon=True,
        )
        _worker_thread.start()

        # Register a timer to poll for completion
        bpy.app.timers.register(_poll_worker_done, first_interval=0.15)

        return {'FINISHED'}


class BLENDERMENTOR_OT_clear_chat(bpy.types.Operator):
    bl_idname = "blendermentor.clear_chat"
    bl_label = "Clear Chat"
    bl_description = "Clear conversation history and steps"

    def execute(self, context):
        scene = bpy.context.scene
        scene.bm_chat_history.clear()
        scene.bm_steps.clear()
        scene.bm_current_step = 0
        scene.bm_followup_step = -1
        scene.bm_last_ai_response = ""
        scene.bm_status_message = ""
        scene.bm_is_processing = False
        clear_all_highlights()
        sync_scene_to_wm(scene)
        return {'FINISHED'}


class BLENDERMENTOR_OT_open_web_companion(bpy.types.Operator):
    bl_idname = "blendermentor.open_web_companion"
    bl_label = "Browser Companion"
    bl_description = "Open BlenderMentor in a browser window (ideal for dual monitors & voice input)"

    def execute(self, context):
        import webbrowser
        port = 8765
        url = f"http://127.0.0.1:{port}"
        webbrowser.open(url)
        self.report({'INFO'}, f"Opened BlenderMentor Companion at {url}")

        def _refresh_poll():
            from ..server import is_companion_connected
            if is_companion_connected(timeout=3.0):
                for win in bpy.context.window_manager.windows:
                    for area in win.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
                return None
            return 0.5

        bpy.app.timers.register(_refresh_poll, first_interval=0.5)
        return {'FINISHED'}




class BLENDERMENTOR_OT_hybrid_mic(bpy.types.Operator):
    """Voice Dictation: Click to toggle dictation in the Browser Companion (Alt + Up Arrow)."""
    bl_idname = "blendermentor.hybrid_mic"
    bl_label = "Voice Dictation"
    bl_description = "Toggle Voice Dictation in Browser Companion (Alt + Up Arrow)"

    def execute(self, context):
        import webbrowser
        from ..server import is_companion_connected

        scene = context.scene

        # 1. If companion browser is not connected, open it automatically and activate mic
        if not is_companion_connected(timeout=4.0):
            url = "http://127.0.0.1:8765"
            webbrowser.open(url)
            scene.bm_remote_mic_active = True
            scene.bm_remote_mic_send = False
            self.report({'INFO'}, "Opening Browser Companion for voice dictation...")
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            return {'FINISHED'}

        # 2. If already listening, stop and send
        if scene.bm_remote_mic_active:
            scene.bm_remote_mic_active = False
            scene.bm_remote_mic_send = True
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            return {'FINISHED'}

        # 3. Turn on dictation in browser
        scene.bm_remote_mic_active = True
        scene.bm_remote_mic_send = False
        scene.bm_remote_mic_abort = False
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


class BLENDERMENTOR_OT_mic_abort(bpy.types.Operator):
    """Cancel / Edit Dictation: Stop listening and keep text in input box, or clear it (Alt + Down Arrow)."""
    bl_idname = "blendermentor.mic_abort"
    bl_label = "Cancel Dictation / Clear Input"
    bl_description = "Stop listening and dump speech into box; press again to clear (Alt + Down Arrow)"

    def execute(self, context):
        scene = context.scene

        if scene.bm_remote_mic_active:
            # Active dictation -> stop listening without sending (speech stays in input box for editing!)
            scene.bm_remote_mic_active = False
            scene.bm_remote_mic_send = False
            scene.bm_remote_mic_abort = True
            self.report({'INFO'}, "Dictation stopped. Speech kept in text box for editing.")
        else:
            # Already stopped -> clear input text box
            scene.bm_remote_mic_abort = True
            scene.bm_input_text = ""
            self.report({'INFO'}, "Cleared input text.")

        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


class BLENDERMENTOR_OT_step_next(bpy.types.Operator):
    bl_idname = "blendermentor.step_next"
    bl_label = "Next"
    bl_description = "Advance to next step (Alt + Right Arrow)"

    def execute(self, context):
        scene = bpy.context.scene
        total = len(scene.bm_steps)
        if total == 0:
            return {'CANCELLED'}

        # Mark current done
        idx = scene.bm_current_step
        if 0 <= idx < total:
            scene.bm_steps[idx].is_done = True

        # Advance
        if idx < total - 1:
            scene.bm_current_step = idx + 1
            _activate_step(scene, idx + 1)
        else:
            clear_all_highlights()

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        sync_scene_to_wm(scene)
        return {'FINISHED'}


class BLENDERMENTOR_OT_step_prev(bpy.types.Operator):
    bl_idname = "blendermentor.step_prev"
    bl_label = "Prev"
    bl_description = "Go back to previous step (Alt + Left Arrow)"

    def execute(self, context):
        scene = bpy.context.scene
        idx = scene.bm_current_step
        if idx > 0:
            scene.bm_current_step = idx - 1
            _activate_step(scene, idx - 1)

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

        sync_scene_to_wm(scene)
        return {'FINISHED'}


class BLENDERMENTOR_OT_step_goto(bpy.types.Operator):
    bl_idname = "blendermentor.step_goto"
    bl_label = "Show Highlight"
    bl_description = "Highlights the interface element in Blender for this step"

    step_index: bpy.props.IntProperty()

    def execute(self, context):
        scene = bpy.context.scene
        if 0 <= self.step_index < len(scene.bm_steps):
            scene.bm_current_step = self.step_index
            _activate_step(scene, self.step_index)
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
            sync_scene_to_wm(scene)
        return {'FINISHED'}

# ---------------------------------------------------------------------------
# Follow-up question operators
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_ask_step(bpy.types.Operator):
    bl_idname = "blendermentor.ask_step"
    bl_label = "Ask About This Step"
    bl_description = "Ask a follow-up question about this step"

    step_index: bpy.props.IntProperty()

    def execute(self, context):
        scene = context.scene
        scene.bm_followup_step = self.step_index
        scene.bm_input_text = ""  # clear for fresh input
        return {'FINISHED'}


class BLENDERMENTOR_OT_cancel_followup(bpy.types.Operator):
    bl_idname = "blendermentor.cancel_followup"
    bl_label = "Cancel Follow-up"
    bl_description = "Cancel the follow-up question and return to normal input"

    def execute(self, context):
        context.scene.bm_followup_step = -1
        return {'FINISHED'}


class BLENDERMENTOR_OT_info(bpy.types.Operator):
    bl_idname = "blendermentor.info"
    bl_label = "BlenderMentor Info"
    bl_description = (
        "BlenderMentor: AI teaching assistant that guides you step-by-step through Blender.\n\n"
        "How to use:\n"
        "• Type question below & click Play / Send\n"
        "• Voice Dictate: Alt + ↑ (Option + ↑ on Mac)\n"
        "• Cancel Voice / Clear Input: Alt + ↓\n"
        "• Guided Steps: Alt + → (Next) / Alt + ← (Prev)"
    )

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=290)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="BlenderMentor", icon='INFO')
        col.separator()
        col.label(text="AI assistant teaching you Blender step-by-step.")
        col.label(text="Highlights active UI elements as you navigate.")
        col.separator()
        col.label(text="Quick Shortcuts:")
        col.label(text="• Alt + ↑ : Start / Finalize Voice")
        col.label(text="• Alt + ↓ : Cancel Voice / Clear Input")
        col.label(text="• Alt + → : Next Guided Step")
        col.label(text="• Alt + ← : Previous Guided Step")


# Dev-mode operators
class BLENDERMENTOR_OT_test_highlight(bpy.types.Operator):
    bl_idname = "blendermentor.test_highlight"
    bl_label = "Test Highlight"
    bl_description = "Fire a highlight by target name"

    def execute(self, context):
        scene = bpy.context.scene
        target = getattr(scene, 'bm_dev_highlight_target', '')
        if not target:
            return {'CANCELLED'}

        _run_dev_command(scene, target)
        return {'FINISHED'}


class BLENDERMENTOR_OT_open_youtube_search(bpy.types.Operator):
    bl_idname = "blendermentor.open_youtube_search"
    bl_label = "Open YouTube Search"
    bl_description = "Open a YouTube search for this step in your browser"

    query: bpy.props.StringProperty()

    def execute(self, context):
        import urllib.parse
        q = self.query.strip()
        if not q:
            return {'CANCELLED'}
        
        # Ensure it has "blender" in the query
        if "blender" not in q.lower():
            q = f"blender {q}"
            
        encoded_query = urllib.parse.quote_plus(q)
        url = f"https://www.youtube.com/results?search_query={encoded_query}"
        bpy.ops.wm.url_open(url=url)
        return {'FINISHED'}

class BLENDERMENTOR_OT_load_mock_steps(bpy.types.Operator):
    bl_idname = "blendermentor.load_mock_steps"
    bl_label = "Load Mock Steps"
    bl_description = "Inject test steps into the step navigator"

    def execute(self, context):
        scene = bpy.context.scene
        scene.bm_steps.clear()
        scene.bm_youtube_query = "blender subdivision surface modifier tutorial"

        mock = [
            {"instruction": "Look at the 3D Viewport",
             "description": "The 3D Viewport is the large central area where you can see and interact with your 3D objects. It usually takes up most of the Blender window.",
             "icon": "VIEW3D",
             "highlight": {"level": "area", "space": "VIEW_3D", "target": "viewport"}},
            {"instruction": "Open the Properties editor",
             "description": "The Properties editor is usually on the right side of the screen. It has a vertical strip of icons (tabs) that let you access different settings.",
             "icon": "PROPERTIES",
             "highlight": {"level": "area", "space": "PROPERTIES", "target": "properties"}},
            {"instruction": "Click the wrench icon (Modifiers tab)",
             "description": "The wrench icon is in the vertical icon strip of the Properties editor. It opens the Modifier Properties panel where you can add and manage modifiers.",
             "icon": "MODIFIER",
             "highlight": {"level": "tab", "space": "PROPERTIES", "target": "modifiers"}},
            {"instruction": "Click 'Add Modifier'",
             "description": "The 'Add Modifier' dropdown button is at the top of the Modifiers panel. Clicking it reveals categories like Generate, Deform, and Physics.",
             "icon": "ADD",
             "highlight": {"level": "panel", "space": "PROPERTIES", "target": "modifier_add_button"}},
            {"instruction": "Select Subdivision Surface from the menu",
             "description": "Subdivision Surface is inside the 'Generate' category. It smooths your mesh by subdividing its faces. Start with a viewport level of 1 or 2.",
             "icon": "MOD_SUBSURF",
             "highlight": None},
        ]

        for step_data in mock:
            s = scene.bm_steps.add()
            s.instruction = step_data["instruction"]
            s.description = step_data.get("description", "")
            s.is_done = False
            h = step_data.get("highlight")
            s.highlight_json = json.dumps(h) if h else ""
            s.icon = _resolve_step_icon(step_data.get("icon"), h, s.instruction)

        scene.bm_current_step = 0
        # Store mock response for follow-up testing
        scene.bm_last_ai_response = json.dumps({
            "summary": "Here's how to add a Subdivision Surface modifier.",
            "youtube_search_query": scene.bm_youtube_query,
            "steps": mock
        })
        if mock[0].get("highlight"):
            trigger_highlight(mock[0]["highlight"])

        sync_scene_to_wm(scene)
        self.report({'INFO'}, f"Loaded {len(mock)} mock steps.")
        return {'FINISHED'}


class BLENDERMENTOR_OT_test_stackexchange(bpy.types.Operator):
    bl_idname = "blendermentor.test_stackexchange"
    bl_label = "Test Stack Exchange"
    bl_description = "Test the Stack Exchange search API"

    def execute(self, context):
        scene = bpy.context.scene
        query = getattr(scene, 'bm_dev_web_query', '').strip()
        if not query:
            self.report({'WARNING'}, "Enter a search query first.")
            return {'CANCELLED'}

        from ..scene_reader import search_blender_community
        result = search_blender_community(query)

        reply = scene.bm_chat_history.add()
        if "error" in result:
            reply.text = f"❌ SE Error: {result['error']}"
        else:
            count = result.get('result_count', 0)
            lines = [f"🔍 Stack Exchange: {count} result(s) for '{query}'"]
            for r in result.get('results', [])[:3]:
                lines.append(f"  • [{r.get('score',0)}↑] {r.get('title','')[:60]}")
            reply.text = "\n".join(lines)
        reply.is_user = False
        self.report({'INFO'}, f"Stack Exchange search returned {result.get('result_count', 0)} results.")
        return {'FINISHED'}


class BLENDERMENTOR_OT_test_docs_fetch(bpy.types.Operator):
    bl_idname = "blendermentor.test_docs_fetch"
    bl_label = "Test Docs Fetch"
    bl_description = "Test fetching a Blender documentation page"

    def execute(self, context):
        scene = bpy.context.scene
        query = getattr(scene, 'bm_dev_web_query', '').strip()
        if not query:
            self.report({'WARNING'}, "Enter a doc page path first (e.g., 'modeling/modifiers/generate/subdivision_surface.html').")
            return {'CANCELLED'}

        from ..scene_reader import fetch_blender_docs
        result = fetch_blender_docs(query)

        reply = scene.bm_chat_history.add()
        if "error" in result:
            reply.text = f"❌ Docs Error: {result['error']}"
        else:
            content = result.get('content', '')[:200]
            reply.text = f"📚 Docs ({result.get('blender_version','?')}): {content}..."
        reply.is_user = False
        self.report({'INFO'}, f"Docs fetch: {result.get('url', 'failed')}")
        return {'FINISHED'}


class BLENDERMENTOR_OT_open_log_file(bpy.types.Operator):
    bl_idname = "blendermentor.open_log_file"
    bl_label = "Open Log File"
    bl_description = "Open blendermentor_api.log in your system default text editor"

    def execute(self, context):
        log_path = get_log_filepath()
        if not os.path.exists(log_path):
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("(BlenderMentor API Exchange Log initialized)\n")

        try:
            bpy.ops.wm.path_open(filepath=log_path)
            self.report({'INFO'}, f"Opened {os.path.basename(log_path)}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to open log file: {e}")
        return {'FINISHED'}


class BLENDERMENTOR_OT_view_in_blender_text(bpy.types.Operator):
    bl_idname = "blendermentor.view_in_blender_text"
    bl_label = "Inspect in Text Editor"
    bl_description = "Open or switch to Blender Text Editor showing BlenderMentor_Log.txt"

    def execute(self, context):
        sync_to_blender_text()
        text_block = bpy.data.texts.get("BlenderMentor_Log.txt")

        # Check if any existing area is TEXT_EDITOR
        text_area = None
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                text_area = area
                break

        if text_area:
            # Set the space's active text datablock
            for space in text_area.spaces:
                if space.type == 'TEXT_EDITOR':
                    space.text = text_block
                    break
            text_area.tag_redraw()
            self.report({'INFO'}, "Updated Blender Text Editor with latest API log.")
        else:
            # Look for an editor to split, or guide user
            self.report({'INFO'}, "Log synced to 'BlenderMentor_Log.txt'. Open a Text Editor area to view.")
        return {'FINISHED'}


class BLENDERMENTOR_OT_clear_log_file(bpy.types.Operator):
    bl_idname = "blendermentor.clear_log_file"
    bl_label = "Clear Logs"
    bl_description = "Clear blendermentor_api.log and reset in-memory exchange"

    def execute(self, context):
        clear_log()
        sync_to_blender_text(content="(Log cleared)\n")
        self.report({'INFO'}, "BlenderMentor API logs cleared.")
        return {'FINISHED'}



# ---------------------------------------------------------------------------
# Helper: follow-up prompt builder
# ---------------------------------------------------------------------------

def _build_followup_prompt(user_question, step_index, last_response_json, scene_ctx):
    """Build a prompt that includes previous context for follow-up questions."""
    return (
        f"PREVIOUS RESPONSE (for context):\n{last_response_json}\n\n"
        f"The user is currently on Step {step_index + 1} and has a follow-up question:\n"
        f"\"{user_question}\"\n\n"
        f"Please directly ANSWER the user's question in your 'summary' field. "
        f"Then, provide a NEW complete set of steps that incorporates "
        f"any needed changes or clarifications based on their question. "
        f"Set 'focus_step_index' to {step_index + 1} (or whichever step index in the new list directly addresses this query). "
        f"Do NOT repeat the previous response verbatim — adapt and improve it."
    )



# ---------------------------------------------------------------------------
# Helper: dev commands (test mode targets)
# ---------------------------------------------------------------------------

_DEV_TARGETS = {
    # Area-level
    "viewport":     {"level": "area", "space": "VIEW_3D", "target": "viewport"},
    "properties":   {"level": "area", "space": "PROPERTIES", "target": "properties"},
    "outliner":     {"level": "area", "space": "OUTLINER", "target": "outliner"},
    "timeline":     {"level": "area", "space": "DOPESHEET_EDITOR", "target": "timeline"},
    "graph_editor": {"level": "area", "space": "GRAPH_EDITOR", "target": "graph_editor"},
    # Tab-level
    "modifiers":    {"level": "tab", "space": "PROPERTIES", "target": "modifiers"},
    "render":       {"level": "tab", "space": "PROPERTIES", "target": "render"},
    "output":       {"level": "tab", "space": "PROPERTIES", "target": "output"},
    "world":        {"level": "tab", "space": "PROPERTIES", "target": "world"},
    "object":       {"level": "tab", "space": "PROPERTIES", "target": "object"},
    "particles":    {"level": "tab", "space": "PROPERTIES", "target": "particles"},
    "physics":      {"level": "tab", "space": "PROPERTIES", "target": "physics"},
    "constraints":  {"level": "tab", "space": "PROPERTIES", "target": "constraints"},
    # Region-level
    "header":       {"level": "region", "space": "VIEW_3D", "target": "header"},
    "toolbar":      {"level": "region", "space": "VIEW_3D", "target": "toolbar"},
    "sidebar":      {"level": "region", "space": "VIEW_3D", "target": "sidebar"},
    # Panel-level (menus)
    "add_menu":     {"level": "panel", "space": "VIEW_3D", "target": "add_menu"},
    "object_menu":  {"level": "panel", "space": "VIEW_3D", "target": "object_menu"},
}


def _run_dev_command(scene, text):
    """Check if text matches a known dev target. Returns True if handled."""
    cmd = text.strip().lower()
    target_data = _DEV_TARGETS.get(cmd)
    if target_data:
        trigger_highlight(target_data)
        reply = scene.bm_chat_history.add()
        reply.text = f"🔦 Highlighting: {cmd}"
        reply.is_user = False
        return True
    return False


def _activate_step(scene, index):
    """Trigger the highlight for the given step index."""
    if 0 <= index < len(scene.bm_steps):
        step = scene.bm_steps[index]
        trigger_highlight_from_json(step.highlight_json)

# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

class BLENDERMENTOR_PT_chat(bpy.types.Panel):
    bl_label = ""
    bl_idname = "BLENDERMENTOR_PT_chat"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderMentor"
    bl_order = 0

    def draw_header(self, context):
        layout = self.layout
        logo_id = get_logo_icon_id()
        if logo_id:
            layout.label(text="BlenderMentor", icon_value=logo_id)
        else:
            layout.label(text="BlenderMentor", icon='WINDOW')

    def draw_header_preset(self, context):
        layout = self.layout
        row = layout.row(align=True)

        # DEV badge
        addon_prefs = context.preferences.addons.get(__package__.rpartition('.')[0])
        if addon_prefs and addon_prefs.preferences.developer_mode:
            row.label(text="DEV", icon='TOOL_SETTINGS')

        # Information button positioned at the extreme right
        row.operator("blendermentor.info", text="", icon='INFO')

    def draw(self, context):
        layout = self.layout
        scene = bpy.context.scene
        steps = scene.bm_steps
        total = len(steps)
        current = scene.bm_current_step
        char_width = _calc_char_width(context)

        # -------------------------------------------------------------
        # 1. Connection Banner / Start BlenderMentor Button
        # -------------------------------------------------------------
        from ..server import is_companion_connected
        connected = is_companion_connected(timeout=3.0)

        if not connected:
            start_box = layout.box()
            start_col = start_box.column(align=True)
            start_col.scale_y = 1.35
            start_col.label(text="Browser Companion Not Connected", icon='INFO')
            start_col.operator(
                "blendermentor.open_web_companion",
                text="Start BlenderMentor",
                icon='NONE'
            )
            layout.separator(factor=0.5)
            return

        # -------------------------------------------------------------
        # 2. Unified Input Bar: [Mic Button (1:1 square)] [Text Field + Send]
        # (Displayed only when connected to browser companion)
        # -------------------------------------------------------------
        bar = layout.row(align=False)
        bar.scale_y = 1.35

        # 1. Mic Button (Standalone Square 1:1, to the left of the text field)
        mic_col = bar.column(align=True)
        mic_col.ui_units_x = 1.35
        mic_icon_id = get_mic_icon_id(active=scene.bm_remote_mic_active)
        if mic_icon_id:
            mic_col.operator("blendermentor.hybrid_mic", text="", icon_value=mic_icon_id)
        else:
            mic_icon = 'REC' if scene.bm_remote_mic_active else 'SOUND'
            mic_col.operator("blendermentor.hybrid_mic", text="", icon=mic_icon)

        # 2. Text input field & Send button (connected together)
        input_sub = bar.row(align=True)
        input_sub.prop(scene, "bm_input_text", text="")
        send_sub = input_sub.row(align=True)
        send_sub.scale_x = 1.2
        wm = context.window_manager
        is_processing = getattr(scene, "bm_is_processing", False) or (wm and getattr(wm, "bm_is_processing", False))
        status_msg = getattr(scene, "bm_status_message", "") or (wm and getattr(wm, "bm_status_message", "")) or "Thinking..."

        send_btn = send_sub.operator("blendermentor.send_message", text="", icon='PLAY')
        if is_processing:
            send_btn.enabled = False
        layout.separator(factor=0.3)

        # Dynamic auto-expanding card for long text (prevents truncation in N-Panel)
        input_text = scene.bm_input_text.strip()
        if input_text:
            lines = textwrap.wrap(input_text, width=max(18, char_width - 4))
            if len(lines) > 1 or len(input_text) > 20:
                text_box = layout.box()
                text_col = text_box.column(align=True)
                text_col.scale_y = 0.88
                for line in lines:
                    text_col.label(text=line)
                layout.separator(factor=0.3)

        # -------------------------------------------------------------
        # 3. Live status indicator (Thinking...)
        # Displayed right below the text box area, above the active card
        # -------------------------------------------------------------
        if is_processing:
            status_box = layout.box()
            status_col = status_box.column(align=True)
            status_col.scale_y = 1.05
            msg_lines = textwrap.wrap(status_msg, width=max(18, char_width - 4))
            for i, line in enumerate(msg_lines):
                row = status_col.row(align=True)
                row.alert = True
                if i == 0:
                    row.label(text=line, icon='TIME')
                else:
                    row.label(text=f"  {line}")
            layout.separator(factor=0.3)

        # -------------------------------------------------------------
        # 4. Step Navigator & Remote Controls (Only when steps exist)
        # -------------------------------------------------------------
        if total > 0 and 0 <= current < total:
            nav_box = layout.box()
            nav_row = nav_box.row(align=True)
            nav_row.scale_y = 1.25

            # Prev button
            prev_btn = nav_row.row(align=True)
            prev_btn.enabled = (current > 0)
            prev_btn.operator("blendermentor.step_prev", text="Prev", icon='TRIA_LEFT')

            # Step Counter
            counter_label = nav_row.row(align=True)
            counter_label.alignment = 'CENTER'
            counter_label.label(text=f"Step {current + 1} of {total}")

            # Next button
            next_btn = nav_row.row(align=True)
            next_btn.enabled = (current < total - 1)
            next_btn.operator("blendermentor.step_next", text="Next", icon='TRIA_RIGHT')

            # Visual gap / separator between navigation buttons and step action icons
            nav_row.separator(factor=1.8)

            # Step Action Buttons Group
            cur_step = steps[current]
            actions_group = nav_row.row(align=True)

            # Re-trigger UI Highlight
            if cur_step.highlight_json:
                hl_op = actions_group.operator("blendermentor.step_goto", text="", icon='LIGHT')
                hl_op.step_index = current

            # Ask follow-up about this step
            ask_op = actions_group.operator("blendermentor.ask_step", text="", icon='QUESTION')
            ask_op.step_index = current

            # Watch Tutorial video for this step (compact icon)
            if scene.bm_youtube_query:
                yt_op = actions_group.operator("blendermentor.open_youtube_search", text="", icon='URL')
                yt_op.query = scene.bm_youtube_query

            # -------------------------------------------------------------
            # 5. Active Step Card (Instruction & Context Reasoning)
            # -------------------------------------------------------------
            step = steps[current]
            card = layout.box()

            # Instruction row with Blender icon
            inst_row = card.row(align=True)
            step_icon = step.icon if (step.icon and step.icon != "NONE") else 'LAYER_ACTIVE'
            inst_lines = textwrap.wrap(step.instruction, width=max(20, char_width - 6))
            for i, line in enumerate(inst_lines):
                if i == 0:
                    inst_row.label(text=line, icon=step_icon)
                else:
                    card.label(text=f"  {line}")

            # Description / Reasoning box
            if step.description:
                desc_box = card.box()
                desc_col = desc_box.column(align=True)
                desc_lines = textwrap.wrap(step.description, width=max(20, char_width - 8))
                for line in desc_lines:
                    desc_col.label(text=line)

            # Final step completion celebration
            if current == total - 1:
                fin_box = card.box()
                fin_col = fin_box.column(align=True)
                fin_lines = textwrap.wrap("This is the last step. Hope you've achieved what you wanted!", width=max(20, char_width - 8))
                for idx_fl, fl in enumerate(fin_lines):
                    if idx_fl == 0:
                        fin_col.label(text=fl, icon='CHECKMARK')
                    else:
                        fin_col.label(text=f"  {fl}")

        # -------------------------------------------------------------
        # 5. Follow-up banner (if active)
        # -------------------------------------------------------------
        followup = scene.bm_followup_step
        if followup >= 0 and followup < len(scene.bm_steps):
            followup_box = layout.box()
            row = followup_box.row(align=True)
            row.label(text=f"Asking about Step {followup + 1}", icon='QUESTION')
            row.operator("blendermentor.cancel_followup", text="", icon='X')


class BLENDERMENTOR_PT_devtools(bpy.types.Panel):
    bl_label = "Dev Tools"
    bl_idname = "BLENDERMENTOR_PT_devtools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderMentor"
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        addon_prefs = context.preferences.addons.get(__package__.rpartition('.')[0])
        return addon_prefs and addon_prefs.preferences.developer_mode

    def draw(self, context):
        layout = self.layout
        scene = bpy.context.scene

        # --- Highlight Tester ---
        layout.label(text="Highlight Tester", icon='LIGHT')
        row = layout.row(align=True)
        row.prop(scene, "bm_dev_highlight_target", text="")
        row.operator("blendermentor.test_highlight", text="", icon='PLAY')

        targets = ", ".join(sorted(_DEV_TARGETS.keys()))
        col = layout.column(align=True)
        col.scale_y = 0.7
        for line in textwrap.wrap(f"Targets: {targets}", width=35):
            col.label(text=line)

        layout.separator()

        # --- Step Navigator Tester ---
        layout.label(text="Step Navigator Tester", icon='SEQUENCE')
        layout.operator("blendermentor.load_mock_steps", icon='FILE_NEW')

        layout.separator()

        # --- Scene Context Inspector ---
        layout.label(text="Scene Context (Base)", icon='SCENE_DATA')
        box = layout.box()
        try:
            from ..scene_reader import get_basic_context, TOOL_REGISTRY
            ctx_data = get_basic_context()
            col = box.column(align=True)
            col.scale_y = 0.7
            for key, val in ctx_data.items():
                text = f"{key}: {val}"
                for line in textwrap.wrap(text, width=35):
                    col.label(text=line)

            # Show available on-demand tools
            layout.label(text="Available Tools", icon='TOOL_SETTINGS')
            tool_box = layout.box()
            tool_col = tool_box.column(align=True)
            tool_col.scale_y = 0.7
            for name in TOOL_REGISTRY:
                tool_col.label(text=f"• {name}")
        except Exception as e:
            box.label(text=f"Error: {e}", icon='ERROR')

        layout.separator()

        # --- Web Search Tester ---
        layout.label(text="Web Search Tester", icon='URL')
        row = layout.row(align=True)
        row.prop(scene, "bm_dev_web_query", text="")
        row = layout.row(align=True)
        row.operator("blendermentor.test_stackexchange", text="Stack Exchange", icon='COMMUNITY')
        row.operator("blendermentor.test_docs_fetch", text="Docs Fetch", icon='HELP')

        layout.separator()

        # --- API Exchange Logs ---
        layout.label(text="API Exchange Logs", icon='TEXT')
        log_box = layout.box()
        col = log_box.column(align=True)
        col.scale_y = 0.7
        log_path = get_log_filepath()
        col.label(text=f"File: {os.path.basename(log_path)}")
        exists = os.path.exists(log_path)
        size_kb = os.path.getsize(log_path) / 1024.0 if exists else 0.0
        col.label(text=f"Size: {size_kb:.1f} KB")

        row = log_box.row(align=True)
        row.operator("blendermentor.open_log_file", text="Open in Editor", icon='FILE_TEXT')
        row.operator("blendermentor.view_in_blender_text", text="Blender Text", icon='TEXT')

        row_clr = log_box.row(align=True)
        row_clr.operator("blendermentor.clear_log_file", text="Clear Log File", icon='TRASH')


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _calc_char_width(context):
    """Estimate how many characters fit in the panel width."""
    try:
        ui_scale = context.preferences.view.ui_scale
        w = context.region.width / ui_scale
        return max(20, int(w / 8.5))
    except Exception:
        return 30


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    BLENDERMENTOR_OT_send_message,
    BLENDERMENTOR_OT_clear_chat,
    BLENDERMENTOR_OT_open_web_companion,
    BLENDERMENTOR_OT_hybrid_mic,
    BLENDERMENTOR_OT_mic_abort,
    BLENDERMENTOR_OT_step_next,
    BLENDERMENTOR_OT_step_prev,
    BLENDERMENTOR_OT_step_goto,
    BLENDERMENTOR_OT_ask_step,
    BLENDERMENTOR_OT_cancel_followup,
    BLENDERMENTOR_OT_info,
    BLENDERMENTOR_OT_test_highlight,
    BLENDERMENTOR_OT_open_youtube_search,
    BLENDERMENTOR_OT_load_mock_steps,
    BLENDERMENTOR_OT_test_stackexchange,
    BLENDERMENTOR_OT_test_docs_fetch,
    BLENDERMENTOR_OT_open_log_file,
    BLENDERMENTOR_OT_view_in_blender_text,
    BLENDERMENTOR_OT_clear_log_file,
    BLENDERMENTOR_PT_chat,
    BLENDERMENTOR_PT_devtools,
)




# ---------------------------------------------------------------------------
# Keymaps
# ---------------------------------------------------------------------------

_addon_keymaps = []


def register_keymaps():
    wm = bpy.context.window_manager
    kc = getattr(wm, "keyconfigs", None)
    if not kc or not getattr(kc, "addon", None):
        return

    # Add global keymap items to Window (unbound conflict-free shortcuts across all editors)
    km = kc.addon.keymaps.new(name="Window", space_type='EMPTY')

    # Next Step: Alt + Right Arrow
    kmi_next = km.keymap_items.new(
        "blendermentor.step_next",
        type='RIGHT_ARROW',
        value='PRESS',
        alt=True
    )
    _addon_keymaps.append((km, kmi_next))

    # Prev Step: Alt + Left Arrow
    kmi_prev = km.keymap_items.new(
        "blendermentor.step_prev",
        type='LEFT_ARROW',
        value='PRESS',
        alt=True
    )
    _addon_keymaps.append((km, kmi_prev))

    # Toggle Voice Dictation: Alt + Up Arrow
    kmi_mic = km.keymap_items.new(
        "blendermentor.hybrid_mic",
        type='UP_ARROW',
        value='PRESS',
        alt=True
    )
    _addon_keymaps.append((km, kmi_mic))

    # Cancel Dictation / Clear Input: Alt + Down Arrow
    kmi_abort = km.keymap_items.new(
        "blendermentor.mic_abort",
        type='DOWN_ARROW',
        value='PRESS',
        alt=True
    )
    _addon_keymaps.append((km, kmi_abort))


def unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _addon_keymaps.clear()


_last_companion_connected = False


def _poll_companion_status():
    """Poll browser companion connection status and redraw 3D Viewport when state changes."""
    global _last_companion_connected
    from ..server import is_companion_connected
    current = is_companion_connected(timeout=3.0)
    if current != _last_companion_connected:
        _last_companion_connected = current
        try:
            for win in bpy.context.window_manager.windows:
                for area in win.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
        except Exception:
            pass
    return 1.0


def register():
    # Load custom icon preview collection
    try:
        pcoll = bpy.utils.previews.new()
        icons_dir = os.path.join(os.path.dirname(__file__), "..", "icons")
        logo_path = os.path.join(icons_dir, "logo.png")
        if os.path.exists(logo_path):
            pcoll.load("logo", logo_path, 'IMAGE')
        mic_path = os.path.join(icons_dir, "mic.png")
        if os.path.exists(mic_path):
            pcoll.load("mic", mic_path, 'IMAGE')
        mic_active_path = os.path.join(icons_dir, "mic_active.png")
        if os.path.exists(mic_active_path):
            pcoll.load("mic_active", mic_active_path, 'IMAGE')
        _preview_collections["main"] = pcoll
    except Exception as e:
        print(f"[BlenderMentor] Warning: Could not load preview collection: {e}")

    for cls in _classes:
        bpy.utils.register_class(cls)

    # Register global window shortcuts (Alt+Right / Alt+Left)
    try:
        register_keymaps()
    except Exception as e:
        print(f"[BlenderMentor] Warning: Could not register keymaps: {e}")

    # Polling monitor for browser companion connection state changes
    try:
        if not bpy.app.timers.is_registered(_poll_companion_status):
            bpy.app.timers.register(_poll_companion_status, first_interval=1.0, persistent=True)
    except Exception as e:
        print(f"[BlenderMentor] Warning: Could not register companion monitor timer: {e}")

    # Dev tools properties
    bpy.types.Scene.bm_dev_highlight_target = bpy.props.StringProperty(
        name="Target", default="viewport"
    )
    bpy.types.Scene.bm_dev_web_query = bpy.props.StringProperty(
        name="Web Query", default=""
    )


def unregister():
    try:
        if bpy.app.timers.is_registered(_poll_companion_status):
            bpy.app.timers.unregister(_poll_companion_status)
    except Exception:
        pass

    unregister_keymaps()

    try:
        del bpy.types.Scene.bm_dev_highlight_target
    except Exception:
        pass
    try:
        del bpy.types.Scene.bm_dev_web_query
    except Exception:
        pass

    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

    for pcoll in _preview_collections.values():
        try:
            bpy.utils.previews.remove(pcoll)
        except Exception:
            pass
    _preview_collections.clear()
