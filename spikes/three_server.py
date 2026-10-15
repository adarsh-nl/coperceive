from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
from pathlib import Path
OUT = Path("/tmp/spikes/viewer/three_result.json")
class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory="/tmp/spikes/viewer", **k)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        OUT.write_bytes(raw)
        self.send_response(204)
        self.end_headers()
        print("RESULT", raw.decode(), flush=True)
    def log_message(self, fmt, *args):
        return
ThreadingHTTPServer(("127.0.0.1", 8767), H).serve_forever()
