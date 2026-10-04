# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — AI client (Gemini / Claude / Ollama) with tool-calling loop
#
# Instead of dumping all scene context upfront, this module sends a minimal
# base context and declares "tools" that the AI can call on-demand to fetch
# additional Blender state.  The agentic loop repeats until the AI produces
# the final structured step-list JSON.

import json
import ssl
import urllib.request
import urllib.error

from .scene_reader import TOOL_REGISTRY, execute_tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS = 8   # safety cap — prevent infinite loops

# ---------------------------------------------------------------------------
# System prompt — enforces structured JSON from the AI
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are BlenderMentor, an expert Blender teacher embedded directly inside Blender.

Rules:
1. NEVER perform actions in Blender yourself. Always guide the user to do it.
2. Your FINAL response must ALWAYS be formatted as a valid JSON object matching the schema below. All conversational chat, answers, explanations, and advice MUST reside inside the "summary" field. Never write commentary, markdown headers, or text outside the JSON.
3. Break your guidance into clear numbered steps. Each step must contain a single action.
4. Each step must have:
   - "instruction": a short, clear action (e.g. "Click Add Modifier")
   - "description": a brief helpful explanation with extra context (e.g. "The Add Modifier button is at the top of the Modifiers panel. It opens a dropdown with categories like Generate, Deform, and Physics.")
   - "icon": an optional Blender UI icon name to visually represent the step (e.g. "MODIFIER" for the wrench, "MOD_SUBSURF", "MOD_BEVEL", "MATERIAL", "EDITMODE_HLT", "OBJECT_DATAMODE", "LIGHT_DATA", "CAMERA_DATA", "ADD", or null)
   - "highlight": which UI element to highlight (see format below), or null if none
5. Use exact Blender UI names — panel labels, button text, menu paths.
6. Keep each step focused on one action only.
7. Use the provided scene context to tailor your response:
   - You are ALWAYS provided with the user's exact "blender_version" (e.g. '4.3.0', '5.1.0') and operating system "platform" ('macOS', 'Windows', 'Linux') in the scene context.
   - The scene context ALREADY contains rich active scene state:
     * "selected_objects": list of all selected objects and selection count.
     * "active_object_details": scale, scale_applied (boolean), dimensions, existing modifiers, materials, and vertex/face counts.
     * "viewport_state": shading type ('SOLID', 'MATERIAL', 'RENDERED', 'WIREFRAME') and overlay visibility.
     * "active_tool": active tool idname in the 3D viewport.
   - If "scale_applied" is false and the user is applying bevels, modifiers, or physics, ALWAYS guide them to apply scale first (Ctrl+A > Scale).
   - NEVER give vague or conditional advice like "depending on your version", "In Blender 4.2 or newer...", or "if you are on Mac/Windows". Speak with 100% confidence tailored specifically for their version and OS.
   - Use exact platform-appropriate menu paths (e.g. on macOS, Preferences is under "Blender > Preferences..."; on Windows/Linux it is "Edit > Preferences...").
   - In Blender 4.2 and newer, add-ons and extensions are accessed via "Get Extensions" or "Installed Extensions" in Preferences (or the Extensions menu). In pre-4.2 versions, they are under "Add-ons".
   - Use the "check_addon_status" tool if a workflow requires an add-on or extension (like Cell Fracture, Node Wrangler, Rigify, Bool Tool) to verify whether it is already enabled or installed before directing the user.
8. The "description" should provide useful context the user might not know — where to find things, what submenus look like, what an option does, etc.
9. Include a "summary" field:
   - Provide a warm, brief conversational reply (1–2 concise sentences) answering the user's question directly before they follow the steps.
   - This is shown directly in the chat window as your conversational reply.
   Example: "Sure! Here's how to add a Subdivision Surface modifier to your Cube."
