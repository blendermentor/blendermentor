# BlenderMentor

**BlenderMentor** is a self-contained Blender add-on that provides an AI-powered chat interface directly inside Blender. It acts as an expert teacher, guiding you through complex workflows by highlighting relevant UI elements in real-time, ensuring you learn *how* to use the software rather than just having it done for you.

---

## 🌟 Key Features

- **Context-Aware AI Chat**: Integrated directly into the Blender N-panel. The AI reads your current scene state (active objects, materials, modifiers, etc.) to provide tailored advice.
- **Guided Step-by-Step Navigation**: Complex procedures are broken down into digestible steps. Navigate through instructions at your own pace with a dedicated step-by-step UI.
- **Dynamic UI Highlighting**: The AI doesn't just tell you where to go; it shows you. 
  - **Level 1**: Highlights entire Editor areas.
  - **Level 2**: Points out specific tab icons (e.g., Modifiers, Render).
  - **Level 3**: Pinpoints specific panels or buttons with visual indicators.
- **Flexible UI Layouts**: Use it in the Sidebar, pop it out into a floating window for dual-monitor setups, or dock it as a split area in your workspace.
- **Privacy & Control**: Uses your own API keys for **Gemini** or **Claude**. Supports **Ollama** for running open-source models locally with 100% privacy. No hardcoded models — the list is fetched live from the providers.

---

## 🚀 Getting Started

> 💡 **For full, detailed setup, key features, and troubleshooting instructions, please read our [Installation and Usage Guide](INSTRUCTIONS.md).**

## Installation

1. Download the latest `blendermentor_addon.zip` from the [Releases](https://github.com/blendermentor/blendermentor/releases) page.
2. In Blender, go to `Edit > Preferences > Get Extensions`.
3. Click the **cog/gear icon** in the top-right corner of the preferences window and select **Install from Disk...**
4. Select the `blendermentor_addon.zip` file and confirm installation.

### API Configuration

1. In the Add-on preferences (accessible via `Edit > Preferences > Add-ons` or your installed extensions details panel), choose your preferred provider (**Gemini**, **Claude**, or **Ollama**).
2. Enter your API Key (or host address for Ollama).
3. Click **Fetch Models** to populate the model list.
4. Select your desired model and click **Test Connection** to verify.

---

## 🛠 Usage

1. Open the **BlenderMentor** tab in the 3D Viewport Sidebar (press `N` to toggle).
2. Type your question (e.g., "How do I add a bevel to this cube?") and press **Send** (or press **Enter**).
3. The AI will respond with a friendly one-liner summary and a comprehensive list of steps.
4. Interact with the Guided Steps panel:
   *   **Click-Anywhere Selection**: Click directly on any step box (the instruction or explanation text) to instantly focus it and trigger its visual highlight overlay.
   *   **Highlight Re-trigger**: Click the lightbulb (`'LIGHT'`) icon next to a step header to replay its highlight animation at any time.
   *   **Top-Row Navigation**: Use the arrow navigators (`'TRIA_LEFT'` and `'TRIA_RIGHT'`) at the top of the panel to step through sequentially.
   *   **YouTube Search**: Click the Globe (`'URL'`) icon in the top progress bar to immediately launch an optimized YouTube tutorial search in your external web browser.
5. If you're stuck, click the **Ask ❓** button next to any step's instruction to ask a follow-up question specifically about that part of the process.

---

## 🏗 Architecture

BlenderMentor is built with a focus on being lightweight and self-contained:

- **Logic**: Pure Python using Blender's `bpy` and `gpu` modules.
- **Networking**: Built-in `urllib` for API calls — no external dependencies or `pip` required.
- **Drawing**: GPU-accelerated highlight overlays using the `gpu` module (Blender 4.0+ compatible).
- **State Management**: Scene-level properties that persist with your `.blend` file.

---

## 🧑‍💻 Developer Tools

Enable **Developer Mode** in preferences to access built-in debugging tools:
- **Highlight Tester**: Manually trigger any UI highlight target.
- **Context Inspector**: View the raw JSON data being sent to the AI.
- **Mock Step Tester**: Test navigation UI without consuming API credits.

---

## 🤝 Contributions

**BlenderMentor** was originally created by [bijuneyyan](https://github.com/bijuneyyan). 

Contributions are welcome! If you'd like to help improve the project, please check out our [Contributing Guide](CONTRIBUTING.md) for details on how to get started, our development workflow, and coding standards.

## 🛡 Privacy

Your privacy is important. BlenderMentor is designed to be as transparent as possible:
- **Direct Communication**: All data is sent directly from your computer to the AI provider. No intermediate servers are used.
- **Local Storage**: API keys are stored only in your local Blender preferences.
- **Zero Telemetry**: We do not track your usage or collect any personal data.

For more details, see our full [Privacy Notice](PRIVACY.md).

---

## 📄 License

This project is licensed under the **GPL v3** License — a requirement for all Blender add-ons interacting with `bpy`. See the [LICENSE](LICENSE) file for details.
