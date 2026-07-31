#!/usr/bin/env python3
"""Minimaler TLS-Proxy: HTTPS auf 9443 -> HTTP auf 9090.

Usage: OWNTRACKS_TLS_CERT=... OWNTRACKS_TLS_KEY=... python3 tls_proxy.py
"""

from __future__ import annotations

import http.client
import http.server
import os
import ssl
import sys

TLS_CERT = os.environ.get("OWNTRACKS_TLS_CERT")
TLS_KEY = os.environ.get("OWNTRACKS_TLS_KEY")
TLS_PORT = int(os.environ.get("OWNTRACKS_TLS_PORT", "9443"))
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = int(os.environ.get("OWNTRACKS_PORT", "9090"))

if not TLS_CERT or not TLS_KEY:
    print("FATAL: OWNTRACKS_TLS_CERT and OWNTRACKS_TLS_KEY required", file=sys.stderr)
    sys.exit(1)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        self._proxy(body)

    def _proxy(self, body: bytes | None = None):
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=10)
            conn.request(self.command, self.path, body=body, headers=dict(self.headers))
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
            conn.close()
        except Exception as exc:
            self.send_error(502, f"Proxy error: {exc}")

    def log_message(self, fmt, *args):
        pass  # quiet


def main():
    server = http.server.HTTPServer(("0.0.0.0", TLS_PORT), ProxyHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(TLS_CERT, TLS_KEY)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"TLS proxy: 0.0.0.0:{TLS_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()