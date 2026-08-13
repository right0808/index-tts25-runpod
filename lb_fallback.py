from __future__ import annotations

import argparse
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

    def _write(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path.split("?", 1)[0] == "/ping":
            self._write(200, {"status": "healthy", "api_status": "fallback" if not self.health_only else "starting"})
            return
        detail = _tail(Path("/tmp/lb-api.failed")) or "Load balancer API is still starting"
        self._write(503, {"status": "failed", "error": detail})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        detail = _tail(Path("/tmp/lb-api.failed")) or "Load balancer API is still starting"
        self._write(503, {"detail": {"code": "API_STARTING", "message": detail, "retryable": True}})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()
    Handler.health_only = args.health_only
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
