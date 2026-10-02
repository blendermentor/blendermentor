# BlenderMentor

**BlenderMentor** is an AI-powered tutoring companion embedded directly inside Blender. It teaches you how to create by answering your questions, breaking complex workflows into step-by-step guidance, and visually highlighting relevant UI elements in real-time — without ever altering your scene on your behalf.

In addition to the native 3D Viewport N-panel sidebar, BlenderMentor now includes a **Browser Companion App** designed for dual-screen setups, tablets, and second monitors, complete with voice dictation, high-quality speech readout, official Blender vector icons, and real-time scene synchronization.

---

## 🌟 Key Features

### 🎮 1. N-Panel Remote Control & Hybrid Mic
- **Compact Remote Mode**: Keeps your 3D Viewport workspace clean by condensing the sidebar into an efficient remote control showing only the active step, its official Blender icon, action title, reasoning, and quick re-highlight/ask buttons.
- **Full View Toggle**: The full chat stream and complete step checklist are neatly tucked away and accessible via a single **Full View / Remote** toggle button.
- **🎙️ Hybrid Walkie-Talkie Mic**:
  - **Press & Hold (>0.4s)**: Walkie-talkie mode — hold down to speak your question inside Blender, and release to immediately stop dictation and dispatch the prompt to your browser companion.
  - **Quick Tap (<0.4s)**: Toggle mode — tap once to turn the microphone on, speak at your leisure, and tap again to finish and send.
- **Global Keyboard Navigation**:
  - **`Alt + →`** (`Option + →` on macOS): Advances to the next step from *any* editor in Blender (3D View, Properties, Outliner, Shader Editor, Timeline).
  - **`Alt + ←`** (`Option + ←` on macOS): Reverses to the previous step globally without requiring your cursor to hover over the 3D Viewport.

### 🖥️ 2. Dual-Column Browser Companion (`http://localhost:8765`)
- **Second-Screen Workflow**: Keep your 3D Viewport completely clutter-free while running your interactive mentor in any web browser on a second monitor, laptop, or tablet.
- **Wide-Screen Responsive Frame**: Automatically centers and constrains the workspace to a comfortable `1440px` max-width on 1440p, 4K, and ultrawide displays with a sleek dark backdrop (`#141414`) to prevent awkward, stretched text lines.
- **Interactive Draggable Splitter**: Click and drag the vertical divider line to freely adjust the width of the Chat and Steps columns. Preserves your customized width across browser reloads via `localStorage`, and double-clicks to snap back to the default 45% / 55% split.
- **Bi-directional Live Sync**: Step selection, highlights, follow-ups, and chat history synchronize instantly between Blender and your browser.
- **Zero Configuration**: Built-in Python HTTP server running inside Blender — no Node.js, external servers, or terminal setups required.

### 🎙️ 3. Voice Dictation & Live Audio VU Meter
- **Hands-Free Speech-to-Text**: Click the mic or press a shortcut to speak your questions naturally while keeping your hands on your mouse and keyboard.
- **Live Waveform & Audio Meter**: Visual 5-bar audio visualizer with real-time input percentage feedback so you know sound is being received.
- **Hardware Device Detector**: Identifies active microphone devices and alerts you if a silent virtual device (such as BlackHole) is accidentally selected.
- **Privacy-First**: Audio is transcribed client-side into text; raw voice recordings are never sent to external servers or stored in files.

### 🔊 4. Natural Voice Readout & Speed Control
- **Three Readout Modes**:
  - **`🔊 Readout: Title & Description`** *(Default)*: Speaks both the action instruction and the full context reasoning.
  - **`🔉 Readout: Title Only`**: Fast, concise narration speaking only the core instruction title.
  - **`🔇 Readout: Off`**: Mutes voice readout completely and dims the indicator.
- **⚡ Adjustable Readout Speed**: Fine-tune narration speed right from the header dropdown (`0.8x`, `1.0x`, `1.15x` [Default], `1.25x`, `1.5x`, `1.75x`, `2.0x`) with instant voice preview and persistent `localStorage` preference.
- **High-Quality Speech Synthesis**: Automatically prioritizes modern neural, natural, and enhanced system voices over legacy synthesizers.
- **Voice Selector Dropdown**: Choose your preferred narrator voice right from the browser companion header.
- **Smart Targeted Narration**: Reads the mentor's friendly conversational summary first, and on follow-ups automatically starts reading from the revised step (`focus_step_index`) rather than restarting from Step 1.

