import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lb_fallback import Handler


class UpstreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"status": "ready"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_gateway_stays_live_when_app_is_starting():
    gateway_handler = type("StartingGatewayHandler", (Handler,), {"health_only": False, "upstream_port": 1})
    gateway = start_server(gateway_handler)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{gateway.server_port}/ping", timeout=2) as response:
            assert response.status == 200
            assert json.load(response)["api_status"] == "starting"

        with urllib.request.urlopen(f"http://127.0.0.1:{gateway.server_port}/ready", timeout=2) as response:
            raise AssertionError(f"expected HTTP 503, got {response.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
        assert json.load(exc)["status"] == "starting"
    finally:
        gateway.shutdown()


def test_gateway_proxies_to_loaded_app():
    upstream = start_server(UpstreamHandler)
    gateway_handler = type(
        "ReadyGatewayHandler", (Handler,), {"health_only": False, "upstream_port": upstream.server_port}
    )
    gateway = start_server(gateway_handler)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{gateway.server_port}/ready", timeout=2) as response:
            assert response.status == 200
            assert json.load(response) == {"status": "ready"}

        payload = json.dumps({"text": "test"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{gateway.server_port}/tts",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert response.status == 201
            assert json.load(response) == {"text": "test"}
    finally:
        gateway.shutdown()
        upstream.shutdown()
