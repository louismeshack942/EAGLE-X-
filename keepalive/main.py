import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

TARGET = os.environ.get("KEEPALIVE_TARGET", "https://eaglex-backend-excn.onrender.com/health")
PORT = int(os.environ.get("PORT", "8000"))


def ping_loop():
    while True:
        try:
            with urllib.request.urlopen(TARGET, timeout=20) as r:
                print("ping", TARGET, r.status)
        except Exception as e:
            print("ping failed:", e)
        time.sleep(600)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=ping_loop, daemon=True).start()
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
