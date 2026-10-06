# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — API Exchange Logger
#
# Provides structured logging for prompts, scene context, tool execution rounds,
# and model responses during developer mode.

import os
import json
import time
import threading
from datetime import datetime
import bpy

_log_lock = threading.Lock()
_last_exchange_text = ""


def get_log_filepath() -> str:
    """Return the absolute path to blendermentor_api.log in Blender's user config dir."""
    config_dir = bpy.utils.user_resource('CONFIG')
    os.makedirs(config_dir, exist_ok=True)
    return os.path.join(config_dir, "blendermentor_api.log")


def format_exchange(session: dict) -> str:
    """Format a session dictionary into a clean, human-readable log entry."""
    timestamp = session.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    provider = session.get("provider", "UNKNOWN")
    model = session.get("model", "UNKNOWN")
    latency = session.get("latency", 0.0)
    prompt = session.get("prompt", "")
    base_ctx = session.get("base_ctx", "")
    rounds = session.get("rounds", [])
    raw_response = session.get("raw_response", "")
    error = session.get("error", None)

    border = "=" * 80
    sub_border = "-" * 80

    lines = [
        border,
        f"BLENDERMENTOR API EXCHANGE — {timestamp}",
        f"Provider: {provider} | Model: {model} | Latency: {latency:.2f}s",
        sub_border,
        "",
        "--- [1. USER PROMPT] ---",
        prompt,
        "",
        "--- [2. BASE SCENE CONTEXT] ---",
    ]

    # Try formatting base_ctx nicely if it's JSON
    if isinstance(base_ctx, str):
        try:
            parsed = json.loads(base_ctx)
            lines.append(json.dumps(parsed, indent=2))
        except Exception:
            lines.append(base_ctx)
    elif isinstance(base_ctx, dict):
        lines.append(json.dumps(base_ctx, indent=2))
    else:
        lines.append(str(base_ctx))

    lines.append("")
    lines.append(f"--- [3. TOOL CALLING ROUNDS ({len(rounds)})] ---")
    if not rounds:
        lines.append("No tool calls — direct answer on first round.")
    else:
        for idx, r in enumerate(rounds, 1):
            lines.append(f"  • Round {idx}:")
            tool_calls = r.get("tool_calls", [])
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args = tc.get("args", {})
                res = tc.get("result", {})
                lines.append(f"    - Tool: {name}({json.dumps(args)})")
                res_str = json.dumps(res, indent=6) if isinstance(res, (dict, list)) else str(res)
                lines.append(f"      Result: {res_str}")

    lines.append("")
    if error:
        lines.append("--- [4. ERROR ENCOUNTERED] ---")
        lines.append(str(error))
    else:
        lines.append("--- [4. FINAL MODEL RESPONSE] ---")
        if isinstance(raw_response, str):
            try:
                parsed_res = json.loads(raw_response)
                lines.append(json.dumps(parsed_res, indent=2))
            except Exception:
                lines.append(raw_response)
        elif isinstance(raw_response, dict):
            lines.append(json.dumps(raw_response, indent=2))
        else:
            lines.append(str(raw_response))

    lines.append("")
    lines.append(border)
    lines.append("\n")

    return "\n".join(lines)


def log_api_exchange(session: dict):
    """Write an API exchange to blendermentor_api.log and update in-memory cache."""
    global _last_exchange_text

    formatted = format_exchange(session)
    with _log_lock:
        _last_exchange_text = formatted
        try:
            filepath = get_log_filepath()
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(formatted)
        except Exception as e:
            print(f"[BlenderMentor] Error writing to API log file: {e}")


def get_last_exchange() -> str:
    """Return the most recent formatted API exchange text."""
    global _last_exchange_text
    return _last_exchange_text


def get_full_log(max_bytes: int = 500000) -> str:
    """Read the contents of blendermentor_api.log up to max_bytes from the end."""
    filepath = get_log_filepath()
    if not os.path.exists(filepath):
        return "(No log entries yet. Run a prompt with Developer Mode enabled to generate logs.)"

    try:
        size = os.path.getsize(filepath)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                content = f.read()
                # Find the next clean newline
                nl = content.find("\n")
                if nl != -1:
                    content = content[nl + 1:]
                return f"[... truncated {size - max_bytes} bytes ...]\n" + content
            return f.read()
    except Exception as e:
        return f"Error reading log file: {e}"


def clear_log():
    """Clear both the log file on disk and the in-memory last exchange."""
    global _last_exchange_text
    with _log_lock:
        _last_exchange_text = ""
        filepath = get_log_filepath()
        try:
            if os.path.exists(filepath):
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write("")
        except Exception as e:
            print(f"[BlenderMentor] Error clearing API log file: {e}")


def sync_to_blender_text(content: str = None):
    """Mirror the log into Blender's built-in Text Editor datablock 'BlenderMentor_Log.txt'.

    MUST be called from Blender's main thread (e.g. inside a timer or operator).
    """
    if content is None:
        content = _last_exchange_text or get_full_log(max_bytes=100000)

    try:
        text_name = "BlenderMentor_Log.txt"
        text_block = bpy.data.texts.get(text_name)
        if not text_block:
            text_block = bpy.data.texts.new(name=text_name)
        text_block.clear()
        text_block.write(content)
    except Exception as e:
        print(f"[BlenderMentor] Error syncing log to Blender text block: {e}")
