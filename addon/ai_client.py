# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — AI client (Gemini / Claude / Ollama calls + JSON parsing)

import json
import ssl
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# System prompt — enforces structured JSON from the AI
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are BlenderMentor, an expert Blender teacher embedded directly inside Blender.

Rules:
1. NEVER perform actions in Blender yourself. Always guide the user to do it.
2. Respond ONLY with a valid JSON object matching the schema below. No prose, no markdown, no explanation outside the JSON.
3. Break your guidance into clear numbered steps. Each step must contain a single action.
4. Each step must have:
   - "instruction": a short, clear action (e.g. "Click Add Modifier")
   - "description": a brief helpful explanation with extra context (e.g. "The Add Modifier button is at the top of the Modifiers panel. It opens a dropdown with categories like Generate, Deform, and Physics.")
   - "highlight": which UI element to highlight (see format below), or null if none
5. Use exact Blender UI names — panel labels, button text, menu paths.
6. Keep each step focused on one action only.
7. Use the provided scene context to tailor your response.
8. The "description" should provide useful context the user might not know — where to find things, what submenus look like, what an option does, etc.
9. Include a "summary" field:
   - For a normal request, provide a friendly sentence summarizing what you will help with.
   - For a follow-up question, use this field to DIRECTLY ANSWER the user's question.
   This is shown directly in the chat window as a conversational reply.
   Example: "Sure! Here's how to add a Subdivision Surface modifier to your Cube."
10. The scene context includes "visible_editors" — a list of editor types currently
    visible on screen. If your steps reference an editor NOT in this list, you MUST
    first include a step instructing the user to open it (e.g. split an area, or
    change an existing editor's type via the editor type selector in its header).
    Only AFTER that step should you reference that editor in a highlight.
11. When a follow-up question is provided with a previous response, answer the question
    directly in the "summary" field. Then, provide an updated, complete step list that
    incorporates the clarification or new requirements. Even if the user just asks a
    conceptual question, always provide the full steps for the task.

Valid "highlight" values:
   { "level": "area" | "tab" | "panel", "space": "<BLENDER_SPACE_TYPE>", "target": "<target_name>" }
- "area"  — highlight an entire editor area. "space" is the Blender space type (VIEW_3D, PROPERTIES, OUTLINER, DOPESHEET_EDITOR, GRAPH_EDITOR, NODE_EDITOR, IMAGE_EDITOR, SEQUENCE_EDITOR, TEXT_EDITOR). "target" is a human-readable name.
- "tab"   — highlight a specific tab icon in the Properties NAVIGATION_BAR. "space" must be "PROPERTIES". "target" is one of: render, output, view_layer, scene, world, object, modifiers, particles, physics, constraints, object_data.
- "panel" — highlight a specific panel or button inside an editor. "space" is the editor type. "target" is a descriptive name like "modifier_add_button".

Response schema:
{ "summary": "<friendly one-liner>", "steps": [ { "index": <int>, "instruction": "<string>", "description": "<string>", "highlight": <object|null> } ] }
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_ai(prefs, scene_context_json: str, user_message: str) -> dict:
    """
    Send *user_message* (with scene context) to the configured AI provider.

    Returns the parsed JSON dict (with a "steps" key) on success.
    Raises on network / parsing errors.
    """
    provider = prefs.provider
    api_key = prefs.api_key
    model_id = prefs.get_selected_model_id()

    prompt = (
        f"Scene context:\n{scene_context_json}\n\n"
        f"User question:\n{user_message}"
    )

    if provider == 'GEMINI':
        raw = _call_gemini(api_key, model_id, prompt)
    elif provider == 'CLAUDE':
        raw = _call_claude(api_key, model_id, prompt)
    elif provider == 'OLLAMA':
        ollama_host = getattr(prefs, 'ollama_host', 'http://localhost:11434')
        raw = _call_ollama(ollama_host, model_id, prompt)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return _parse_response(raw)


def list_models(provider: str, api_key: str) -> list:
    """Fetch available models from the provider. Returns [(id, name), ...]."""
    # Delegated to preferences.py operators; kept here as a convenience stub.
    from .preferences import BLENDERMENTOR_OT_fetch_models
    return BLENDERMENTOR_OT_fetch_models._fetch(provider, api_key)


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _call_gemini(api_key: str, model_id: str, prompt: str) -> str:
    ctx = ssl.create_default_context()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{model_id}:generateContent?key={api_key}"
    )
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    # Extract text from Gemini response
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response structure: {e}")


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _call_claude(api_key: str, model_id: str, prompt: str) -> str:
    ctx = ssl.create_default_context()
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps({
        "model": model_id,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        data = json.loads(resp.read().decode())

    # Extract text from Claude response
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Claude response structure: {e}")


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

def _call_ollama(host: str, model_id: str, prompt: str) -> str:
    host = (host or "http://localhost:11434").rstrip("/")
    url = f"{host}/api/chat"
    body = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,   # keep structured output more deterministic
            "num_predict": 4096,  # allow enough tokens for multi-step JSON
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

    # Ollama chat response: {"message": {"role": "assistant", "content": "..."}}
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Unexpected Ollama response structure: {e}")


# ---------------------------------------------------------------------------
# JSON parsing — robust brace-counting extractor
# ---------------------------------------------------------------------------

def _extract_outermost_json(text: str) -> str:
    """Walk the string and find the first balanced { … } block."""
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

    raise ValueError("Unbalanced braces in AI response JSON.")


def _parse_response(raw_text: str) -> dict:
    """Parse the AI's raw text into a validated steps dict."""
    json_str = _extract_outermost_json(raw_text)
    data = json.loads(json_str)

    if "steps" not in data or not isinstance(data["steps"], list):
        raise ValueError("AI response JSON missing 'steps' array.")

    # Ensure summary field exists
    if "summary" not in data or not data["summary"]:
        data["summary"] = f"{len(data['steps'])} steps ready — see below!"

    # Validate each step minimally
    for step in data["steps"]:
        if "instruction" not in step:
            step["instruction"] = step.get("text", "(no instruction)")
        if "description" not in step:
            step["description"] = ""
        if "index" not in step:
            step["index"] = data["steps"].index(step) + 1

    return data
