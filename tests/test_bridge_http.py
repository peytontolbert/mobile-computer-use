"""HTTP smoke tests against a live ``ThreadingHTTPServer``."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from mobile_computer_use.bridge import PROTOCOL, BridgeState, Handler

from tests.conftest import bridge_namespace


@pytest.fixture
def http_server(isolated_config_dir):
    """Bind to port 0; shutdown after test."""
    args = bridge_namespace(isolated_config_dir, port=0)
    state = BridgeState(args)
    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_get_health(http_server) -> None:
    _host, port = http_server.server_address
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
        body = json.loads(resp.read().decode())
    assert body["status"] == "ok"
    assert body["protocol"] == PROTOCOL


def test_post_unknown_path_returns_404(http_server) -> None:
    _host, port = http_server.server_address
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/nope",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "Origin": "https://peytontolbert.com"},
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req)
    assert ei.value.code == 404
