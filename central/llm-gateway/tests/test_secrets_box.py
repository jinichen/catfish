"""供应商 API key 加密存储 (8/1, DESIGN-PROVIDER-SPLIT §5).

盯的几个事故形态:
  · 主密钥没配 → **整份配置挂掉 / 网关起不来** (7/30 那条 ${VAR} 的同款)
  · 主密钥没配 → 界面上"保存成功"但其实什么都没存
  · key 从某一次序列化里漏出去
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from catfish_gateway import secrets_box as SB
from catfish_gateway.config import ModelConfig


@pytest.fixture(autouse=True)
def _clean():
    SB.reset_cache()
    yield
    SB.reset_cache()


@pytest.fixture()
def master(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setenv(SB.MASTER_KEY_ENV, k)
    SB.reset_cache()
    return k


# ── 基本往返 ────────────────────────────────────────────────────────


def test_加解密往返(master):
    assert SB.decrypt(SB.encrypt("sk-绝密-123")) == "sk-绝密-123"


def test_密文里不含明文(master):
    """Fernet 是加密不是编码 —— 但值得钉一条, 万一哪天有人换成 base64。"""
    enc = SB.encrypt("sk-绝密-123")
    assert b"sk-" not in enc
    assert "绝密".encode() not in enc


def test_同一个明文两次加密结果不同(master):
    """Fernet 带随机 IV。相同就意味着能从密文比对出"这两家用的是同一个 key"。"""
    assert SB.encrypt("same") != SB.encrypt("same")


def test_改一个字节就解不开(master):
    """Fernet 自带认证。没有认证的话, 改坏的密文会解出乱码当 key 用。"""
    enc = bytearray(SB.encrypt("sk-x"))
    enc[-1] ^= 0x01
    assert SB.decrypt(bytes(enc)) is None


# ── 主密钥没配 / 配错: 读降级、写 fail loud ──────────────────────────


def test_主密钥没配时解密返_None_而不抛(monkeypatch):
    """冷启动时 lifespan 第一行就是 get_config() —— 抛出去 = 全站 502。"""
    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()
    assert SB.is_configured() is False
    assert SB.decrypt(b"whatever") is None


def test_主密钥没配时加密要抛(monkeypatch):
    """写路径必须 fail loud —— 静默失败会变成"界面显示保存成功但员工调用一直失败"。"""
    monkeypatch.delenv(SB.MASTER_KEY_ENV, raising=False)
    SB.reset_cache()
    with pytest.raises(RuntimeError) as e:
        SB.encrypt("sk-x")
    d = str(e.value)
    assert SB.MASTER_KEY_ENV in d
    assert "密码管理器" in d, "必须提醒备份 —— 丢了所有 key 都解不开"


def test_主密钥配错格式_当没配处理但要能区分(monkeypatch, caplog):
    """"没配"是还没做这一步, "配错"是做了但做错了 —— 后者必须响。"""
    monkeypatch.setenv(SB.MASTER_KEY_ENV, "这不是合法的-fernet-key")
    SB.reset_cache()
    with caplog.at_level("ERROR"):
        assert SB.is_configured() is False
    assert any("合法" in r.message or "Fernet" in r.message for r in caplog.records)


def test_换过主密钥的老密文解不开但不抛(monkeypatch):
    old = Fernet.generate_key().decode()
    monkeypatch.setenv(SB.MASTER_KEY_ENV, old)
    SB.reset_cache()
    enc = SB.encrypt("sk-x")

    monkeypatch.setenv(SB.MASTER_KEY_ENV, Fernet.generate_key().decode())
    SB.reset_cache()
    assert SB.decrypt(enc) is None


# ── key 不能从序列化里漏出去 ────────────────────────────────────────


def test_解密后的_key_不进任何一次序列化(master):
    """**结构性保证**, 不靠"每次记得 exclude"。

    admin_models_router 有一条降级路径会 dump cfg.models, /v1/models 和
    /v1/catalog 也各自序列化模型信息 —— 靠纪律逐个 exclude 迟早漏一处。
    用 PrivateAttr 之后, 漏不出去这件事由 pydantic 保证。
    """
    m = ModelConfig.model_validate(
        {"name": "m", "display_name": "m", "upstream": {"model": "openai/x"}}
    )
    m.upstream._secret = "sk-绝密-不该出现"

    assert m.upstream.api_key == "sk-绝密-不该出现", "自己能用"
    for blob in (
        str(m.model_dump()),
        m.model_dump_json(),
        str(m.upstream.model_dump()),
        m.upstream.model_dump_json(),
    ):
        assert "sk-绝密-不该出现" not in blob
        assert "_secret" not in blob


def test_model_validate_塞不进_secret():
    """外部输入 (界面 PUT 的 body) 不该能设置这个字段。"""
    m = ModelConfig.model_validate(
        {
            "name": "m",
            "display_name": "m",
            "upstream": {"model": "openai/x", "_secret": "注入试试"},
        }
    )
    assert m.upstream._secret is None


# ── 两条来源并存 (迁移期) ───────────────────────────────────────────


def test_存库的_key_优先于环境变量(monkeypatch):
    monkeypatch.setenv("SOME_ENV_KEY", "来自环境变量")
    m = ModelConfig.model_validate(
        {
            "name": "m",
            "display_name": "m",
            "upstream": {"model": "openai/x", "api_key_env": "SOME_ENV_KEY"},
        }
    )
    assert m.upstream.api_key == "来自环境变量"
    m.upstream._secret = "来自库"
    assert m.upstream.api_key == "来自库"


def test_两条都没有时_is_available_为假并给出可执行的报错(monkeypatch):
    monkeypatch.delenv("NO_SUCH_ENV", raising=False)
    m = ModelConfig.model_validate(
        {
            "name": "m",
            "display_name": "m",
            "upstream": {"model": "openai/x", "api_key_env": "NO_SUCH_ENV"},
        }
    )
    assert m.upstream.is_available is False
    with pytest.raises(RuntimeError) as e:
        _ = m.upstream.api_key
    assert "NO_SUCH_ENV" in str(e.value), "报错要点名是哪个变量, 否则没法查"
