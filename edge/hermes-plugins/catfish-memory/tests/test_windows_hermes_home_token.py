"""10/15 鸿波 Windows: Dream Engine「现在重蒸」llm_fail —— 插件写死 ~/.hermes/.env 找令牌,
Windows 上 hermes 在 %LOCALAPPDATA%\\hermes, 永远读不到。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catfish_memory_gateway as gw  # noqa: E402


def test_token_is_read_from_hermes_home_not_dot_hermes(tmp_path, monkeypatch):
    hermes = tmp_path / "AppData" / "Local" / "hermes"
    hermes.mkdir(parents=True)
    (hermes / ".env").write_text('OPENAI_API_KEY="eyJsvc"\n', encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # ~/.hermes 不存在
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    assert gw._gateway_dev_token() == "eyJsvc"


def test_windows_default_is_localappdata(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setitem(sys.modules, "hermes_constants", None)   # 子进程里拿不到
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert gw._hermes_home() == tmp_path / "hermes"


def test_vocab_is_found_next_to_the_installed_plugin():
    """安装包里插件旁边的 contracts/ 先找; 源码目录里回落 edge/contracts/ —— 两边都得读到。"""
    import catfish_memory_wiki as wiki
    import catfish_memory_wiki_provenance as prov

    assert wiki._ENTITY_TYPES, "类型词表没读到"
    assert prov._RELATIONS, "关系词表没读到"
