# SPDX-License-Identifier: GPL-3.0-or-later
# BlenderMentor — Embedded HTTP Server for Browser Companion Window

import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import bpy

_server = None
_server_thread = None
SERVER_PORT = 8765


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads for responsiveness."""
    daemon_threads = True
    allow_reuse_address = True


class BlenderMentorHTTPHandler(BaseHTTPRequestHandler):
    """Serves the companion Web App and provides bi-directional REST endpoints."""

    def log_message(self, format, *args):
        # Suppress routine HTTP request logging in Blender terminal
        pass

    def _set_cors_headers(self, content_type="application/json"):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Type", content_type)

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        clean_path = self.path.split("?")[0].lstrip("/")

        # 1. Web companion single-page app or static asset
        web_dir = os.path.join(os.path.dirname(__file__), "web")
        if self.path in ("/", "/index.html", ""):
            index_path = os.path.join(web_dir, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self._set_cors_headers("text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                self.send_response(404)
                self._set_cors_headers("text/plain")
                self.end_headers()
                self.wfile.write(b"Web companion UI file not found.")
                return

        # Static assets in web/ (e.g. blender_icons.js, css, etc.)
        candidate_web_file = os.path.abspath(os.path.join(web_dir, clean_path))
        if candidate_web_file.startswith(web_dir) and os.path.isfile(candidate_web_file):
            ext = os.path.splitext(candidate_web_file)[1].lower()
            mime_map = {
                ".js": "application/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json; charset=utf-8",
                ".svg": "image/svg+xml",
                ".html": "text/html; charset=utf-8",
                ".png": "image/png",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            with open(candidate_web_file, "rb") as f:
                content = f.read()
            self.send_response(200)
            self._set_cors_headers(mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # Static official SVG icons from addon/icons/
        if self.path.startswith("/icons/"):
            icon_file = os.path.basename(clean_path)
            icons_dir = os.path.join(os.path.dirname(__file__), "icons")
            candidate_icon_file = os.path.abspath(os.path.join(icons_dir, icon_file))
            if candidate_icon_file.startswith(icons_dir) and os.path.isfile(candidate_icon_file):
                with open(candidate_icon_file, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self._set_cors_headers("image/svg+xml")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # 2. Health check
        if self.path == "/api/health":
            data = {"status": "ok", "app": "BlenderMentor", "blender_version": ".".join(map(str, bpy.app.version))}
            payload = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self._set_cors_headers()
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # 3. Synchronized State Endpoint
        if self.path == "/api/state":
            try:
                scene = getattr(bpy.context, "scene", None)
                act_obj = getattr(bpy.context, "active_object", None)
                mode = getattr(bpy.context, "mode", "OBJECT")

                chat_data = []
                steps_data = []
                current_step = 0
                is_processing = False
                status_msg = ""
                youtube_query = ""

                if scene:
                    current_step = getattr(scene, "bm_current_step", 0)
                    is_processing = getattr(scene, "bm_is_processing", False)
                    status_msg = getattr(scene, "bm_status_message", "")
                    youtube_query = getattr(scene, "bm_youtube_query", "")

                    for msg in scene.bm_chat_history:
                        chat_data.append({"text": msg.text, "is_user": msg.is_user})

                    for s in scene.bm_steps:
                        hl = None
                        if s.highlight_json:
                            try:
                                hl = json.loads(s.highlight_json)
                            except Exception:
                                pass
                        steps_data.append({
                            "instruction": s.instruction,
                            "description": s.description,
                            "is_done": s.is_done,
                            "icon": getattr(s, "icon", "NONE"),
                            "highlight": hl,
                        })

                data = {
                    "active_object": act_obj.name if act_obj else None,
                    "active_object_type": act_obj.type if act_obj else None,
                    "mode": mode,
                    "chat_history": chat_data,
                    "steps": steps_data,
                    "current_step": current_step,
                    "is_processing": is_processing,
                    "status_message": status_msg,
                    "youtube_query": youtube_query,
                }
                payload = json.dumps(data).encode("utf-8")
                self.send_response(200)
                self._set_cors_headers()
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            except Exception as e:
                err_payload = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self._set_cors_headers()
                self.send_header("Content-Length", str(len(err_payload)))
                self.end_headers()
                self.wfile.write(err_payload)
                return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            req_data = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            req_data = {}

        # 1. Send chat message / voice transcript from browser
        if self.path == "/api/chat":
            msg_text = req_data.get("message", "").strip()
            if not msg_text:
                self.send_response(400)
                self._set_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Empty message"}).encode("utf-8"))
                return

            def _schedule_message():
                scene = getattr(bpy.context, "scene", None)
                if scene:
                    scene.bm_input_text = msg_text
                    try:
                        bpy.ops.blendermentor.send_message('EXEC_DEFAULT')
                    except Exception as e:
                        print(f"[BlenderMentor] Error dispatching send_message: {e}")
                return None

            bpy.app.timers.register(_schedule_message, first_interval=0.001)

            self.send_response(200)
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "queued", "message": msg_text}).encode("utf-8"))
            return

        # 2. Step navigation from browser
        if self.path == "/api/step":
            action = req_data.get("action")
            step_idx = req_data.get("step")

            def _schedule_step():
                scene = getattr(bpy.context, "scene", None)
                if not scene:
                    return None
                if action == "next":
                    bpy.ops.blendermentor.step_next('EXEC_DEFAULT')
                elif action == "prev":
                    bpy.ops.blendermentor.step_prev('EXEC_DEFAULT')
                elif step_idx is not None and 0 <= step_idx < len(scene.bm_steps):
                    scene.bm_current_step = step_idx
                    from .ui.chat_panel import _activate_step
                    _activate_step(scene, step_idx)
                return None

            bpy.app.timers.register(_schedule_step, first_interval=0.001)

            self.send_response(200)
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
            return

        # 3. Clear chat
        if self.path == "/api/clear":
            def _schedule_clear():
                try:
                    bpy.ops.blendermentor.clear_chat('EXEC_DEFAULT')
                except Exception:
                    pass
                return None

            bpy.app.timers.register(_schedule_clear, first_interval=0.001)
            self.send_response(200)
            self._set_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "cleared"}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


def start_server(port=SERVER_PORT):
    """Start the background HTTP server for the browser companion."""
    global _server, _server_thread
    if _server is not None:
        return

    try:
        _server = ThreadedHTTPServer(("127.0.0.1", port), BlenderMentorHTTPHandler)
        _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
        _server_thread.start()
        print(f"[BlenderMentor] Browser companion server active at http://127.0.0.1:{port}")
    except OSError as e:
        print(f"[BlenderMentor] Could not bind server to port {port}: {e}")
        _server = None
        _server_thread = None


def stop_server():
    """Stop the background HTTP server."""
    global _server, _server_thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None
        _server_thread = None