10. The scene context includes "visible_editors" — a list of editor types currently
    visible on screen. If your steps reference an editor NOT in this list, you MUST
    first include a step instructing the user to open it (e.g. split an area, or
    change an existing editor's type via the editor type selector in its header).
    Only AFTER that step should you reference that editor in a highlight.
11. For follow-up or conceptual questions:
    - Answer the question directly and conversationally in the "summary" field (1–2 concise sentences).
    - If the question requires actions in Blender, provide an updated, complete step list and set "focus_step_index" to the step where the action or clarification happens.
    - If it is purely conceptual with no Blender actions needed, provide a single step summarizing the key takeaway.
12. TOOLS ARE ON-DEMAND FOR DEEP INSPECTION ONLY:
    - The initial base scene context ALREADY provides the active object's modifiers, materials, scale, dimensions, selection list, active tool, and viewport shading mode.
    - DO NOT call "get_object_details", "get_selection_info", or "get_viewport_state" if that information is already provided in the initial scene context!
    - Provide your final JSON answer directly on the FIRST round whenever possible without calling tools.
    - Call tools ONLY when strictly necessary: e.g. checking a DIFFERENT object not in the active details, inspecting specialized world lighting with "get_world_and_lighting", checking an addon with "check_addon_status", or consulting docs/community for complex third-party workflows.
    - Never call redundant tools.
    - Always provide your final JSON answer as soon as you have enough information without continuing into unnecessary tool rounds.
13. When a step involves a menu or action that has a keyboard shortcut, ALWAYS mention it.
    Format the instruction like: "Click Add in the header menu bar (or press Shift+A)".
    Common shortcuts: Shift+A (Add menu), X or Delete (delete), G (grab/move), R (rotate),
    S (scale), Tab (toggle Edit Mode), Ctrl+Z (undo), Numpad 0 (camera view).
14. If a PREVIOUS RESPONSE is included in the context, the user's new message may be a
    follow-up to that response. If the message references the previous steps or asks for
    clarification (e.g. "what does that mean?", "can I do it differently?", "why?"),
    treat it as a follow-up: answer directly in the "summary" field and provide an
    updated, complete step list. If the message is clearly a new, unrelated topic,
    treat it as a fresh question.
15. Include a root-level "youtube_search_query" field:
    - This is a single, AI-optimized YouTube search query for the overall task described by the steps (e.g., "blender subdivision surface modifier tutorial").
    - Keep it concise, relevant, and prefix it with "blender ".
16. SCENE-GROUNDED RESPONSES: When you use web search, community Q&A, or documentation
    tools, treat the results as REFERENCE MATERIAL only. Your final response MUST be
    tailored to the user's specific scene:
    - Reference the user's actual object names, modifiers, and settings.
    - Adapt generic solutions to their exact Blender version and setup.
    - Use scene context tools alongside web tools to ground your answer.
    - Never just repeat a Stack Exchange answer verbatim — translate it into
      step-by-step guidance for THIS user's scene.
    - If you use web/community tools, also call relevant scene tools (like
      get_object_details) so you can personalize the answer.
17. Include a root-level "focus_step_index" integer field (1-indexed):
    - For a new task starting from the beginning, set "focus_step_index": 1.
    - For a follow-up or revised question targeting a specific step (e.g. Step 5), set "focus_step_index" to that step number (e.g. 5) so the UI and voice readout immediately jump to that step.
18. Quotation Formatting: Inside "instruction" and "description" strings, use single quotes (e.g. 'Cube', 'Add Modifier') rather than unescaped double quotes to guarantee valid, unbroken JSON.

HIGHLIGHT LEVELS (from most precise to least precise — always pick the MOST precise level that fits):

   { "level": "panel" | "tab" | "region" | "area", "space": "<BLENDER_SPACE_TYPE>", "target": "<target_name>" }

- "panel"   — MOST PRECISE. Highlights a specific panel, menu, or header with a visual glow.
              Use this whenever the target is a known panel or menu from this list:
              modifier_add_button, material_slots, render_settings, output_settings, transform_panel,
              view3d_header, properties_header, outliner_header,
              add_menu, mesh_add_menu, curve_add_menu, surface_add_menu, light_add_menu,
              object_menu, select_menu, view_menu, shader_add_menu.
- "tab"     — Highlights a specific tab icon in the Properties NAVIGATION_BAR.
              "space" must be "PROPERTIES". "target" is one of: render, output, view_layer, scene,
              world, object, modifiers, particles, physics, constraints, object_data.
- "region"  — Highlights a specific region WITHIN an editor (header bar, toolbar, sidebar).
              "target" is one of: header, toolbar, tool_header, sidebar, n_panel.
              Use this when the user needs to look at a general area like the header menu bar,
              but there is no specific "panel" target for the exact item.
- "area"    — LEAST PRECISE. Last resort ONLY. Highlights an entire editor.
              Use ONLY when directing the user to open or look at a whole editor that is not
              yet visible (e.g. "open the Timeline editor").
              Do NOT use "area" when the user needs to click something specific within an editor.

HIGHLIGHT DECISION RULES — follow these strictly:
- When a step involves clicking a menu in the header (Add, Object, View, Select, Mesh, etc.),
  use level "panel" with the matching menu target (e.g. "add_menu", "object_menu", "view_menu").
  If no specific menu target exists, use level "region" with target "header". NEVER use "area".
- When a step involves clicking a tool in the left toolbar, use level "region" with target "toolbar".
- When a step involves the N-panel sidebar, use level "region" with target "sidebar".
- When a step involves a Properties tab icon, use level "tab".
- When a step involves a specific panel within Properties (like Add Modifier), use level "panel".
- Use level "area" ONLY for steps like "open the Outliner" or "look at the Timeline" where
  the entire editor is the target.

EXAMPLES of correct highlight usage:
  Step: "Click Add in the header menu bar" →
    CORRECT:   {"level": "panel", "space": "VIEW_3D", "target": "add_menu"}
    WRONG:     {"level": "area", "space": "VIEW_3D", "target": "viewport"}
  Step: "Select the Modifier Properties tab (wrench icon)" →
    CORRECT:   {"level": "tab", "space": "PROPERTIES", "target": "modifiers"}
  Step: "Look at the header bar of the 3D Viewport" →
    CORRECT:   {"level": "region", "space": "VIEW_3D", "target": "header"}
    WRONG:     {"level": "area", "space": "VIEW_3D", "target": "viewport"}
  Step: "Open the Outliner editor" →
    CORRECT:   {"level": "area", "space": "OUTLINER", "target": "outliner"}

Response schema:
{ "summary": "<warm, brief 1-2 sentence reply>", "focus_step_index": <int>, "youtube_search_query": "<string>", "steps": [ { "index": <int>, "instruction": "<string>", "description": "<string>", "icon": "<string|null>", "highlight": <object|null> } ] }
"""



# ---------------------------------------------------------------------------
# Tool schema builders (provider-specific formats)
# ---------------------------------------------------------------------------

def _build_gemini_tools(allow_python_eval: bool, enable_web_search: bool) -> list:
    """Build the Gemini-format tool declarations from TOOL_REGISTRY."""
    # Web tools that require network access
    _WEB_TOOLS = {"search_blender_community", "fetch_blender_docs"}

    declarations = []
    for name, entry in TOOL_REGISTRY.items():
        if name == "evaluate_python_expression" and not allow_python_eval:
            continue
        if name in _WEB_TOOLS and not enable_web_search:
            continue
        decl = {
            "name": name,
            "description": entry["description"],
            "parameters": entry["parameters"],
        }
        declarations.append(decl)

    tools = [{"function_declarations": declarations}]

    # Add native Google Search grounding tool
    if enable_web_search:
        tools.append({"google_search": {}})

    return tools


def _build_claude_tools(allow_python_eval: bool, enable_web_search: bool) -> list:
    """Build the Claude-format tool declarations from TOOL_REGISTRY."""
    _WEB_TOOLS = {"search_blender_community", "fetch_blender_docs"}

    tools = []
    for name, entry in TOOL_REGISTRY.items():
        if name == "evaluate_python_expression" and not allow_python_eval:
            continue
        if name in _WEB_TOOLS and not enable_web_search:
            continue
        tool = {
            "name": name,
            "description": entry["description"],
            "input_schema": entry["parameters"],
        }
        tools.append(tool)

    # Add native Claude web search tool
    if enable_web_search:
        tools.append({
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        })

    return tools


def _build_ollama_tools(allow_python_eval: bool, enable_web_search: bool) -> list:
    """Build the Ollama-format tool declarations from TOOL_REGISTRY.

    Note: Ollama does not support native web search, so only the
    client-side tools (Stack Exchange, docs fetch) are added when
    web search is enabled.
    """
    _WEB_TOOLS = {"search_blender_community", "fetch_blender_docs"}

    tools = []
    for name, entry in TOOL_REGISTRY.items():
        if name == "evaluate_python_expression" and not allow_python_eval:
            continue
        if name in _WEB_TOOLS and not enable_web_search:
            continue
        tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": entry["description"],
                "parameters": entry["parameters"],
            },
        }
        tools.append(tool)
    return tools


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_ai(prefs, basic_context_json: str, user_message: str,
           status_callback=None) -> dict:
    """
    Send *user_message* (with minimal scene context) to the configured AI
    provider.  The AI may call tools to fetch additional context.

    Args:
        prefs:              AddonPreferences instance
        basic_context_json: JSON string of get_basic_context()
        user_message:       The user's question or follow-up prompt
        status_callback:    Optional callable(str) invoked with status
                            messages like "Thinking...", "Fetching render
                            settings..."  Called from the worker thread.

    Returns the parsed JSON dict (with a "steps" key) on success.
    Raises on network / parsing errors.
    """
    import bpy
    if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
        raise RuntimeError("Internet access is disabled in Blender's System Preferences (Allow Internet Access).")

    provider = prefs.provider

    def _status(msg):
        if status_callback:
            status_callback(msg)

    _status("Thinking...")

    if provider == 'GEMINI':
        raw = _gemini_tool_loop(prefs, basic_context_json, user_message, _status)
    elif provider == 'CLAUDE':
        raw = _claude_tool_loop(prefs, basic_context_json, user_message, _status)
    elif provider == 'OLLAMA':
        raw = _ollama_tool_loop(prefs, basic_context_json, user_message, _status)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    _status("Processing response...")
    return _parse_response(raw)


def list_models(provider: str, api_key: str) -> list:
    """Fetch available models from the provider. Returns [(id, name), ...]."""
    from .preferences import BLENDERMENTOR_OT_fetch_models
    return BLENDERMENTOR_OT_fetch_models._fetch(provider, api_key)


# ---------------------------------------------------------------------------
# Gemini — tool-calling loop
# ---------------------------------------------------------------------------

def _gemini_tool_loop(prefs, base_ctx: str, prompt: str,
                      status_cb) -> str:
    """Run the Gemini generateContent loop with tool calling."""
    ctx = ssl.create_default_context()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{prefs.get_selected_model_id()}:generateContent?key={prefs.api_key}"
    )

    allow_python_eval = getattr(prefs, 'allow_python_eval', False)
    enable_web_search = getattr(prefs, 'enable_web_search', True)
    tools = _build_gemini_tools(allow_python_eval, enable_web_search)

    # Build initial conversation
    contents = [
        {"role": "user", "parts": [{"text": f"Scene context:\n{base_ctx}\n\nUser question:\n{prompt}"}]},
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": tools,
            "generationConfig": {
                "maxOutputTokens": 8192,
            },
        }).encode()

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[BlenderMentor] Gemini API HTTP {e.code} error: {err_body}")
            raise RuntimeError(f"Gemini API Error ({e.code}): {err_body}")

        # Extract model response parts
        try:
            candidate = data["candidates"][0]["content"]
            parts = candidate["parts"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Gemini response structure: {e}")

        # Check if any part is a function call
        function_calls = [p for p in parts if "functionCall" in p]

        if not function_calls:
            # No tool calls — extract text response (filter out thought parts)
            text_parts = [
                p.get("text", "") for p in parts
                if "text" in p and not p.get("thought", False)
            ]
            return "".join(text_parts)

        # Append model response to conversation
        contents.append({"role": "model", "parts": parts})

        # Execute each tool call and build function responses
        func_response_parts = []
        for fc_part in function_calls:
            fc = fc_part["functionCall"]
            tool_name = fc["name"]
            tool_args = fc.get("args", {})
            call_id = fc.get("id")

            # Update status
            friendly = tool_name.replace("get_", "").replace("_", " ").title()
            # Friendlier status for web tools
            if tool_name == "search_blender_community":
                status_cb("Searching community Q&A...")
            elif tool_name == "fetch_blender_docs":
                status_cb("Fetching Blender docs...")
            else:
                status_cb(f"Checking {friendly}...")

            # Execute the tool
            result = execute_tool(tool_name, tool_args)

            fr = {
                "functionResponse": {
                    "name": tool_name,
                    "response": result,
                }
            }
            if call_id:
                fr["functionResponse"]["id"] = call_id

            func_response_parts.append(fr)

        # Append tool results to conversation
        contents.append({"role": "function", "parts": func_response_parts})

    # Graceful synthesis fallback: force final text response without tools
    fallback_err = None
    try:
        status_cb("Finalizing guidance...")
        # In Gemini API, the function response turn must be answered by model next.
        # Calling without tools forces Gemini to output model text!
        body = json.dumps({
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 8192},
            # tools omitted to force final text generation
        }).encode()

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[BlenderMentor] Gemini fallback HTTP {e.code} error: {err_body}")
            fallback_err = f"{e.code} - {err_body}"
            raise

        candidate = data["candidates"][0]["content"]
        parts = candidate.get("parts", [])
        text_parts = [
            p.get("text", "") for p in parts
            if "text" in p and not p.get("thought", False)
        ]
        if text_parts:
            return "".join(text_parts)
    except Exception as e:
        fallback_err = fallback_err or e
        print(f"[BlenderMentor] Gemini fallback synthesis error: {e}")

    err_detail = f": {fallback_err}" if fallback_err else ""
    raise RuntimeError(f"AI was unable to complete guidance within the allowed tool rounds{err_detail}.")


# ---------------------------------------------------------------------------
# Claude — tool-calling loop
# ---------------------------------------------------------------------------

def _claude_tool_loop(prefs, base_ctx: str, prompt: str,
                      status_cb) -> str:
    """Run the Claude messages loop with tool calling."""
    ctx = ssl.create_default_context()
    url = "https://api.anthropic.com/v1/messages"

    allow_python_eval = getattr(prefs, 'allow_python_eval', False)
    enable_web_search = getattr(prefs, 'enable_web_search', True)
    tools = _build_claude_tools(allow_python_eval, enable_web_search)

    messages = [
        {"role": "user", "content": f"Scene context:\n{base_ctx}\n\nUser question:\n{prompt}"},
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        body = json.dumps({
            "model": prefs.get_selected_model_id(),
            "max_tokens": 8192,
            "system": SYSTEM_PROMPT,
            "tools": tools,
            "messages": messages,
        }).encode()

        req = urllib.request.Request(url, data=body, headers={
            "x-api-key": prefs.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[BlenderMentor] Claude API HTTP {e.code} error: {err_body}")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message", err_body)
                raise RuntimeError(f"Claude API Error ({e.code}): {msg}")
            except Exception:
                raise RuntimeError(f"Claude API Error ({e.code}): {err_body}")

        stop_reason = data.get("stop_reason", "")
        content_blocks = data.get("content", [])

        # Check if AI wants to use tools
        if stop_reason != "tool_use":
            # Final response — extract text
            text_parts = [
                block["text"]
                for block in content_blocks
                if block.get("type") == "text"
            ]
            return "".join(text_parts)

        # Append assistant message (with tool_use blocks)
        messages.append({"role": "assistant", "content": content_blocks})

        # Execute each tool call
        tool_results = []
        for block in content_blocks:
            if block.get("type") != "tool_use":
                continue

            tool_name = block["name"]
            tool_args = block.get("input", {})
            tool_id = block["id"]

            # Update status
            friendly = tool_name.replace("get_", "").replace("_", " ").title()
            if tool_name == "search_blender_community":
                status_cb("Searching community Q&A...")
            elif tool_name == "fetch_blender_docs":
                status_cb("Fetching Blender docs...")
            else:
                status_cb(f"Checking {friendly}...")

            result = execute_tool(tool_name, tool_args)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(result),
            })

        # Append tool results as a user message (never append empty content: [])
        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            messages.append({
                "role": "user",
                "content": [{"type": "text", "text": "Please continue with the gathered information."}]
            })

    # Graceful synthesis fallback: force final text response without tools
    fallback_err = None
    try:
        status_cb("Finalizing guidance...")
        # In Anthropic API, roles must strictly alternate: append text to the EXISTING user turn
        instruction_block = {
            "type": "text",
            "text": "You have gathered all needed information. Please synthesize your final response now as a valid JSON object matching the required schema. Do not call any further tools."
        }
        if messages and messages[-1].get("role") == "user":
            user_content = messages[-1].get("content")
            if isinstance(user_content, list):
                if user_content:
                    user_content.append(instruction_block)
                else:
                    messages[-1]["content"] = [instruction_block]
            else:
                messages[-1]["content"] = [
                    {"type": "text", "text": str(user_content or "Please proceed.")},
                    instruction_block
                ]
        else:
            messages.append({
                "role": "user",
                "content": [instruction_block]
            })

        body = json.dumps({
            "model": prefs.get_selected_model_id(),
            "max_tokens": 8192,
            "system": SYSTEM_PROMPT,
            "tools": tools,
            "tool_choice": {"type": "none"},
            "messages": messages,
            "thinking": {
                "type": "adaptive",
                "block_binding": {
                    "prefix_mismatch_behavior": "drop_block"
                }
            }
        }).encode()

        req = urllib.request.Request(url, data=body, headers={
            "x-api-key": prefs.api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "thinking-binding-controls-2026-08-01",
            "content-type": "application/json",
        }, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"[BlenderMentor] Claude fallback HTTP {e.code} error: {err_body}")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("error", {}).get("message", err_body)
                fallback_err = f"{e.code} - {msg}"
            except Exception:
                fallback_err = f"{e.code} - {err_body}"
            raise

        content_blocks = data.get("content", [])
        text_parts = [
            block["text"]
            for block in content_blocks
            if block.get("type") == "text"
        ]
        if text_parts:
            return "".join(text_parts)
    except Exception as e:
        fallback_err = fallback_err or e
        print(f"[BlenderMentor] Claude fallback synthesis error: {e}")

    err_detail = f": {fallback_err}" if fallback_err else ""
    raise RuntimeError(f"AI was unable to complete guidance within the allowed tool rounds{err_detail}.")


# ---------------------------------------------------------------------------
# Ollama — tool-calling loop
# ---------------------------------------------------------------------------

def _ollama_tool_loop(prefs, base_ctx: str, prompt: str,
                      status_cb) -> str:
    """Run the Ollama chat loop with tool calling."""
    host = getattr(prefs, 'ollama_host', 'http://localhost:11434').rstrip("/")
    url = f"{host}/api/chat"

    allow_python_eval = getattr(prefs, 'allow_python_eval', False)
    enable_web_search = getattr(prefs, 'enable_web_search', True)
    tools = _build_ollama_tools(allow_python_eval, enable_web_search)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Scene context:\n{base_ctx}\n\nUser question:\n{prompt}"},
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        body = json.dumps({
            "model": prefs.get_selected_model_id(),
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
            },
        }).encode()

        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot reach Ollama at {host}. Is Ollama running? ({e.reason})"
            )

        msg = data.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            # Final response — return text content
            content = msg.get("content", "")
            if content:
                return content
            raise RuntimeError("Ollama returned empty response with no tool calls.")

        # Append assistant message to conversation
        messages.append(msg)

        # Execute each tool call
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            tool_args = func.get("arguments", {})

            # Update status
            friendly = tool_name.replace("get_", "").replace("_", " ").title()
            if tool_name == "search_blender_community":
                status_cb("Searching community Q&A...")
            elif tool_name == "fetch_blender_docs":
                status_cb("Fetching Blender docs...")
            else:
                status_cb(f"Checking {friendly}...")

            result = execute_tool(tool_name, tool_args)

            # Append tool response
            messages.append({
                "role": "tool",
                "content": json.dumps(result),
            })

    # Graceful synthesis fallback: force final text response without tools
    try:
        status_cb("Finalizing guidance...")
        messages.append({
            "role": "user",
            "content": "You have gathered all needed information. Please synthesize your final response now as a valid JSON object matching the required schema. Do not call any further tools."
        })
        body = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{host}/api/chat", data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
        content = data.get("message", {}).get("content", "")
        if content:
            return content
    except Exception as e:
        fallback_err = e
        print(f"[BlenderMentor] Ollama fallback synthesis error: {e}")

    err_detail = f": {fallback_err}" if fallback_err else ""
    raise RuntimeError(f"AI was unable to complete guidance within the allowed tool rounds{err_detail}.")


# ---------------------------------------------------------------------------
# JSON parsing — robust brace-counting extractor
# ---------------------------------------------------------------------------

def _extract_outermost_json(text: str) -> str:
    """Walk the string and find the first balanced { … } block, repairing truncation if needed."""
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in AI response.")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    # Attempt a repair on truncated JSON (if cut off mid-response)
    candidate = text[start:].strip()
    if in_string:
        candidate += '"'
    candidate += "}" * depth
    try:
        json.loads(candidate)
        return candidate
    except Exception:
        pass

    raise ValueError("Unbalanced braces in AI response JSON.")


def _parse_response(raw_text: str) -> dict:
    """Parse the AI's raw text into a validated steps dict with graceful fallback."""
    raw_text = raw_text.strip()
    data = None

    try:
        json_str = _extract_outermost_json(raw_text)
        data = json.loads(json_str)
    except Exception:
        # Graceful fallback: treat non-JSON conversational text as the chat summary
        import re
        clean_text = raw_text.replace("```json", "").replace("```", "").strip()
        lines = [line.strip() for line in clean_text.splitlines() if line.strip()]

        steps = []
        step_idx = 1
        summary_lines = []

        for line in lines:
            m = re.match(r"^(\d+)[\.\)]\s*(.*)", line)
            if m:
                instruction = m.group(2).strip()
                steps.append({
                    "index": step_idx,
                    "instruction": instruction,
                    "description": "",
                    "icon": "INFO",
                    "highlight": None
                })
                step_idx += 1
            elif not steps:
                summary_lines.append(line)

        summary = " ".join(summary_lines).strip()
        if not summary:
            summary = clean_text[:200]

        if not steps:
            steps = [{
                "index": 1,
                "instruction": summary[:80] + ("..." if len(summary) > 80 else ""),
                "description": clean_text,
                "icon": "INFO",
                "highlight": None
            }]

        data = {
            "summary": summary,
            "focus_step_index": 1,
            "youtube_search_query": "",
            "steps": steps
        }

    if "steps" not in data or not isinstance(data["steps"], list) or len(data["steps"]) == 0:
        data["steps"] = [{
            "index": 1,
            "instruction": data.get("summary", "Guidance ready")[:80],
            "description": data.get("summary", ""),
            "icon": "INFO",
            "highlight": None
        }]

    # Ensure summary field exists
    if "summary" not in data or not data["summary"]:
        data["summary"] = f"{len(data['steps'])} steps ready — see below!"

    # Ensure focus_step_index exists
    if "focus_step_index" not in data or not isinstance(data["focus_step_index"], int):
        data["focus_step_index"] = 1

    # Ensure youtube_search_query exists at the root
    if "youtube_search_query" not in data:
        data["youtube_search_query"] = ""

    # Validate each step minimally
    for step in data["steps"]:
        if "instruction" not in step:
            step["instruction"] = step.get("text", "(no instruction)")
        if "description" not in step:
            step["description"] = ""
        if "index" not in step:
            step["index"] = data["steps"].index(step) + 1

    return data

