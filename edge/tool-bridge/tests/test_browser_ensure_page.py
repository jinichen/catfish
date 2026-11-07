"""11/07: 鲶鱼 Chrome 窗口全关了 (mac 上进程还在) → 连之前先开一个空白页, 否则 Playwright 连都连不上。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from catfish_tool_bridge.catfish_tools_browser import _ensure_open_page


@pytest.fixture
def fake_chrome():
    state = {"targets": [], "calls": []}

    class H(BaseHTTPRequestHandler):
        def _reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state["calls"].append(("GET", self.path))
            self._reply(state["targets"])

        def do_PUT(self):
            state["calls"].append(("PUT", self.path))
            t = {"id": "N", "type": "page", "url": "about:blank"}
            state["targets"].append(t)
            self._reply(t)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", state
    srv.shutdown()


def test_no_page_opens_blank(fake_chrome):
    base, state = fake_chrome
    state["targets"] = [{"type": "browser_ui", "url": "chrome://omnibox-popup.top-chrome/"}]
    assert _ensure_open_page(base) is True
    assert ("PUT", "/json/new?about:blank") in state["calls"]


def test_has_page_does_nothing(fake_chrome):
    base, state = fake_chrome
    state["targets"] = [{"type": "page", "url": "https://business.sohu.com/"}]
    assert _ensure_open_page(base) is False
    assert all(m == "GET" for m, _ in state["calls"])


def test_chrome_not_running_is_silent():
    # 端口没人听: 不报错, 交给后面的连接报原本的「Chrome 没起」
    assert _ensure_open_page("http://127.0.0.1:9") is False
