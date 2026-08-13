from __future__ import annotations

import argparse
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _tail(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-4000:].strip()
    except OSError:
        return f"could not read diagnostic file: {path}"


class Handler(BaseHTTPRequestHandler):
    health_only = False
    upstream_port: int | None = None

    def _write(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self) -> bool:
        if self.upstream_port is None:
            return False

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        headers: dict[str, str] = {}
        content_type = self.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type

        connection = http.client.HTTPConnection("127.0.0.1", self.upstream_port, timeout=2)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            self.send_response(response.status)
            for name in ("Content-Type", "Retry-After"):
                value = response.getheader(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            return True
        except (ConnectionError, OSError, http.client.HTTPException):
            return False
        finally:
            connection.close()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = self.path.split("?", 1)[0]
        if self.health_only and path == "/ping":
            self._write(200, {"status": "healthy", "api_status": "starting"})
            return
        if not self.health_only and self._proxy():
            return
        if path == "/ping":
            self._write(200, {"status": "healthy", "api_status": "starting"})
            return
        detail = _tail(Path("/tmp/lb-api.failed")) or "Load balancer API is still starting"
        self._write(503, {"status": "starting", "error": detail})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if not self.health_only and self._proxy():
            return
        detail = _tail(Path("/tmp/lb-api.failed")) or "Load balancer API is still starting"
        self._write(503, {"detail": {"code": "API_STARTING", "message": detail, "retryable": True}})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--health-only", action="store_true")
    parser.add_argument("--upstream-port", type=int)
    args = parser.parse_args()
    Handler.health_only = args.health_only
    Handler.upstream_port = args.upstream_port
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
