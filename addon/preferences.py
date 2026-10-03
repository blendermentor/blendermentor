# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Addon Preferences (API keys, provider, model fetching, dev mode)

import bpy
import urllib.request
import urllib.error
import json
import ssl


# ---------------------------------------------------------------------------
# Dynamic model list stored per-session (not saved to .blend)
# ---------------------------------------------------------------------------

class ModelItem(bpy.types.PropertyGroup):
    """A single model entry fetched from the provider API."""
    name: bpy.props.StringProperty(name="Name")
    model_id: bpy.props.StringProperty(name="Model ID")


# ---------------------------------------------------------------------------
# Operators — Fetch Models / Test Connection
# ---------------------------------------------------------------------------

class BLENDERMENTOR_OT_fetch_models(bpy.types.Operator):
    bl_idname = "blendermentor.fetch_models"
    bl_label = "Fetch Models"
    bl_description = "Fetch available models from the selected AI provider"

    def execute(self, context):
        import bpy
        if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
            self.report({'ERROR'}, "Internet access is disabled in Blender's System Preferences (Allow Internet Access).")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__package__].preferences
        api_key = prefs.api_key

        # Ollama doesn't need an API key
        if prefs.provider != 'OLLAMA' and not api_key:
            self.report({'ERROR'}, "Please enter an API key first.")
            return {'CANCELLED'}

        try:
            models = self._fetch(prefs.provider, api_key, prefs.ollama_host)
        except urllib.error.URLError as e:
            if prefs.provider == 'OLLAMA':
                self.report({'ERROR'},
                            f"Cannot reach Ollama at {prefs.ollama_host}. "
                            f"Is Ollama running? ({e.reason})")
            else:
                self.report({'ERROR'}, f"Failed to fetch models: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to fetch models: {e}")
            return {'CANCELLED'}

        prefs.fetched_models.clear()
        for model_id, display in models:
            item = prefs.fetched_models.add()
            item.name = display
            item.model_id = model_id

        if models:
            prefs.selected_model_index = 0
            self.report({'INFO'}, f"Fetched {len(models)} models.")
        else:
            self.report({'WARNING'}, "No models found.")

        return {'FINISHED'}

    # -- provider-specific fetch logic --
    @staticmethod
    def _fetch(provider, api_key, ollama_host=""):
        """Return list of (model_id, display_name) tuples."""
        ctx = ssl.create_default_context()

        if provider == 'GEMINI':
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models"
                f"?key={api_key}"
            )
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode())
            results = []
            for m in data.get("models", []):
                mid = m.get("name", "").replace("models/", "")
                if "generateContent" in str(m.get("supportedGenerationMethods", [])):
                    results.append((mid, m.get("displayName", mid)))
            return results

        elif provider == 'CLAUDE':
            url = "https://api.anthropic.com/v1/models"
            req = urllib.request.Request(url, headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            })
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                data = json.loads(resp.read().decode())
            results = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                results.append((mid, m.get("display_name", mid)))
            return results

        elif provider == 'OLLAMA':
            host = (ollama_host or "http://localhost:11434").rstrip("/")
            url = f"{host}/api/tags"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            results = []
            for m in data.get("models", []):
                name = m.get("name", "")
                # Display: "llama3:8b (4.7 GB)" when size info is available
                size_gb = m.get("size", 0) / (1024 ** 3)
                display = f"{name} ({size_gb:.1f} GB)" if size_gb > 0 else name
                results.append((name, display))
            return results

        return []