### 🎨 5. Official Blender UI Vector Icons
- **791 Official SVG Icons**: Embedded vector icons extracted directly from [ui.blender.org/icons](https://ui.blender.org/icons).
- **Contextual Visual Badges**: Step cards display the exact icons you see inside Blender (e.g. Modifier wrench, Bevel, Material preview, Light data, Outliner, etc.) to help you find tools faster.

### 🎯 6. Guided Step Navigation & UI Highlighting
- **Structured Guidance**: Complex 3D tasks are organized into manageable, numbered action steps.
- **Step Reasoning & Context**: Each step provides clear action instructions plus detailed background context explaining *why* the setting matters and *where* to find submenus.
- **🏁 Final Step Celebration**: The final step is visually celebrated in both the Blender Remote Control and Browser Companion, and naturally spoken aloud (*"This is the last step. Hope you've achieved what you wanted!"*).
- **Dynamic UI Highlighting (Level 1–4)**:
  - **Level 1 Area Quad**: Highlights entire editor areas (Properties, Viewport, Timeline, Outliner).
  - **Level 2 Tab Icons**: Highlights specific navigation tabs (e.g. Modifier wrench tab in Properties).
  - **Level 3 Region Overlay**: Targets editor headers, toolbars, and sidebars.
  - **Level 4 Panel/Menu Injection**: Injects visual `◀ HERE` indicators inside menus and panels.
- **Show Highlight (`💡`)**: Re-trigger any step's visual highlight directly from the browser card or N-panel.
- **Ask Follow-Up (`❓`)**: One-click clarification on any specific step with dedicated follow-up context and immediate step focus.

### 🧠 7. Agentic Tool Calling, Version Awareness & Resilient AI
- **Exact Version & OS Grounding**: Passes your exact `blender_version`, operating system `platform` (macOS/Windows/Linux), and active `render_engine` in the base context. The AI provides definitive, version-matched instructions (e.g. Extensions vs Add-ons in Blender 4.2+, `Blender > Preferences` on macOS vs `Edit > Preferences` on Windows) without vague conditional hedging.
- **Addon & Extension Checker (`check_addon_status`)**: On-demand tool that checks whether required add-ons (Cell Fracture, Node Wrangler, Rigify, Bool Tool) are installed or enabled before instructing you to enable them.
- **Two-Tier Context**: Sends minimal base context upfront and allows the AI to call on-demand tools (`get_render_settings`, `get_viewport_state`, `get_object_details`, `get_selection_info`, `get_active_tool_info`) only when necessary.
- **Blender Community & Docs Integration**: Live lookup tools for Blender Stack Exchange solutions and version-specific Blender documentation.
- **Resilient Tool Loop & Fallback**: High 8192 token limit, tool-economy directives, smart truncation repair, and a graceful forced synthesis round that guarantees you never get cutoff errors or exceed maximum tool rounds.
- **Provider Choice**: Use your own API keys for **Google Gemini** (default) or **Anthropic Claude**, or run local models with 100% privacy via **Ollama**. Model lists are fetched dynamically — no hardcoded models.

---

## 🚀 Installation & Quick Start

### Installation

1. Download the latest `blendermentor_addon.zip` from the [Releases](https://github.com/blendermentor/blendermentor/releases) page.
2. In Blender, go to `Edit > Preferences > Get Extensions`.
3. Click the **cog/gear icon** in the top-right corner and select **Install from Disk...**
4. Choose `blendermentor_addon.zip` and enable the add-on.

### API Configuration

1. In `Edit > Preferences > Add-ons > BlenderMentor`:
   - Select your preferred provider (**Gemini**, **Claude**, or **Ollama**).
   - Enter your API Key (or host URL for Ollama).
   - Click **Fetch Models** to populate the model list live from the provider.
   - Pick your preferred model and click **Test Connection**.

---

## 🛠 Usage Modes

### Mode 1: 3D Viewport Remote Control (N-Panel)
1. In the 3D Viewport, press `N` to expand the Sidebar and switch to the **BlenderMentor** tab.
2. Click **Browser** in the header to launch the Browser Companion on your second screen.
3. Use the **🎙️ Hold to Speak (or Tap)** hybrid button to dictate questions hands-free.
4. Navigate through steps using **`Alt + →`** (Next) and **`Alt + ←`** (Prev) from anywhere in Blender — the Remote Control, 3D highlights, and browser view stay in sync.
5. Click **Full View** at the top whenever you want to expand the complete chat history or full step checklist.

### Mode 2: Browser Companion (Dual-Monitor / Tablet)
1. With Blender open, navigate to **`http://localhost:8765`** in Google Chrome, Edge, Safari, or on your tablet.
2. Type or click the **🎙️** button to dictate your question using voice.
3. Review the dual-column guidance:
   - **Left Column**: Live chat conversation and reasoning status.
   - **Right Column**: Guided checklist, active step hero card with reasoning, **💡 Highlight** button, and **❓ Ask** follow-up button.
   - **Center Resizer**: Drag the divider to resize columns, or double-click to reset.
4. Use the **🔊 Readout Mode** dropdown to select your preferred narration depth (*Title & Description*, *Title Only*, or *Off*).

---

## 🏗 Architecture & Tech Stack

- **Language**: Pure Python using Blender's bundled interpreter (`bpy`, `gpu`).
- **Networking**: Python standard library `urllib` — zero pip dependencies required.
- **Embedded Server**: Native threaded HTTP server (`server.py`) serving the companion web app and REST state endpoints on port 8765.
- **Drawing Engine**: Modern GPU shaders via `gpu` module (`POST_PIXEL` draw handlers, Blender 4.0+ compatible; no deprecated `bgl`).
- **State Management**: Scene-level properties (`bpy.types.Scene`) that persist with your `.blend` file.

---

## 🧑‍💻 Developer Mode

Enable **Developer Mode** in preferences to unlock internal testing tools:
- **Highlight Tester**: Manually fire any UI highlight target without querying the AI.
- **Context Inspector**: Inspect the exact JSON scene payload sent to the AI in real time.
- **Step Navigator Tester**: Inject mock step sequences to test UI flows without using API quota.

---

## 🛡 Privacy & Transparency

- **Direct Connections**: All requests are sent directly from your machine to your chosen AI provider endpoint. No intermediary proxy servers.
- **Local Keys**: API keys are saved exclusively in your local `AddonPreferences` and never saved inside `.blend` project files.
- **Zero Telemetry**: No tracking, usage analytics, or external telemetry of any kind.
- **No Autonomous Scene Alteration**: BlenderMentor will never execute destructive code or modify your meshes without your consent.

---

## 📄 License

This project is licensed under the **GNU General Public License v3 (GPL v3)**. See [LICENSE](LICENSE) for details.
