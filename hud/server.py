#!/usr/bin/env python3
"""Brong voice screen. Listens in the browser and runs commands for real."""

import json
import os
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import Engine

HOST = "127.0.0.1"
PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
ENGINE = Engine()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/health":
            self._send(200, json.dumps(ENGINE.health()), "application/json")
            return
        if path == "/":
            path = "/index.html"
        relative = path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, relative))
        if os.path.commonpath([full, STATIC]) != STATIC or not os.path.isfile(full):
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        if full.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif full.endswith(".jpg"):
            content_type = "image/jpeg"
        else:
            content_type = "application/octet-stream"
        with open(full, "rb") as handle:
            self._send(200, handle.read(), content_type)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, '{"error":"bad json"}', "application/json")
            return

        path = urllib.parse.urlparse(self.path).path
        if path == "/api/command":
            try:
                result = ENGINE.ask(payload)
            except Exception as error:
                result = {
                    "say": "That didn't go through. Say it once more.",
                    "pending": payload.get("pending"),
                    "city": payload.get("city"),
                    "open": None,
                    "card": None,
                    "error": str(error),
                }
            self._send(200, json.dumps(result), "application/json")
            return

        if path == "/api/open":
            url = payload.get("url") or ""
            if not url.startswith("https://mail.google.com/"):
                self._send(400, '{"ok":false}', "application/json")
                return
            subprocess.run(["open", url], check=False)
            self._send(200, '{"ok":true}', "application/json")
            return

        self._send(404, '{"error":"not found"}', "application/json")


def main():
    ENGINE.start_boot()
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        return 0
    print("Brong voice screen at http://{0}:{1}".format(HOST, PORT), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
