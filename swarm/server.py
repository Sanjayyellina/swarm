"""Universal input: one HTTP endpoint, any source.

POST /handle  {"message": "...", "session": "optional-id"}
GET  /health

Web forms, SMS gateways, email hooks, other software, cron systems — anything
that can make an HTTP request can talk to this client's swarm. Channels are
just thin adapters that POST here; none of them is special.

Stdlib only — no framework dependency. Run:  python run.py --client X --serve 8080
"""
import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve(swarm, port):
    # One request at a time per client instance: SQLite stays consistent and
    # conversation ordering is preserved. Small-business load never needs more;
    # scale-out is one-instance-per-client anyway.
    handle_lock = threading.Lock()
    # Cost protection: cap requests per minute (each request = LLM spend).
    window = deque()
    rate_limit = int(os.environ.get("SWARM_RATE_LIMIT", "60"))
    # Auth: set SWARM_SERVER_TOKEN in production; requests must send
    # "Authorization: Bearer <token>". Unset = open (dev mode).
    token = os.environ.get("SWARM_SERVER_TOKEN")
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True, "client": swarm.manifest["name"]})
            elif self.path in ("/", "/index.html"):
                page = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "static", "index.html")
                try:
                    with open(page, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    self._send(404, {"error": "dashboard not found"})
            elif self.path == "/api/state":
                if not self._authed():
                    self._send(401, {"error": "unauthorized"})
                    return
                m = swarm.memory
                appts = m.appointments()[:25]
                msgs = m.recent_messages(25)
                jobs = m.recent_jobs(25)
                usage = m.usage_turns()
                events_n = m.event_count()
                self._send(200, {
                    "client": swarm.manifest["name"],
                    "model": getattr(swarm.llm, "model", "?"),
                    "appointments": appts,
                    "approvals": [x for x in msgs
                                  if x["body"].startswith("APPROVAL NEEDED")],
                    "messages": [x for x in msgs
                                 if not x["body"].startswith("APPROVAL NEEDED")],
                    "jobs": jobs, "llm_turns": usage, "event_count": events_n})
            elif self.path.startswith("/api/events"):
                if not self._authed():
                    self._send(401, {"error": "unauthorized"})
                    return
                self._send(200, {"events": swarm.memory.recent_events(60)})
            else:
                self._send(404, {"error": "unknown path"})

        def do_POST(self):
            if self.path != "/handle":
                self._send(404, {"error": "unknown path"})
                return
            if token:
                auth = self.headers.get("Authorization", "")
                if auth != f"Bearer {token}":
                    self._send(401, {"error": "unauthorized"})
                    return
            now = time.time()
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= rate_limit:
                self._send(429, {"error": "rate limit exceeded, try again shortly"})
                return
            window.append(now)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 100_000:  # 100KB cap: no memory-blowup payloads
                    self._send(413, {"error": "payload too large"})
                    return
                data = json.loads(self.rfile.read(length) or b"{}")
                message = data.get("message", "").strip()
                if not message:
                    self._send(400, {"error": "message required"})
                    return
                # Channel identity: adapters declare their channel (sms, email,
                # web, voice...); sessions are namespaced by it so one customer
                # keeps one history per channel today, and a future customers
                # table can link them across channels.
                channel = str(data.get("channel", "web"))[:16]
                session = f"{channel}:{str(data.get('session', 'anon'))[:48]}"
                with handle_lock:
                    reply = swarm.dispatch(message, session=session)
                self._send(200, {"reply": reply, "session": session,
                                 "route": swarm.memory.last_route()})
            except Exception as e:  # noqa: BLE001 — API must answer, never hang
                self._send(500, {"error": str(e)})

        def log_message(self, *args):  # quiet default logging
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Swarm API for '{swarm.manifest['name']}' on http://0.0.0.0:{port}"
          f"  (POST /handle, GET /health)")
    httpd.serve_forever()
