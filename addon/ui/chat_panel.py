# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Chat panel, step navigator, and dev tools

import bpy
import json
import textwrap

from ..ai_client import ask_ai
from ..scene_reader import get_scene_context_json, get_scene_context
from .highlight import (
    trigger_highlight_from_json,
    clear_all_highlights,
    trigger_highlight,
)


class BLENDERMENTOR_OT_send_message(bpy.types.Operator):
    bl_idname = "blendermentor.send_message"
    bl_label = "Send"
    bl_description = "Send your message to the AI mentor"

    def execute(self, context):
        scene = bpy.context.scene
        user_text = scene.bm_input_text.strip()
        if not user_text:
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

        # Dev mode: handle test commands
        addon_prefs = context.preferences.addons.get(__package__.split('.')[0])
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
            return {'FINISHED'}

        # Get AI preferences
        if not addon_prefs:
            reply = scene.bm_chat_history.add()
            reply.text = "⚠ Please configure BlenderMentor in Edit → Preferences → Add-ons."
            reply.is_user = False
            scene.bm_followup_step = -1
            return {'FINISHED'}

        prefs = addon_prefs.preferences
        if prefs.provider != 'OLLAMA' and not prefs.api_key:
            reply = scene.bm_chat_history.add()
            reply.text = "⚠ No API key set. Go to Edit → Preferences → Add-ons → BlenderMentor."
            reply.is_user = False
            scene.bm_followup_step = -1
            return {'FINISHED'}

        # Call AI
        try:
            scene_ctx = get_scene_context_json()

            # Build prompt — follow-up or fresh
            if followup_step >= 0 and last_response:
                prompt = _build_followup_prompt(
                    user_text, followup_step, last_response, scene_ctx
                )
            else:
                prompt = user_text

            response = ask_ai(prefs, scene_ctx, prompt)
            steps = response.get("steps", [])

            if steps:
                # Populate step navigator
                scene.bm_steps.clear()
                for step_data in steps:
                    s = scene.bm_steps.add()
                    s.instruction = step_data.get("instruction", "")
                    s.description = step_data.get("description", "")
                    s.is_done = False
                    highlight = step_data.get("highlight")
                    s.highlight_json = json.dumps(highlight) if highlight else ""

                scene.bm_current_step = 0

                # Show friendly summary in chat
                summary = response.get("summary", f"{len(steps)} steps ready — see below!")
                reply = scene.bm_chat_history.add()
                reply.text = f"💡 {summary}"
                reply.is_user = False

                # Store response for potential follow-ups
                scene.bm_last_ai_response = json.dumps(response)

                # Trigger first step highlight
                if steps[0].get("highlight"):
                    trigger_highlight(steps[0]["highlight"])
            else:
                reply = scene.bm_chat_history.add()
                reply.text = "The AI did not return any steps. Please try again."
                reply.is_user = False

        except Exception as e:
            reply = scene.bm_chat_history.add()
            reply.text = f"❌ Error: {str(e)}"
            reply.is_user = False

        # Reset follow-up state
        scene.bm_followup_step = -1

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
        clear_all_highlights()
        return {'FINISHED'}


class BLENDERMENTOR_OT_step_next(bpy.types.Operator):
    bl_idname = "blendermentor.step_next"
    bl_label = "Next"
    bl_description = "Mark current step done and advance to next"

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

        return {'FINISHED'}


class BLENDERMENTOR_OT_step_prev(bpy.types.Operator):
    bl_idname = "blendermentor.step_prev"
    bl_label = "Prev"
    bl_description = "Go back to previous step"

    def execute(self, context):
        scene = bpy.context.scene
        idx = scene.bm_current_step
        if idx > 0:
            scene.bm_current_step = idx - 1
            _activate_step(scene, idx - 1)
        return {'FINISHED'}


class BLENDERMENTOR_OT_step_goto(bpy.types.Operator):
    bl_idname = "blendermentor.step_goto"
    bl_label = "Show Highlight"
    bl_description = "Show the highlight for this step"

    step_index: bpy.props.IntProperty()

    def execute(self, context):
        scene = bpy.context.scene
        if 0 <= self.step_index < len(scene.bm_steps):
            scene.bm_current_step = self.step_index
            _activate_step(scene, self.step_index)
        return {'FINISHED'}


class BLENDERMENTOR_OT_mark_all_done(bpy.types.Operator):
    bl_idname = "blendermentor.mark_all_done"
    bl_label = "Mark All Done"
    bl_description = "Mark all steps as complete"

    def execute(self, context):
        scene = bpy.context.scene
        for step in scene.bm_steps:
            step.is_done = True
        clear_all_highlights()
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


