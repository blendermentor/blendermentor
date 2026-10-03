# BlenderMentor

A self-contained Blender addon that provides an AI-powered chat interface directly inside Blender. It teaches users how to use Blender by giving step-by-step guidance and highlighting the relevant UI elements — it never performs actions on the user's behalf.

---

## Core Behaviour

- User types a question in the chat panel inside Blender
- The addon reads a **minimal base context** (active object, mode, visible editors) via `bpy`
- It sends the question + base context to the AI provider API using the user's own API key
- The AI may **call tools** to fetch additional scene details on demand (render settings, viewport state, object details, etc.)
- The addon executes the requested tool locally, returns the result, and lets the AI continue reasoning
- This tool-calling loop repeats until the AI has enough context to provide its final answer
- The AI responds with a structured list of numbered steps — each step contains the instruction text and a hidden highlight target
- The chat panel displays all steps at once and lets the user navigate through them one by one
- At each step, the relevant Blender UI element is highlighted automatically
- A **live status indicator** shows what the AI is doing during tool-calling rounds (e.g. "Checking Render Settings...")
- No external tools, terminals, servers, or IDEs are needed

---

## Architecture

```
User types question in Blender chat panel
        ↓
addon reads minimal base context via bpy
        ↓
HTTPS call → AI provider API with base context + question + tool declarations
        ↓
    ┌─── AI decides: do I need more info? ───┐
    │ YES                                     │ NO
    ↓                                         ↓
AI returns tool call(s)                  AI returns final JSON
(e.g. get_render_settings)               (steps + highlights)
    ↓                                         ↓
addon executes tool locally              addon parses and stores
and sends result back to AI              steps in scene state
    ↓                                         ↓
    └──── loop back to AI ────┘          chat panel displays steps;
                                         user navigates with Prev/Next
                                              ↓
                                         at each step, addon triggers
                                         the corresponding UI highlight
```

All API calls use Python's built-in `urllib` — no pip installs required.
All AI calls run in a **background thread** to keep Blender's UI responsive.

---

## Tech Stack

