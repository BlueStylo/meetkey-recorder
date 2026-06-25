#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = os.environ.get("MEETKEY_CAPTIVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("MEETKEY_CAPTIVE_PORT", "80"))
DEFAULT_TARGET = os.environ.get("MEETKEY_CAPTIVE_DEFAULT_TARGET", "http://10.42.0.1/records")
TARGET_FILE = Path(os.environ.get("MEETKEY_CAPTIVE_TARGET_FILE", "/home/gunwoo/MeetKey_Recordings/captive_target.txt")).expanduser()
UPSTREAM = os.environ.get("MEETKEY_CAPTIVE_UPSTREAM", "http://127.0.0.1:8000")

CAPTIVE_CHECK_PATHS = {
    "/",
    "/hotspot-detect.html",
    "/generate_204",
    "/gen_204",
    "/ncsi.txt",
    "/connecttest.txt",
    "/canonical.html",
    "/success.txt",
}


def current_target() -> str:
    try:
        value = TARGET_FILE.read_text(encoding="utf-8").strip()
        if value.startswith(("http://", "https://")):
            return value
    except OSError:
        pass
    return DEFAULT_TARGET


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/captive-portal/api":
            return self._capport_api()
        if parsed.path in CAPTIVE_CHECK_PATHS:
            return self._redirect_to_target()
        return self._proxy()

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/captive-portal/api":
            return self._capport_api(head_only=True)
        if parsed.path in CAPTIVE_CHECK_PATHS:
            return self._redirect_to_target(head_only=True)
        return self._proxy(head_only=True)

    def do_POST(self) -> None:
        return self._proxy()

    def _capport_api(self, head_only: bool = False) -> None:
        target = current_target()
        body = json.dumps(
            {
                "captive": True,
                "user-portal-url": target,
                "venue-info-url": target,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/captive+json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _redirect_to_target(self, head_only: bool = False) -> None:
        target = current_target()
        body = f"""<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="refresh" content="1;url={html.escape(target)}" />
    <title>MeetKey</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #18181b;
        color: #f4f4f5;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        width: min(360px, calc(100% - 36px));
        display: grid;
        gap: 16px;
        text-align: center;
      }}
      a {{
        display: inline-flex;
        min-height: 52px;
        align-items: center;
        justify-content: center;
        border-radius: 10px;
        background: #22c55e;
        color: #052e16;
        font-weight: 900;
        text-decoration: none;
      }}
      p {{ color: #a1a1aa; line-height: 1.5; }}
    </style>
  </head>
  <body>
    <main>
      <h1>MeetKey</h1>
      <p>녹음 기록 페이지를 여는 중입니다.</p>
      <a href="{html.escape(target)}">녹음 기록 열기</a>
    </main>
    <script>setTimeout(() => {{ location.replace({target!r}); }}, 300);</script>
  </body>
</html>
""".encode("utf-8")
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", target)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _proxy(self, head_only: bool = False) -> None:
        target = UPSTREAM.rstrip("/") + self.path
        length = int(self.headers.get("content-length", "0") or "0")
        data = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection", "accept-encoding"}
        }
        request = urllib.request.Request(target, data=data, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read() if not head_only else b""
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in {"connection", "transfer-encoding", "content-encoding"}:
                        continue
                    if key.lower() == "content-length":
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            body = exc.read() if not head_only else b""
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "text/plain; charset=utf-8"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
        except Exception as exc:
            body = f"MeetKey app is not ready: {exc}".encode("utf-8")
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MeetKey captive portal running on http://{HOST}:{PORT} -> {DEFAULT_TARGET}")
    server.serve_forever()


if __name__ == "__main__":
    main()