class BLENDERMENTOR_OT_test_connection(bpy.types.Operator):
    bl_idname = "blendermentor.test_connection"
    bl_label = "Test Connection"
    bl_description = "Send a minimal request to verify the API key works"

    def execute(self, context):
        import bpy
        if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
            self.report({'ERROR'}, "Internet access is disabled in Blender's System Preferences (Allow Internet Access).")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__package__].preferences
        api_key = prefs.api_key

        # Ollama doesn't need an API key
        if prefs.provider != 'OLLAMA' and not api_key:
            self.report({'ERROR'}, "Please enter an API key first.")
            return {'CANCELLED'}

        model_id = prefs.get_selected_model_id()

        try:
            ctx = ssl.create_default_context()

            if prefs.provider == 'GEMINI':
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{model_id}:generateContent?key={api_key}"
                )
                body = json.dumps({
                    "contents": [{"parts": [{"text": "Say OK"}]}]
                }).encode()
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    resp.read()
                self.report({'INFO'}, "✓ Gemini connection successful!")

            elif prefs.provider == 'CLAUDE':
                url = "https://api.anthropic.com/v1/messages"
                body = json.dumps({
                    "model": model_id,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "Say OK"}],
                }).encode()
                req = urllib.request.Request(url, data=body, headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }, method="POST")
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    resp.read()
                self.report({'INFO'}, "✓ Claude connection successful!")

            elif prefs.provider == 'OLLAMA':
                host = (prefs.ollama_host or "http://localhost:11434").rstrip("/")
                url = f"{host}/api/chat"
                body = json.dumps({
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "stream": False,
                }).encode()
                req = urllib.request.Request(
                    url, data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                self.report({'INFO'}, f"✓ Ollama connection successful ({model_id})!")

        except urllib.error.URLError as e:
            if prefs.provider == 'OLLAMA':
                self.report({'ERROR'},
                            f"Cannot reach Ollama at {prefs.ollama_host}. "
                            f"Is Ollama running?")
            else:
                self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except urllib.error.HTTPError as e:
            self.report({'ERROR'}, f"HTTP {e.code}: {e.reason}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        return {'FINISHED'}


class BLENDERMENTOR_OT_open_api_link(bpy.types.Operator):
    bl_idname = "blendermentor.open_api_link"
    bl_label = "Get API Key"
    bl_description = "Open the API key page in your browser"

    def execute(self, context):
        import webbrowser
        prefs = context.preferences.addons[__package__].preferences
        if prefs.provider == 'GEMINI':
            webbrowser.open("https://aistudio.google.com")
        elif prefs.provider == 'CLAUDE':
            webbrowser.open("https://console.anthropic.com")
        else:  # OLLAMA
            webbrowser.open("https://ollama.com")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Callback for dynamic EnumProperty (model selector)
# ---------------------------------------------------------------------------

def _model_enum_items(self, context):
    """Generate enum items from the fetched_models collection."""
    items = []
    for i, m in enumerate(self.fetched_models):
        items.append((m.model_id, m.name, "", i))
    if not items:
        items.append(("NONE", "(click Fetch Models)", "", 0))
    return items


# ---------------------------------------------------------------------------
# AddonPreferences
# ---------------------------------------------------------------------------

class BlenderMentorPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__  # "addon"

    provider: bpy.props.EnumProperty(
        name="AI Provider",
        items=[
            ('GEMINI', "Gemini", "Google Gemini API"),
            ('CLAUDE', "Claude", "Anthropic Claude API"),
            ('OLLAMA', "Ollama (Local)", "Local AI via Ollama — no API key needed"),
        ],
        default='GEMINI',
    )

    api_key: bpy.props.StringProperty(
        name="API Key",
        description="Your API key (stored locally, never in .blend files)",
        subtype='PASSWORD',
        default="",
    )

    ollama_host: bpy.props.StringProperty(
        name="Ollama Host",
        description="URL of your local Ollama server",
        default="http://localhost:11434",
    )

    fetched_models: bpy.props.CollectionProperty(type=ModelItem)
    selected_model_index: bpy.props.IntProperty(default=0)

    selected_model: bpy.props.EnumProperty(
        name="Model",
        items=_model_enum_items,
    )

    developer_mode: bpy.props.BoolProperty(
        name="Developer Mode",
        description="Enable dev tools in the chat panel",
        default=False,
    )

    allow_python_eval: bpy.props.BoolProperty(
        name="Enable AI Python Evaluation",
        description="Allows the AI to execute arbitrary Python expressions to inspect the scene. Extremely powerful, but use with caution",
        default=True,
    )

    enable_web_search: bpy.props.BoolProperty(
        name="Enable Web Search",
        description="Allow the AI to search the web, Blender docs, and community Q&A for up-to-date information. Disable for privacy or to reduce API costs",
        default=True,
    )

    auto_open_browser: bpy.props.BoolProperty(
        name="Auto-open Browser Companion",
        description="Automatically open the Browser Companion in your web browser when Blender starts",
        default=False,
    )

    def get_selected_model_id(self) -> str:
        """Return the model id string from the enum or a sensible default."""
        try:
            return self.selected_model
        except Exception:
            if self.provider == 'GEMINI':
                return "gemini-2.0-flash"
            elif self.provider == 'OLLAMA':
                return "llama3"
            return "claude-sonnet-4-20250514"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        # Provider
        layout.prop(self, "provider")

        if self.provider == 'OLLAMA':
            # Ollama: show host URL instead of API key
            layout.prop(self, "ollama_host")
            row = layout.row(align=True)
            row.operator("blendermentor.open_api_link",
                         text="Ollama Website", icon='URL')
        else:
            # Cloud providers: show API key
            row = layout.row(align=True)
            row.prop(self, "api_key")
            row.operator("blendermentor.open_api_link", text="", icon='URL')

        # Model selector + fetch
        row = layout.row(align=True)
        row.prop(self, "selected_model", text="Model")
        row.operator("blendermentor.fetch_models", text="", icon='FILE_REFRESH')

        # Test
        layout.operator("blendermentor.test_connection", icon='CHECKMARK')

        layout.separator()
        box = layout.box()
        box.label(text="Advanced Settings", icon='PREFERENCES')
        box.prop(self, "developer_mode")
        box.prop(self, "allow_python_eval")
        box.prop(self, "enable_web_search")
        box.prop(self, "auto_open_browser")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = (
    ModelItem,
    BLENDERMENTOR_OT_fetch_models,
    BLENDERMENTOR_OT_test_connection,
    BLENDERMENTOR_OT_open_api_link,
    BlenderMentorPreferences,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
