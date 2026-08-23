import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8000"))
TARGETS = [t.strip() for t in os.environ.get(
"KEEPALIVE_TARGETS",
"https://eaglex-backend-excn.onrender.com/health").split(",") if t.strip()]
PROBE = os.environ.get("PROBE_URL", "")

LAST = {"probe": "not run"}


def probe():
    if not PROBE:
        return
    try:
        req = urllib.request.Request(PROBE, headers={"User-Agent": "probe"})
        with urllib.request.urlopen(req, timeout=20) as r:
            LAST["probe"] = "%s" % r.status
    except Exception as e:
        code = getattr(e, "code", None)
        LAST["probe"] = "HTTP %s" % code if code else "ERR %s" % e


def ping_loop():
    while True:
        for t in TARGETS:
            try:
                with urllib.request.urlopen(t, timeout=20) as r:
                    print("ping", t, r.status)
            except Exception as e:
                print("ping failed", t, e)
        probe()
        time.sleep(600)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(("ok probe=" + str(LAST["probe"])).encode())
    def log_message(self, *a):
        pass


if __name__ == "__main__":
    probe()
    threading.Thread(target=ping_loop, daemon=True).start()
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
