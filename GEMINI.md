# BlenderMentor

A self-contained Blender addon that provides an AI-powered chat interface directly inside Blender. It teaches users how to use Blender by giving step-by-step guidance and highlighting the relevant UI elements — it never performs actions on the user's behalf.

---

## Core Behaviour

- User types a question in the chat panel inside Blender
- The addon reads the current scene state (objects, camera, materials, active mode, etc.) via `bpy`
- It sends the question + scene context to the AI provider API using the user's own API key
- The AI responds with a structured list of numbered steps — each step contains the instruction text and a hidden highlight target
- The chat panel displays all steps at once and lets the user navigate through them one by one
- At each step, the relevant Blender UI element is highlighted automatically
- No external tools, terminals, servers, or IDEs are needed

---

## Architecture

```
User types question in Blender chat panel
        ↓
addon reads scene state via bpy
        ↓
HTTPS call → AI provider API (Gemini or Claude) with scene context + question
        ↓
AI returns structured JSON: list of steps, each with instruction + highlight target
        ↓
addon parses and stores steps in scene state
        ↓
chat panel displays all steps; user navigates with Prev / Next buttons
        ↓
at each step, addon triggers the corresponding UI highlight
```

All API calls use Python's built-in `urllib` — no pip installs required.

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
- Keys are stored in `bpy.types.AddonPreferences` — never in the `.blend` file

### 2. Chat Panel
- Located in the **N-panel sidebar** of the 3D Viewport (`View3D > Sidebar > BlenderMentor` tab)
- Conversation history displayed in a scrollable area
- Text input field + Send button — **pressing Enter also sends** (keymap registered in VIEW_3D UI region)
- AI replies with a **friendly one-liner summary** in the chat (e.g. "Sure! Here's how to add a modifier.") instead of a cold status message
- "Clear" button to reset the conversation
- Conversation history stored in `bpy.types.Scene` — persists with the `.blend` file and is shared across all Blender editor windows
- **Pop Out** button (header): opens BlenderMentor in a separate floating window — ideal for dual-monitor setups
- **Dock** button (header): splits the 3D Viewport and creates a dedicated BlenderMentor panel docked to the right
- **Undock** button (header, when docked): closes the docked panel and reclaims the space
- **Follow-up questions**: each step has an **Ask ❓** button — clicking it puts the input into follow-up mode, and the AI receives the previous response as context to provide an improved, clarified step list

### 3. Step-by-Step Navigation
When the AI responds, it returns a structured list of steps. The chat panel renders them as follows:

- All steps are listed at once so the user can see the full picture before starting
- A **step indicator** shows the current position (e.g. "Step 2 of 5")
- **← Prev** and **Next →** buttons let the user move through steps at their own pace
- The currently active step is visually distinct in the list (bold label or highlighted row)
- Each step has a hidden `highlight` field (not shown to the user) specifying which UI element to highlight when that step is active
- Moving to a step automatically triggers the corresponding UI highlight
- Highlights clear when the user moves to a different step

### 4. UI Highlighting
Blender's Python API does not expose pixel coordinates of individual widgets. Highlighting is implemented at three levels of granularity:

**Level 1 — Editor Area**
A GPU quad (semi-transparent coloured border) drawn over the entire area using a draw handler on the relevant space type with `POST_PIXEL`. Used when directing the user to a specific editor (e.g. "open the Properties editor", "look at the Timeline").

**Level 2 — Properties Tab Icon (e.g. the Modifier wrench)**
A draw handler registered on `SpaceProperties` targeting the `NAVIGATION_BAR` region. Tab icons are stacked vertically in a fixed layout. A coordinate lookup table maps tab names to their vertical index; the highlight box is drawn at the computed pixel position within that region.

```python
# Coordinate lookup — vertical index of each Properties tab icon
PROPERTIES_TAB_INDEX = {
    "render":      0,
    "output":      1,
    "view_layer":  2,
    "scene":       3,
    "world":       4,
    "object":      5,
    "modifiers":   6,   # wrench icon
    "particles":   7,
    "physics":     8,
    "constraints": 9,
    "object_data": 10,
}
```

**Level 3 — Specific Panel or Button**
For button-level precision (e.g. the "Add Modifier" button inside the Modifier tab), Blender's `prepend`/`append` mechanism injects a visual indicator directly into the target panel's `draw` or `draw_header` function. This uses Blender's own layout system — no pixel math required.

```python
def _modifier_panel_highlight(self, context):
    if context.scene.blendermentor_highlight_target == "modifier_add_button":
        self.layout.label(text="◀ here", icon='RESTRICT_SELECT_OFF')

bpy.types.DATA_PT_modifiers.prepend(_modifier_panel_highlight)
```

**Highlight target format in AI responses:**
```json
{ "level": "area", "space": "VIEW_3D", "target": "viewport" }
{ "level": "tab",  "space": "PROPERTIES", "target": "modifiers" }
{ "level": "panel","space": "PROPERTIES", "target": "modifier_add_button" }
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

## Scene Context Format

Sent with every AI request:

```json
{
  "active_object": "Cube",
  "active_object_type": "MESH",
  "mode": "OBJECT",
  "editor": "VIEW_3D",
  "objects": ["Cube", "Camera", "Light"],
  "modifiers_on_active": [],
  "materials_on_active": ["Material"],
  "frame_current": 1,
  "animation_data_exists": false,
  "visible_editors": ["VIEW_3D", "PROPERTIES", "OUTLINER", "DOPESHEET_EDITOR"]
}
```

---

## File Structure

```
blendermentor/
├── GEMINI.md                        # This file
├── README.md
├── LICENSE                          # GPL v3
└── addon/
    ├── __init__.py                  # bl_info, register(), unregister()
    ├── preferences.py               # AddonPreferences: provider, API key, fetched models, developer mode toggle
    ├── ai_client.py                 # HTTPS calls to Gemini / Claude: model listing + response generation + JSON parsing
    ├── scene_reader.py              # Reads bpy scene state into context dict
    ├── ui/
    │   ├── chat_panel.py            # N-panel: conversation history, step navigator (all steps + Prev/Next), dev tools section
    │   ├── highlight.py             # All three highlight levels: area GPU quad, tab coord map, panel prepend/append
    │   └── window_manager.py        # Pop-out floating window, dock/undock split area, toggle sidebar
    └── state/
        └── conversation.py          # Scene-level conversation history + current step list (CollectionProperty)
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

### 📋 Milestone 8 — Public Release
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
