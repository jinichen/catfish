"""get_config() 缓存/失效行为 (7/30).

这些测试盯的是一个具体事故形态: 同一进程里存在两套配置真相 ——
app.state.config 是启动快照, load_config() 每次重读 —— 导致改完配置后
"一部分代码看到新值, 另一部分没看到", 且走到哪条路径是随机的。

统一到 get_config() 之后, 下面这几条就是它必须守住的契约。
"""
from __future__ import annotations

import textwrap
import time

import pytest

from catfish_gateway import config as C


def _write_yaml(path, model_name: str, display: str = "测试模型") -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            models:
              - name: {model_name}
                tier: public
                display_name: "{display}"
                mode: chat
                default: true
                upstream:
                  model: openai/whatever
                  api_base: https://example.invalid/v1
                  api_key_env: TEST_KEY
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """一个临时 models.yaml + 干净的模块级缓存."""
    p = tmp_path / "models.yaml"
    _write_yaml(p, "model-a")
    monkeypatch.setenv("CATFISH_CONFIG", str(p))
    # 模块级缓存跨测试会串, 每个用例开始前清干净
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0
    yield p
    C._CACHE = None
    C._CACHE_STAMP = None
    C._CACHE_CHECKED_AT = 0.0


def test_首次加载(cfg_file, monkeypatch):
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    cfg = C.get_config()
    assert [m.name for m in cfg.models] == ["model-a"]


def test_ttl_内不回源(cfg_file, monkeypatch):
    """TTL 内改文件不应该被看到 —— 这是缓存有效的证明, 不是 bug."""
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "60")
    assert C.get_config().models[0].name == "model-a"
    _write_yaml(cfg_file, "model-b")
    assert C.get_config().models[0].name == "model-a", "TTL 内不该回源"


def test_ttl_过后能看到改动(cfg_file, monkeypatch):
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")  # 0 = 每次都查
    assert C.get_config().models[0].name == "model-a"
    time.sleep(0.01)  # 确保 mtime 有变化
    _write_yaml(cfg_file, "model-b")
    assert C.get_config().models[0].name == "model-b"


def test_invalidate_立刻生效(cfg_file, monkeypatch):
    """写接口改完配置调 invalidate, 本 worker 必须立刻看到新值."""
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "60")
    assert C.get_config().models[0].name == "model-a"
    time.sleep(0.01)
    _write_yaml(cfg_file, "model-b")
    assert C.get_config().models[0].name == "model-a"  # TTL 还没到
    C.invalidate_config()
    assert C.get_config().models[0].name == "model-b"


def test_内容没变则不重新解析(cfg_file, monkeypatch):
    """过了 TTL 但文件没动 → 只 stat, 不该重新解析.

    用"返回的是同一个对象"来证明没重新构造过。
    """
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    first = C.get_config()
    second = C.get_config()
    assert first is second, "源没变时不该重新解析出新对象"


def test_配置写坏时沿用上一份而不是全站挂(cfg_file, monkeypatch):
    """配置被写坏是运维事故, 不该演变成 gateway 全站 502."""
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    assert C.get_config().models[0].name == "model-a"
    time.sleep(0.01)
    cfg_file.write_text("{{{ 这不是合法 yaml", encoding="utf-8")
    cfg = C.get_config()
    assert cfg.models[0].name == "model-a", "坏配置时应沿用上一份好的"


def test_写坏后修好能自动恢复(cfg_file, monkeypatch):
    """沿用旧配置不能是"从此不再回源" —— 修好后要能自己好."""
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    C.get_config()
    time.sleep(0.01)
    cfg_file.write_text("{{{ 坏的", encoding="utf-8")
    C.get_config()  # 沿用旧的
    time.sleep(0.01)
    _write_yaml(cfg_file, "model-c")
    assert C.get_config().models[0].name == "model-c", "修好后应自动恢复"


def test_首次加载就失败必须抛(cfg_file, monkeypatch):
    """没有"上一份好的"可沿用时, 不能假装正常 —— 必须让它挂掉."""
    monkeypatch.setenv("CATFISH_CONFIG_TTL", "0")
    cfg_file.write_text("{{{ 坏的", encoding="utf-8")
    C._CACHE = None
    with pytest.raises(Exception):
        C.get_config()


def test_load_config_显式路径仍然可用(tmp_path):
    """测试依赖它按显式 path 加载, 不能因为加了缓存就破坏这个用法."""
    p = tmp_path / "other.yaml"
    _write_yaml(p, "model-explicit")
    cfg = C.load_config(p)
    assert cfg.models[0].name == "model-explicit"