class BLENDERMENTOR_OT_load_mock_steps(bpy.types.Operator):
    bl_idname = "blendermentor.load_mock_steps"
    bl_label = "Load Mock Steps"
    bl_description = "Inject test steps into the step navigator"

    def execute(self, context):
        scene = bpy.context.scene
        scene.bm_steps.clear()

        mock = [
            {"instruction": "Look at the 3D Viewport",
             "description": "The 3D Viewport is the large central area where you can see and interact with your 3D objects. It usually takes up most of the Blender window.",
             "highlight": {"level": "area", "space": "VIEW_3D", "target": "viewport"}},
            {"instruction": "Open the Properties editor",
             "description": "The Properties editor is usually on the right side of the screen. It has a vertical strip of icons (tabs) that let you access different settings.",
             "highlight": {"level": "area", "space": "PROPERTIES", "target": "properties"}},
            {"instruction": "Click the wrench icon (Modifiers tab)",
             "description": "The wrench icon is in the vertical icon strip of the Properties editor. It opens the Modifier Properties panel where you can add and manage modifiers.",
             "highlight": {"level": "tab", "space": "PROPERTIES", "target": "modifiers"}},
            {"instruction": "Click 'Add Modifier'",
             "description": "The 'Add Modifier' dropdown button is at the top of the Modifiers panel. Clicking it reveals categories like Generate, Deform, and Physics.",
             "highlight": {"level": "panel", "space": "PROPERTIES", "target": "modifier_add_button"}},
            {"instruction": "Select Subdivision Surface from the menu",
             "description": "Subdivision Surface is inside the 'Generate' category. It smooths your mesh by subdividing its faces. Start with a viewport level of 1 or 2.",
             "highlight": None},
        ]

        for step_data in mock:
            s = scene.bm_steps.add()
            s.instruction = step_data["instruction"]
            s.description = step_data.get("description", "")
            s.is_done = False
            h = step_data.get("highlight")
            s.highlight_json = json.dumps(h) if h else ""

        scene.bm_current_step = 0
        # Store mock response for follow-up testing
        scene.bm_last_ai_response = json.dumps({
            "summary": "Here's how to add a Subdivision Surface modifier.",
            "steps": mock
        })
        if mock[0].get("highlight"):
            trigger_highlight(mock[0]["highlight"])

        self.report({'INFO'}, f"Loaded {len(mock)} mock steps.")
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
        f"Do NOT repeat the previous response verbatim — adapt and improve it."
    )


# ---------------------------------------------------------------------------
# Helper: dev commands (test mode targets)
# ---------------------------------------------------------------------------

