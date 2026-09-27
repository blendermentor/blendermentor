# Privacy Notice for BlenderMentor

**Last Updated: May 15, 2026**

BlenderMentor ("the Addon") is a self-contained Blender addon designed to teach you how to use Blender through an AI-powered interface. Your privacy is a core design principle: the Addon communicates directly with your chosen AI provider and does not use any intermediate servers.

---

## 1. Information Processed

To provide its functionality, the Addon processes the following types of information:

### A. Scene Context
When you ask a question, the Addon reads the current state of your Blender scene to provide relevant guidance. This includes:
- Names and types of objects in your scene (e.g., "Cube", "Mesh").
- Active materials and modifiers.
- The current editor you are using (e.g., 3D Viewport, Properties).
- Current frame and animation status.
- UI state (which panels are visible).

### B. User Queries
The text questions you type into the chat panel.

### C. API Configuration
Your AI provider choice, API keys, or local host addresses.

---

## 2. Data Transmission

BlenderMentor **does not** have a central server. All communication happens directly from your computer to the AI provider you have configured:

- **Google Gemini API**: Data is sent directly to Google.
- **Anthropic Claude API**: Data is sent directly to Anthropic.
- **OpenAI API**: Data is sent directly to OpenAI.
- **Ollama (Local)**: Data is sent to your **locally running Ollama server**. In this mode, no data leaves your local machine or network unless your Ollama instance is configured to do so. This is the most private way to use BlenderMentor.

The information sent includes your **Scene Context** and your **User Query**. This is necessary for the AI to understand your current situation and provide accurate steps.

**We do not collect, store, or intercept your API keys or your conversations.**

---

## 3. Local Storage

The Addon stores data locally on your machine within Blender:

- **API Keys**: Stored in Blender's `AddonPreferences`. These are saved on your local disk as part of Blender's configuration and are never included in `.blend` files.
- **Conversation History**: Stored within the `bpy.types.Scene` properties. This means your chat history **is saved inside the .blend file**. If you share your `.blend` file with others, they may be able to see your past conversations with the mentor.
- **Model Lists**: The list of available models fetched from the API is stored temporarily in your local preferences.

---

## 4. Third-Party Privacy Policies

Since data is sent directly to AI providers, your use of the Addon is subject to their respective privacy policies and terms of service:
- [Google Privacy Policy](https://policies.google.com/privacy)
- [Anthropic Privacy Policy](https://www.anthropic.com/privacy)

---

## 5. Your Controls

- **Clear Conversations**: You can use the "Clear" button in the chat panel to delete the conversation history from the current scene.
- **Remove API Keys**: You can delete or change your API keys at any time in `Edit > Preferences > Add-ons > BlenderMentor`.
- **Developer Mode**: When enabled, you can see exactly what "Scene Context" is being sent to the AI before you send a message.

---

## 6. Telemetry and Tracking

BlenderMentor contains **no telemetry, no tracking pixels, and no analytics**. We do not know who is using the addon or how many questions are being asked.

---

## 7. Contact

As an open-source project, any concerns regarding privacy can be raised via the project's issue tracker on GitHub.
