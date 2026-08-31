"""network.py 单元测试 —— 不需要真代理，全模拟。"""
from __future__ import annotations

import os
import socket
from unittest import mock

import pytest

from catfish_gateway import network


# ----- _parse_proxy_url -----

@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://127.0.0.1:7890", ("127.0.0.1", 7890)),
        ("https://proxy.corp.com:8443", ("proxy.corp.com", 8443)),
        ("127.0.0.1:7890", ("127.0.0.1", 7890)),  # 无 scheme 也认
        ("http://127.0.0.1", ("127.0.0.1", 80)),  # 默认 80
        ("https://127.0.0.1", ("127.0.0.1", 443)),  # 默认 443
        ("", None),
        ("not-a-url", ("not-a-url", 80)),  # 实际加 http:// 前缀后 urlparse 接受任何字符串
    ],
)
def test_parse_proxy_url(url, expected):
    assert network._parse_proxy_url(url) == expected


# ----- _check_tcp_port -----

def test_check_tcp_port_alive():
    """监听一个临时端口验证 _check_tcp_port 返回 True。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert network._check_tcp_port("127.0.0.1", port, timeout=1.0) is True


def test_check_tcp_port_dead():
    """探一个肯定没人听的端口（uint16 上限附近随机选一个）。"""
    # 0.0.0.0 同一机器但选个非常用端口
    assert network._check_tcp_port("127.0.0.1", 1, timeout=0.5) is False  # port 1 通常没服务


# ----- precheck_and_setup -----

def test_precheck_no_proxy_env(monkeypatch):
    """没设代理 → verdict=none，NO_PROXY 加进内网网段。"""
    for var in network.PROXY_VARS + ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)

    status = network.precheck_and_setup()
    assert status["verdict"] == "none"
    assert "无代理" in status["proxy"]
    # NO_PROXY 已被设置
    assert "10.0.0.0/8" in os.environ["NO_PROXY"]
    assert "127.0.0.1" in os.environ["NO_PROXY"]


def test_precheck_dead_proxy_unsets(monkeypatch):
    """代理 unreachable → 自动 unset。"""
    for var in network.PROXY_VARS + ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")  # port 1 几乎没人听

    status = network.precheck_and_setup()
    assert status["verdict"] == "fixed"
    assert "已清除" in status["proxy"]
    # 所有 proxy 变量都被清掉
    for var in network.PROXY_VARS:
        assert os.environ.get(var) is None or os.environ.get(var) == ""


def test_precheck_alive_proxy_keeps(monkeypatch):
    """代理活着 → 保留 + verdict=ok。"""
    # 起一个临时 listener 当假代理
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        for var in network.PROXY_VARS + ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")

        status = network.precheck_and_setup()

    assert status["verdict"] == "ok"
    assert "代理可达" in status["proxy"]
    # 代理变量保留
    assert "HTTPS_PROXY" in os.environ


def test_precheck_merges_existing_no_proxy(monkeypatch):
    """如果员工已经设了 NO_PROXY，要合并不能丢。"""
    for var in network.PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "internal.corp,my-server")
    monkeypatch.delenv("no_proxy", raising=False)

    network.precheck_and_setup()
    final = os.environ["NO_PROXY"]
    # 员工原有的保留
    assert "internal.corp" in final
    assert "my-server" in final
    # 我们加的内网也在
    assert "10.0.0.0/8" in final
    assert "127.0.0.1" in final


def test_precheck_no_proxy_no_dup(monkeypatch):
    """重复条目去重。"""
    for var in network.PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,10.0.0.0/8")

    network.precheck_and_setup()
    final = os.environ["NO_PROXY"]
    # 各只出现一次
    assert final.count("127.0.0.1") == 1
    assert final.count("10.0.0.0/8") == 1


def test_precheck_sanitizes_smart_quotes_and_invalid_wildcard(monkeypatch):
    """富文本复制产生的 *“localhost 不得让 httpx 解析 NO_PROXY 失败。"""
    for var in network.PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*“localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "“localhost”,127.0.0.1")

    network.precheck_and_setup()

    final = os.environ["NO_PROXY"]
    assert final.split(",").count("localhost") == 1
    assert final.split(",").count("127.0.0.1") == 1
    assert "“" not in final
    assert "*localhost" not in final