_DEV_TARGETS = {
    "viewport":     {"level": "area", "space": "VIEW_3D", "target": "viewport"},
    "properties":   {"level": "area", "space": "PROPERTIES", "target": "properties"},
    "outliner":     {"level": "area", "space": "OUTLINER", "target": "outliner"},
    "timeline":     {"level": "area", "space": "DOPESHEET_EDITOR", "target": "timeline"},
    "graph_editor": {"level": "area", "space": "GRAPH_EDITOR", "target": "graph_editor"},
    "modifiers":    {"level": "tab", "space": "PROPERTIES", "target": "modifiers"},
    "render":       {"level": "tab", "space": "PROPERTIES", "target": "render"},
    "output":       {"level": "tab", "space": "PROPERTIES", "target": "output"},
    "world":        {"level": "tab", "space": "PROPERTIES", "target": "world"},
    "object":       {"level": "tab", "space": "PROPERTIES", "target": "object"},
    "particles":    {"level": "tab", "space": "PROPERTIES", "target": "particles"},
    "physics":      {"level": "tab", "space": "PROPERTIES", "target": "physics"},
    "constraints":  {"level": "tab", "space": "PROPERTIES", "target": "constraints"},
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
    bl_label = "BlenderMentor"
    bl_idname = "BLENDERMENTOR_PT_chat"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderMentor"
    bl_order = 0

    def draw_header(self, context):
        layout = self.layout
        row = layout.row(align=True)

        # Pop-out / dock buttons in the header
        row.operator("blendermentor.popout", text="", icon='WINDOW')

        is_docked = context.scene.get("bm_is_docked", False)
        if is_docked:
            row.operator("blendermentor.undock", text="", icon='PANEL_CLOSE')
        else:
            row.operator("blendermentor.dock", text="", icon='SNAP_PEEL_OBJECT')

        # DEV badge
        addon_prefs = context.preferences.addons.get(__package__.split('.')[0])
        if addon_prefs and addon_prefs.preferences.developer_mode:
            row.label(text="DEV", icon='TOOL_SETTINGS')

    def draw(self, context):
        layout = self.layout
        scene = bpy.context.scene

        # --- Chat history ---
        char_width = _calc_char_width(context)

        box = layout.box()
        if len(scene.bm_chat_history) == 0:
            box.label(text="Ask me anything about Blender!", icon='LIGHT')
        else:
            col = box.column(align=True)
            for msg in scene.bm_chat_history:
                prefix = "You" if msg.is_user else "Mentor"
                icon = 'USER' if msg.is_user else 'OUTLINER_OB_LIGHT'

                # Wrap text
                lines = textwrap.wrap(msg.text, width=char_width) or [msg.text]
                for i, line in enumerate(lines):
                    if i == 0:
                        col.label(text=f"{prefix}: {line}", icon=icon)
                    else:
                        col.label(text=f"  {line}")
                col.separator(factor=0.3)

        # --- Follow-up indicator ---
        followup = scene.bm_followup_step
        if followup >= 0 and followup < len(scene.bm_steps):
            followup_box = layout.box()
            row = followup_box.row(align=True)
            row.label(text=f"Asking about Step {followup + 1}",
                      icon='QUESTION')
            row.operator("blendermentor.cancel_followup", text="", icon='X')

        # --- Input + Send ---
        row = layout.row(align=True)
        row.prop(scene, "bm_input_text", text="")
        row.operator("blendermentor.send_message", text="", icon='PLAY')

        # --- Clear button ---
        layout.operator("blendermentor.clear_chat", text="Clear", icon='TRASH')


class BLENDERMENTOR_PT_steps(bpy.types.Panel):
    bl_label = "Guided Steps"
    bl_idname = "BLENDERMENTOR_PT_steps"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BlenderMentor"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        scene = bpy.context.scene
        steps = scene.bm_steps
        total = len(steps)
        char_width = _calc_char_width(context)

        if total == 0:
            layout.label(text="No steps yet. Ask a question above!", icon='INFO')
            return

        current = scene.bm_current_step
        done_count = sum(1 for s in steps if s.is_done)

        # Progress bar
        row = layout.row()
        row.label(text=f"Step {current + 1} of {total}  ({done_count} done)",
                  icon='SEQUENCE')

        layout.separator(factor=0.5)

        # List all steps
        for i, step in enumerate(steps):
            is_current = (i == current)
            is_done = step.is_done

            # Choose icon
            if is_done:
                icon = 'CHECKMARK'
            elif is_current:
                icon = 'LAYER_ACTIVE'
            else:
                icon = 'LAYER_USED'

            # Step box
            step_box = layout.box()
            if is_current and not is_done:
                step_box.alert = True  # red tint for active step

            # Header row: icon + step number + ask button + goto button
            header = step_box.row(align=True)
            header.label(text="", icon=icon)
            header.label(text=f"Step {i + 1}")

            # Ask follow-up button (always available on each step)
            ask_op = header.operator("blendermentor.ask_step",
                                     text="", icon='QUESTION')
            ask_op.step_index = i

            # Highlight button (available if there is a highlight)
            if step.highlight_json and step.highlight_json != "null":
                op = header.operator("blendermentor.step_goto",
                                     text="", icon='LIGHT')
                op.step_index = i

            # Instruction text (wrapped)
            col = step_box.column(align=True)
            lines = textwrap.wrap(step.instruction, width=max(20, char_width - 6))
            for line in lines:
                col.label(text=line)

            # Description text (dimmer, shown below instruction)
            if step.description:
                desc_col = step_box.column(align=True)
                desc_col.scale_y = 0.8
                desc_lines = textwrap.wrap(step.description, width=max(20, char_width - 6))
                for dline in desc_lines:
                    desc_col.label(text=dline, icon='BLANK1')

        # Navigation buttons
        layout.separator(factor=0.5)
        nav = layout.row(align=True)
        nav.scale_y = 1.4
        sub = nav.row(align=True)
        sub.enabled = (current > 0)
        sub.operator("blendermentor.step_prev", text="◀ Prev", icon='TRIA_LEFT')

        sub2 = nav.row(align=True)
        sub2.enabled = (current < total - 1)
        sub2.operator("blendermentor.step_next", text="Next ▶", icon='TRIA_RIGHT')

        # Mark all done
        layout.operator("blendermentor.mark_all_done", icon='CHECKBOX_HLT')


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
        addon_prefs = context.preferences.addons.get(__package__.split('.')[0])
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
        layout.label(text="Scene Context", icon='SCENE_DATA')
        box = layout.box()
        try:
            ctx_data = get_scene_context()
            col = box.column(align=True)
            col.scale_y = 0.7
            for key, val in ctx_data.items():
                text = f"{key}: {val}"
                for line in textwrap.wrap(text, width=35):
                    col.label(text=line)
        except Exception as e:
            box.label(text=f"Error: {e}", icon='ERROR')


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
    BLENDERMENTOR_OT_step_next,
    BLENDERMENTOR_OT_step_prev,
    BLENDERMENTOR_OT_step_goto,
    BLENDERMENTOR_OT_mark_all_done,
    BLENDERMENTOR_OT_ask_step,
    BLENDERMENTOR_OT_cancel_followup,
    BLENDERMENTOR_OT_test_highlight,
    BLENDERMENTOR_OT_load_mock_steps,
    BLENDERMENTOR_PT_chat,
    BLENDERMENTOR_PT_steps,
    BLENDERMENTOR_PT_devtools,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    # Dev tools property
    bpy.types.Scene.bm_dev_highlight_target = bpy.props.StringProperty(
        name="Target", default="viewport"
    )

def unregister():
    try:
        del bpy.types.Scene.bm_dev_highlight_target
    except Exception:
        pass

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
