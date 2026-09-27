# Product Requirements Document (PRD): BlenderMentor

BlenderMentor is a self-contained Blender add-on that provides a premium, AI-powered interactive tutor interface directly inside Blender. It teaches users how to use Blender by giving step-by-step guidance and visually highlighting relevant UI elements, strictly adhering to the principle of never executing actions on the user's behalf.

---

## 1. System Architecture

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

---

## 2. Organized Feature Registry

### 2.1 API Preferences & Setup
The add-on configuration panel allows users to seamlessly set up and authenticate their preferred AI providers.

*   **Status**: `IMPLEMENTED`
*   **Key Features**:
    *   **Provider Selector**: Dropdown to choose between **Gemini** (default) and **Claude**.
    *   **Password-Masked API Key**: Safe text input field where API keys are masked and securely stored in `AddonPreferences` (never saved to `.blend` files or logged).
    *   **Dynamic Model Fetching**: A "Fetch Models" button makes a live API call using the entered key to retrieve all supported models. **Zero model names are hardcoded in the add-on**.
    *   **Dynamic Model Selector**: A dropdown menu populated purely by the live API response.
    *   **Connection Tester**: A "Test Connection" button that fires a lightweight request to verify key validation and connectivity.
    *   **Developer Mode Toggle**: Enables built-in developer tools and indicators.
    *   **Zero-Dependency Design**: Leverages Python's native `urllib.request` library so no external `pip` packages are needed.

---

### 2.2 Interactive Chat Panel
The N-panel (`View3D > Sidebar > BlenderMentor`) serves as the central hub of interaction.

*   **Status**: `IMPLEMENTED`
*   **Key Features**:
    *   **Clean Scrollable History**: Renders the conversation history with clean margins, word wrapping, and elegant icon-based role badges (`User` vs. `Mentor`).
    *   **Persisted Session state**: Chat history is stored dynamically in `bpy.types.Scene`, persisting across files and editor windows.
    *   **Chat Input**: Input text box and `Send` button. Supports pressing **Enter** in the 3D Viewport region to send.
    *   **Window Management Controls**:
        *   **Pop Out**: Opens a separate, floating window (`wm.window_new`) containing BlenderMentor.
        *   **Dock**: Splits the active 3D Viewport (`screen.area_split`) to create a dedicated panel docked to the right side.
        *   **Undock**: Safely closes the split region and reclaims viewport space.
    *   **Clear Chat**: A button that instantly clears conversation history, active steps, and removes all GPU overlays.

---

### 2.3 Guided Steps Navigator
When the AI responds with a step-by-step resolution list, the navigator displays them sequentially and dynamically.

*   **Status**: `IMPLEMENTED`
*   **Key Features**:
    *   **Direct Step Click Selection**: The entire step label (e.g. `Step 1`, `Step 2`) behaves as a prominent, clickable button. Clicking any step header instantly activates it, updates the selection box color, and triggers its UI highlight.
    *   **Next/Prev Controls**: Clean `← Prev` and `Next →` buttons to walk through instructions sequentially. Marking a step as done changes its state box.
    *   **Friendly Summary Title**: The AI replies with a warm, conversational summary one-liner (e.g. *"Here is how to add a modifier to your Cube:"*) rather than a dry status report.
    *   **Step Indicator**: Displays current progress (e.g. `"Step 2 of 5"`).
    *   **Context-Aware Follow-Ups**: Each step includes an **Ask ❓** button. Clicking it places the chat input box into follow-up mode, and the next user message is automatically sent to the AI alongside the previous response as context.
    *   **Automated Keyboard Shortcut Guidance**: The AI system prompt (Rule 13) strictly enforces presenting exact keyboard shortcuts alongside visual paths (e.g. *"Click Add in the header menu bar (or press Shift+A)"*) whenever a step involves a menu or action that has a keyboard shortcut to build double memory.

---

### 2.4 UI Highlighting System
BlenderMentor overlays a beautiful, pulsing amber glow onto active regions to guide the user's eyes without modifying their workspace.