- **Language**: Python (Blender's bundled Python — no external interpreter)
- **Blender API**: `bpy`, `gpu` module for draw handlers
- **AI API calls**: `urllib.request` (built-in) — direct HTTPS to Gemini / Claude endpoints
- **UI**: Native Blender N-panel (sidebar) as the starting point, progressing to a docked split area
- **State**: `bpy.types.Scene` custom properties — persists with `.blend` file
- **Minimum Blender version**: 4.0
- **License**: GPL v3 (required for all Blender addons using `bpy`)

---

## User-Facing Features

### 1. API Setup (first time)
On first use, the preferences panel guides the user to configure their AI provider:
- `Edit → Preferences → Add-ons → BlenderMentor`
- Provider dropdown: **Gemini** (default) / Claude
- API key field: password-masked text input
- **"Fetch Models" button**: calls the provider's model listing API with the entered key and populates the model selector dynamically — no model names are hardcoded anywhere in the addon
- Model selector: dropdown populated entirely from the live API response; user picks their preferred model
- "Test Connection" button: fires a minimal API call to confirm the key and selected model work
- Helper link: opens Google AI Studio or Anthropic Console in the browser
- Developer Mode toggle (see section below)
- Auto-open Browser Companion toggle (default: OFF) — automatically opens the companion web app when Blender starts
- Keys are stored in `bpy.types.AddonPreferences` — never in the `.blend` file

### 2. Chat Panel
- Located in the **N-panel sidebar** of the 3D Viewport (`View3D > Sidebar > BlenderMentor` tab)
- Streamlined as a distraction-free **Remote Control** that keeps your 3D viewport clean by default
- Shows a **"Start BlenderMentor"** button when the browser companion is not connected, letting you launch it with one click
- Shows only the **Active Step Card** with its official Blender icon, instruction title, context/reasoning box, quick re-highlight (`💡`), follow-up (`❓`), and compact tutorial (`URL`) buttons
- Clean idle state: Prev/Next and empty counters are hidden until steps are active
- **🎙️ Smart Voice Dictation (`Alt + ↑`)**:
  - Tap or press **`Alt + ↑`** (`Option + ↑` on macOS) to toggle voice dictation
  - If the browser companion isn't running, pressing the Mic or shortcut **automatically launches the browser companion** and starts listening seamlessly
  - Tap again or press `Alt + ↑` to stop dictation and submit your question
- **Global Shortcuts**:
  - **`Alt + ↑`** (`Option + ↑` on macOS): Toggle voice dictation (tap to listen, tap again to finalize and send)
  - **`Alt + ↓`** (`Option + ↓` on macOS): Cancel voice dictation without sending (transcribed text remains in the text box for editing; pressing again clears the box)
  - **`Alt + →`** (`Option + →` on macOS): Advance to next step from *any* editor in Blender (3D View, Properties, Outliner, Nodes, Timeline)
  - **`Alt + ←`** (`Option + ←` on macOS): Go back to previous step globally
- **BlenderMentor Branding**: Custom logo integrated into the N-panel header (to the left of the title) and the Browser Companion (header & favicon)
- Conversation history stored in `bpy.types.Scene` — persists with the `.blend` file and is shared across all Blender editor windows
- **Follow-up questions**: each step has an **Ask ❓** button — clicking it puts the input into follow-up mode, and the AI receives the previous response as context to provide an improved, clarified step list

### 3. Step-by-Step Navigation
When the AI responds, it returns a structured list of steps. The chat panel and browser companion render them as follows:

- All steps are listed at once so the user can see the full picture before starting
- A **step indicator** shows the current position (e.g. "Step 2 of 5")
- **← Prev** and **Next →** buttons let the user move through steps at their own pace
- The currently active step is visually distinct in the list (bold label or highlighted hero card)
- **🏁 Final Step Celebration**: The final step is visually distinguished with a congratulatory completion message (*"This is the last step. Hope you've achieved what you wanted!"*), which is also spoken at the end of the audio readout
- Each step has a hidden `highlight` field (not shown to the user) specifying which UI element to highlight when that step is active
- Moving to a step automatically triggers the corresponding UI highlight
- Highlights clear when the user moves to a different step

### 4. UI Highlighting
Blender's Python API does not expose pixel coordinates of individual widgets. Highlighting is implemented at four levels of granularity:

**Level 1 — Editor Area**
A GPU quad (semi-transparent coloured border) drawn over the entire area using a draw handler on the relevant space type with `POST_PIXEL`. Used only as a last resort when directing the user to a specific editor (e.g. "open the Properties editor", "look at the Timeline").

**Level 2 — Properties Tab Icon (e.g. the Modifier wrench)**
A draw handler registered on `SpaceProperties` targeting the `NAVIGATION_BAR` region. Tab icons are stacked vertically in a fixed layout. A coordinate lookup table maps tab names to their vertical index; the highlight box is drawn at the computed pixel position within that region.

**Level 3 — Region (Header / Toolbar / Sidebar)**
A GPU overlay drawn on a specific **region** within an editor — much more precise than highlighting the entire area. Targets the `HEADER`, `TOOLS`, `TOOL_HEADER`, or `UI` region. Used when the user needs to interact with the menu bar, the left toolbar, or the N-panel sidebar.

**Level 4 — Panel / Menu / Header Injection**
For button-level precision, Blender's `prepend`/`append` mechanism injects a visual `◀ HERE` indicator directly into the target class's `draw` function. This works for panels (e.g. `DATA_PT_modifiers`), headers (e.g. `VIEW3D_HT_header`), and menus (e.g. `VIEW3D_MT_add`). When the user opens the Add menu, the indicator appears inside the dropdown itself.

**Highlight target format in AI responses:**
```json
{ "level": "area",   "space": "VIEW_3D",    "target": "viewport" }
{ "level": "tab",    "space": "PROPERTIES",  "target": "modifiers" }
{ "level": "region", "space": "VIEW_3D",     "target": "header" }
{ "level": "panel",  "space": "VIEW_3D",     "target": "add_menu" }
{ "level": "panel",  "space": "PROPERTIES",  "target": "modifier_add_button" }
```

This field is parsed by the addon internally and is never shown to the user.

### 5. Developer Mode
Enabled via a toggle in `AddonPreferences`. When active:

- A **"DEV"** badge appears in the chat panel header so developer mode is always visible
- A collapsible **Dev Tools** section appears in the chat panel with the following:

**Highlight Tester**
Type any target name and press "Test Highlight" to fire that highlight directly — without going through the AI flow. Useful for building and verifying the highlight registry.
Supported targets from day one: `viewport`, `properties`, `outliner`, `timeline`, `graph_editor`, `modifiers`, `render`, `output`, `world`, `object`, `particles`, `physics`, `constraints`

**Step Navigator Tester**
Inject a hardcoded mock step list (JSON) to test the step navigation UI without making a live API call.

**Scene Context Inspector**
Displays the exact JSON that would be sent to the AI as scene context, updated live as the scene changes. Useful for verifying the scene reader is capturing the right data.

---

## AI Response Format

The AI always returns a structured JSON object. The system prompt enforces this schema:

```json
{
  "summary": "Sure! Here's how to add a Subdivision Surface modifier to your Cube.",
  "steps": [
    {
      "index": 1,
      "instruction": "Open the Properties editor. Look for the vertical icon bar on the right side of the Blender window.",
      "highlight": {
        "level": "area",
        "space": "PROPERTIES",
        "target": "properties"
      }
    },
    {
      "index": 2,
      "instruction": "Click the wrench icon (Modifier Properties tab) in the Properties panel.",
      "highlight": {
        "level": "tab",
        "space": "PROPERTIES",
        "target": "modifiers"
      }
    },
    {
      "index": 3,
      "instruction": "Click 'Add Modifier' at the top of the modifier stack.",
      "highlight": {
        "level": "panel",
        "space": "PROPERTIES",
        "target": "modifier_add_button"
      }
    },
    {
      "index": 4,
      "instruction": "From the dropdown that appears, select Generate → Subdivision Surface.",
      "highlight": null
    }
  ]
}
```

---

## System Prompt (Teaching Mode)

```
You are BlenderMentor, an expert Blender teacher embedded directly inside Blender.

Rules:
1. NEVER perform actions in Blender yourself. Always guide the user to do it.
2. Respond ONLY with a valid JSON object matching the schema below. No prose, no markdown, no explanation outside the JSON.
3. Break your guidance into clear numbered steps. Each step must contain a single action.
4. Each step must include a highlight field:
   { "level": "area" | "tab" | "panel", "space": "<BLENDER_SPACE_TYPE>", "target": "<target_name>" }
   Set "highlight": null only if no UI element is relevant for that step.
5. Use exact Blender UI names — panel labels, button text, menu paths.
6. Keep each step focused on one action only.
7. Use the provided scene context to tailor your response.

Response schema:
{ "steps": [ { "index": <int>, "instruction": "<string>", "highlight": <object|null> } ] }
```

---

## AI Provider Details

### Gemini (default)
- API key from: https://aistudio.google.com
- List models: `GET https://generativelanguage.googleapis.com/v1beta/models?key={api_key}`
- Generate: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`
- No model names hardcoded — list is always fetched live

### Claude
- API key from: https://console.anthropic.com
- List models: `GET https://api.anthropic.com/v1/models` (header: `x-api-key`)
- Generate: `POST https://api.anthropic.com/v1/messages`
- Required headers: `x-api-key`, `anthropic-version: 2023-06-01`, `content-type: application/json`
- No model names hardcoded — list is always fetched live

---

## Scene Context (Agentic Tool-Calling Model)

The addon uses a **two-tier context system** to keep API payloads small while giving the AI access to deep scene details when needed.

### Base Context (always sent)
A minimal dict included with every request:

```json
{
  "blender_version": "5.1.0",
  "platform": "macOS",
  "render_engine": "BLENDER_EEVEE",
  "active_object": "Cube",
  "active_object_type": "MESH",
  "mode": "OBJECT",
  "objects": ["Cube", "Camera", "Light"],
  "frame_current": 1,
  "visible_editors": ["VIEW_3D", "PROPERTIES", "OUTLINER", "DOPESHEET_EDITOR"]
}
```

### On-Demand Tools (called by AI when needed)
The AI is given tool declarations and can call any of these during the conversation:

| Tool Name | When AI Calls It |
|---|---|
| `check_addon_status(addon_name)` | Checks if required add-on/extension (Cell Fracture, Node Wrangler, etc.) is enabled/installed |
| `get_render_settings` | Questions about rendering, performance, output quality |
| `get_viewport_state` | Questions about viewport appearance, shading, overlays |
| `get_object_details(object_name)` | Questions about a specific object's modifiers, materials, mesh data |
| `get_selection_info` | Questions about selection, parenting, joining |
| `get_active_tool_info` | Questions about active tool, interaction problems |
| `get_world_and_lighting` | Questions about lighting, environment, background |
| `search_blender_community(query)` | User asks a how-to question that may have community solutions (Stack Exchange) |
| `fetch_blender_docs(page_path)` | AI needs exact documentation for a feature, setting, or workflow (version-matched) |
| `evaluate_python_expression(expr)` | Inspects deep properties, node trees, or settings when other tools don't cover it |

### Native Search Tools (provider-handled, zero client code)

| Provider | Tool | How It Works |
|----------|------|-------------|
| **Gemini** | `google_search` | Added to `tools` array; Gemini searches Google server-side when needed |
| **Claude** | `web_search` | Added to `tools` array; Claude searches the web server-side with citations |
| **Ollama** | _(none)_ | No native search; uses `search_blender_community` and `fetch_blender_docs` only |

All web/search tools are gated by the **"Enable Web Search"** toggle in addon preferences (default: ON).

---

## File Structure

```
blendermentor/
├── GEMINI.md                        # This file
├── README.md
├── LICENSE                          # GPL v3
└── addon/
    ├── __init__.py                  # bl_info, register(), unregister()
    ├── preferences.py               # AddonPreferences: provider, API key, fetched models, developer mode, web search toggle
    ├── ai_client.py                 # Agentic tool-calling loop for Gemini / Claude / Ollama + native search tools + JSON parsing
    ├── scene_reader.py              # Modular: base context + on-demand tool functions + web tools (Stack Exchange, docs fetch) + tool registry
    ├── server.py                    # Lightweight HTTP/WebSocket server for browser companion & voice dictation
    ├── icons/                       # Custom plugin logo and icons
    │   └── logo.png
    ├── web/                         # Browser Companion web app (HTML/CSS/JS)
    │   ├── index.html
    │   └── logo.png
    ├── ui/
    │   ├── chat_panel.py            # N-panel: threaded AI calls, live status, conversation, step navigator, dev tools
    │   ├── highlight.py             # All three highlight levels: area GPU quad, tab coord map, panel prepend/append
    │   └── window_manager.py        # Pop-out floating window, dock/undock split area, toggle sidebar
    └── state/
        └── conversation.py          # Scene-level conversation history + current step list + processing status
```

---

## Development Milestones

### ✅ Milestone 0 — Planning
Architecture, UI approach, API strategy, licensing, and naming all decided.

### ✅ Milestone 1 — Chat Panel + Blink
Multi-file addon with a working N-panel chat UI and a `blink` command that flashes the 3D viewport using a GPU draw handler. Zero dependencies. Blender 4.0+.

### ✅ Milestone 2 — API Preferences + Live Model Fetching
`AddonPreferences` panel: provider selector, password-masked API key field, "Fetch Models" button (live API call populates dropdown), dynamic model selector, test connection button, developer mode toggle, helper links.

### ✅ Milestone 3 — AI Connectivity + Structured Response Parsing
Wire chat input to the AI provider. Send scene context with every request. Parse the structured JSON step response. Store steps in scene-level `CollectionProperty`.

### ✅ Milestone 4 — Step Navigator UI
Render all steps in the Guided Steps panel. Step indicator ("Step N of M"), Prev/Next navigation buttons, active step visually distinct in the list, goto buttons, mark all done.

### ✅ Milestone 5 — UI Highlighting (all three levels)
Implement: Level 1 GPU area quad, Level 2 NAVIGATION_BAR tab coordinate map, Level 3 panel prepend/append injection. Connect to step navigator — each step transition triggers its highlight and clears the previous one.

### ✅ Milestone 6 — Developer Mode
Dev tools section in chat panel: highlight tester (type target → fire highlight), step navigator tester (mock step list), scene context inspector (live JSON).

### ✅ Milestone 7 — Docked / Floating UI
Keep N-panel sidebar as default. Add "Pop Out" button (`wm.window_new`) for a floating window on dual-monitor setups. Add "Dock" button (`screen.area_split`) to split the viewport and create a dedicated mentor panel. Add "Undock" to rejoin. All three modes (sidebar, floating, docked) share the same scene-level state.

### ✅ Milestone 8 — Agentic Tool-Calling
Refactored from a "dump all context" model to an agentic tool-calling architecture. The AI receives minimal base context and can call on-demand tools (get_render_settings, get_viewport_state, get_object_details, get_selection_info, get_active_tool_info, get_world_and_lighting) when it needs more information. AI calls now run in a background thread with live status updates in the chat panel. Native tool-calling support for Gemini, Claude, and Ollama APIs.

### ✅ Milestone 9 — Web Search & External Knowledge
Hybrid internet-backed knowledge system. Native AI search tools (Gemini `google_search`, Claude `web_search`) for broad web access. Client-side tools for Blender Stack Exchange API (`search_blender_community`) and official documentation (`fetch_blender_docs` with version-specific URLs via `bpy.app.version`). Global "Enable Web Search" toggle in preferences. Scene-grounded responses: AI always combines web/community results with the user's actual scene context. Dev tools: web search tester for Stack Exchange and docs fetch.

### ✅ Milestone 10 — Browser Companion & Voice Dictation
Built-in lightweight threaded HTTP server (`server.py`) serving the dual-column web interface on port 8765. Features: 791 official vector Blender icons, real-time client-side voice dictation with a 5-bar live VU meter and hardware mic device detection, 3-mode Voice Readout (Title & Description, Title Only, Off), 1440px wide-screen max-width frame, and interactive draggable column resizer with `localStorage` persistence.

### ✅ Milestone 11 — N-Panel Remote Control, Speed Controls & Version Awareness
Redesigned N-panel sidebar into a sleek, space-saving Remote Control. Displays the active step with icon, instruction, reasoning, and quick actions, while tucking full chat and step checklist behind a toggle. Hybrid walkie-talkie mic button (press-and-hold >0.4s to speak and auto-send, or tap to toggle). Global `Alt + →` and `Alt + ←` step navigation shortcuts registered in the top-level `Window` keymap to work across all Blender editors. Final step completion celebration across Blender, browser, and audio readout. Added ⚡ Voice Readout Speed selector (`0.8x`–`2.0x`) with audible preview and persistence. Full Blender version (`bpy.app.version_string`) and OS platform awareness for 100% version-matched advice without hedging. On-demand `check_addon_status` tool. Targeted follow-up focus (`focus_step_index`) with speech resuming at the revised step. Resilient AI parser with truncation auto-repair, 8192 token limit, and forced final synthesis tool safety loop.

### 📋 Milestone 12 — Public Release
GitHub repo, README, contribution guide, GPL v3 license, cross-platform testing, Blender Extensions platform submission.

---

## Key Constraints

- Do not use `bgl` — deprecated in Blender 4.x. Use the `gpu` module only.
- Do not use `pip` or any external Python packages — only Blender's bundled Python stdlib.
- Do not hardcode AI model names anywhere — always fetch the model list live from the provider API.
- API keys must never be stored in `.blend` files or logged anywhere.
- The AI must never be instructed or allowed to execute `bpy` operations on the user's scene.
- The `highlight` field in AI responses is internal — never display it to the user.
- All addon code is GPL v3 — this is non-negotiable for Blender addons.
