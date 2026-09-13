"""
A small, real local HTTP server standing in for AgentHive itself - just
enough of the wire protocol (auth header, the two endpoints
mcp_server.py calls, the pending/approve/retrieve shape, and the error
response format) to prove this repo's own HTTP plumbing is correct: the
right method/path/headers/body go out, and the right thing comes back
for a success, an auth failure, and an unreachable host.

This is NOT a test of AgentHive's own behavior (the approval gate, team
isolation, the retrieval algorithm, etc. - that's AgentHive's own test
suite, in its own repo, against the real server.py). Real end-to-end
testing against a real running AgentHive service is still the way to
verify this wrapper works against the genuine article; this fake exists
only so this repo's tests don't need that other repo checked out.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class FakeAgentHive:
    def __init__(self, token: str, team_id: str):
        self.token = token
        self.team_id = team_id
        self.nodes = {}
        self._next_id = 1

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # keep test output quiet

            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                return self.headers.get("X-API-Key") == server.token

            def do_POST(self):
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}

                if not self._authed():
                    self._send_json(401, {"error": "invalid, expired, or revoked API key"})
                    return

                if parsed.path == f"/teams/{server.team_id}/memory":
                    node_id = str(server._next_id)
                    server._next_id += 1
                    server.nodes[node_id] = {
                        "id": node_id,
                        "title": body.get("title"),
                        "body": body.get("body"),
                        "tags": body.get("tags", []),
                        "links": body.get("links", []),
                        "status": "pending",
                    }
                    self._send_json(200, dict(server.nodes[node_id]))
                    return

                if parsed.path.startswith(f"/teams/{server.team_id}/memory/") and parsed.path.endswith("/approve"):
                    node_id = parsed.path.split("/")[-2]
                    if node_id not in server.nodes:
                        self._send_json(404, {"error": "not found"})
                        return
                    server.nodes[node_id]["status"] = "approved"
                    self._send_json(200, dict(server.nodes[node_id]))
                    return

                self._send_json(404, {"error": "not found"})

            def do_GET(self):
                parsed = urlparse(self.path)

                if not self._authed():
                    self._send_json(401, {"error": "invalid, expired, or revoked API key"})
                    return

                if parsed.path == f"/teams/{server.team_id}/memory/retrieve":
                    q = parse_qs(parsed.query)
                    anchor = q.get("anchor", [""])[0]
                    matched = [
                        n for n in server.nodes.values()
                        if n["status"] == "approved" and anchor.lower() in (n["title"] or "").lower()
                    ]
                    self._send_json(200, {
                        "neighborhood": matched,
                        "neighborhood_count": len(matched),
                        "approx_tokens": sum(len((n["body"] or "")) // 4 for n in matched),
                    })
                    return

                self._send_json(404, {"error": "not found"})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