*   **Status**: `IMPLEMENTED`
*   **Key Features**:
    *   **Level 1 (Editor Area)**: Pulsing amber border drawn over an entire editor window (e.g. Outliner or Properties panel).
    *   **Level 2 (Properties Tab Icon)**: Highlights the active properties menu vertically by overlaying a pulsing glow on the dynamic `NAVIGATION_BAR` region of the Properties editor (avoiding hardcoded pixel math offsets).
    *   **Level 3 (Editor Regions)**: Highly precise pulsing glow targeting `HEADER`, `TOOLS` (toolbar), `TOOL_HEADER`, or `UI` (N-Panel sidebar) regions.
    *   **Level 4 (Panel/Header/Menu Injection)**: Prepend-injects a subtle red highlight/separator directly into panel classes (`_PT_`), header classes (`_HT_`), or menu classes (`_MT_`) draw loops when active.
    *   **Snappy 2.0s Animation**: Overlays pulse smoothly exactly **twice** (exactly 2.0 seconds) and then disappear cleanly so they are not distracting.
    *   **Pure Visual Guidance for Menus**: Highlighting a step that targets a menu (e.g. `add_menu`) pulses the editor's header region, teaching the user exactly where the menu resides to build correct visual muscle memory.

---

### 2.5 Developer Mode (Dev Tools)
A collapsible section in the sidebar for rapid debugging and feature verification.

*   **Status**: `IMPLEMENTED`
*   **Key Features**:
    *   **"DEV" Header Badge**: Appears in the main chat panel header when Developer Mode is active.
    *   **Highlight Tester**: A text box and "Test Highlight" button allowing devs to type targets directly (e.g., `viewport`, `modifiers`, `outliner`) and verify pulsing immediately.
    *   **Step Navigator Tester**: A "Load Mock Steps" button that injects a hardcoded 5-step mock JSON list to test the navigation UI and overlays instantly without API charges.
    *   **Scene Context Inspector**: Renders a live, updated JSON payload showing exactly what scene data is fed to the AI.

---

## 3. Deferred Features (Not Implemented Yet)

These features have been discussed and designed but are scheduled for implementation in future milestones.

### 3.1 Visual Screenshot Understanding (Multimodal Analysis)
*   **Goal**: Enable the AI to look at the user's viewport/render in real-time to solve complex visual bugs (e.g. "Why is my material black?" or "Why did my shading break?").
*   **Status**: `NOT IMPLEMENTED YET` (Deferred)
*   **Implementation Strategy**:
    *   AI calls a custom tool: `request_screenshot_analysis(reason: str)`.
    *   The background worker thread halts, and the chat panel displays an interactive **Consent Card** detailing the AI's reason.
    *   If the user clicks **Allow**: The add-on takes a window screenshot, encodes it in base64, deletes the file from disk immediately (privacy-first), and sends it to the vision engine (Gemini or Claude).
    *   If the user clicks **Deny**: The thread resumes, informing the AI the screenshot was declined, and the AI guides them verbally instead.

---

### 3.2 Question Mark Focus Shortcut
*   **Goal**: Automatically focus and activate the chat text box cursor when clicking the **Ask ❓** follow-up button on any step.
*   **Status**: `NOT IMPLEMENTED YET` (Deferred)
*   **Implementation Strategy**:
    *   When the operator `blendermentor.ask_step` is triggered, programmatically set the active focus state on the chat input text box property inside the panel layout so the user can begin typing immediately without clicking again.

---

### 3.3 Local RAG / External Knowledge Base Integration
*   **Goal**: Provide accurate answers to highly complex, specialized Blender inquiries by pulling context from official Blender manuals or release notes without relying solely on the LLM's baseline knowledge.
*   **Status**: `NOT IMPLEMENTED YET` (Deferred)
*   **Implementation Strategy**:
    *   Assemble a lightweight, vectorized local index of the Blender Manual or key troubleshooting guides.
    *   Implement a search retrieval client in the `ai_client.py` pipeline to inject matching documentation snippets into the model's system context on demand.
