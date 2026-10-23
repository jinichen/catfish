"""10/23: 长期记忆向量检索 (catfish_memory_vectors) + 跟词面检索的合并 (catfish_memory_recall)。

不调网关: embed 换成按「意思标签」出向量的假函数 —— 同一个标签的文本向量相同, 词面却可以完全不同。
"""
from __future__ import annotations

from array import array
from pathlib import Path

import pytest

import catfish_memory_recall as r
import catfish_memory_vectors as v
from catfish_memory_distill_reconcile import assemble

#: 文本里含这些词 → 落到对应维度 (模拟「意思相近 → 向量相近」)
_MEANING = {"草稿": 0, "起草": 0, "Mail.app": 0, "邮件": 0, "证书": 1, "CMMI": 1, "周报": 2}


def _fake_vec(text: str) -> array:
    vec = [0.0] * 4
    for word, dim in _MEANING.items():
        if word in text:
            vec[dim] += 1.0
    if not any(vec):
        vec[3] = 1.0
    return v._normalize(vec)


@pytest.fixture
def fake_gateway(monkeypatch):
    calls = {"batches": [], "model": "bge_m3", "fail": False}

    def embed(texts, timeout):
        if calls["fail"]:
            return None
        calls["batches"].append(list(texts))
        return calls["model"], [_fake_vec(t) for t in texts]

    v._reset_for_tests()
    r._cache.clear()
    monkeypatch.setattr(v, "embed", embed)
    monkeypatch.setenv("CATFISH_MEMORY_VECTORS", "1")
    # 后台线程改成同步, 测试里好断言
    monkeypatch.setattr(v, "build_in_background", lambda db, texts, model_hint="": v.build(db, texts, model_hint))
    yield calls
    v._reset_for_tests()
    r._cache.clear()


SEG = (
    "## 偏好\n- 草稿写好后由员工在 Mail.app 核对发送\n- 周报沿用上期格式\n\n"
    "## 项目\n- CMMI-5：证书 83824，有效至 2029-08-10\n"
)


def _write(tmp_path: Path) -> tuple[Path, str]:
    text = assemble([("2026-10-19 ~ 2026-10-21", SEG)], "## 进行中\n- 无")
    p = tmp_path / "distilled_facts.md"
    p.write_text(text, encoding="utf-8")
    return p, text


def test_同义改写靠向量找到(tmp_path, fake_gateway):
    p, text = _write(tmp_path)
    # 「起草的邮件怎么发」跟「草稿…Mail.app 核对发送」词面几乎不重合
    out = r.recall_block(p, text, "你起草的东西我一般怎么处理", 1800)
    assert "Mail.app 核对发送" in out


def test_向量用不了就只用词面_跟以前一样(tmp_path, fake_gateway, monkeypatch):
    p, text = _write(tmp_path)
    monkeypatch.setenv("CATFISH_MEMORY_VECTORS", "0")
    assert r.recall_block(p, text, "你起草的东西我一般怎么处理", 1800) == ""
    assert "83824" in r.recall_block(p, text, "CMMI-5 证书编号", 1800)


def test_网关不通_词面照常(tmp_path, fake_gateway):
    p, text = _write(tmp_path)
    fake_gateway["fail"] = True
    assert "83824" in r.recall_block(p, text, "CMMI-5 证书编号", 1800)


def test_向量按文本复用_新增的才算(tmp_path, fake_gateway):
    p, text = _write(tmp_path)
    r.recall_block(p, text, "周报", 1800)
    first = sum(len(b) for b in fake_gateway["batches"] if len(b) > 1)
    assert first == 3, "三条记忆各算一次"
    # 进程重启 (内存清空): 从库里读, 不重算
    v._reset_for_tests()
    r._cache.clear()
    fake_gateway["batches"].clear()
    r.recall_block(p, text, "周报", 1800)
    assert all(len(b) == 1 for b in fake_gateway["batches"]), "只有问题本身要算"
    # 蒸馏新增一条 → 只算这一条
    text2 = assemble([("2026-10-19 ~ 2026-10-21", SEG + "- 新证书 ISO50001 待补数据\n")], "## 进行中\n- 无")
    p.write_text(text2, encoding="utf-8")
    fake_gateway["batches"].clear()
    r.recall_block(p, text2, "周报", 1800)
    built = [b for b in fake_gateway["batches"] if "周报" not in b]
    assert built == [["项目 新证书 ISO50001 待补数据"]]


def test_中央换了向量模型_旧向量作废重建(tmp_path, fake_gateway):
    p, text = _write(tmp_path)
    r.recall_block(p, text, "周报", 1800)
    fake_gateway["model"] = "new_model"
    fake_gateway["batches"].clear()
    # 这一轮问题向量是新模型的 → 跟库里旧向量不能比: 只用词面, 同时后台重建
    out = r.recall_block(p, text, "CMMI-5 证书编号", 1800)
    assert "83824" in out
    rebuilt = sorted(t for b in fake_gateway["batches"] if len(b) > 1 for t in b)
    assert len(rebuilt) == 3
    model, vecs = v._load(r._vector_db(p))
    assert model == "new_model" and len(vecs) == 3


def test_失败后冷却_不每轮都等(monkeypatch):
    v._reset_for_tests()
    tried = []

    class Boom:
        @staticmethod
        def post(*a, **k):
            tried.append(1)
            raise OSError("连不上")

    monkeypatch.setitem(__import__("sys").modules, "httpx", Boom)
    monkeypatch.setattr(v, "_gateway_dev_token", lambda: "t")
    assert v.embed(["a"], 1.0) is None
    assert v.embed(["b"], 1.0) is None
    assert len(tried) == 1, "冷却期内不再发请求"
    v._reset_for_tests()


def test_优先借Companion的本机模型_连不上也不改走网关(tmp_path, monkeypatch):
    import socket
    import threading
    import json as _json
    v._reset_for_tests()
    monkeypatch.setenv("CATFISH_HOME", str(tmp_path))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "bge-m3.onnx").write_bytes(b"x")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    (tmp_path / "companion-embed.port").write_text(str(port))

    def serve():
        conn, _ = srv.accept()
        req = _json.loads(conn.makefile().readline())
        conn.sendall((_json.dumps({"model": "local:bge-m3", "dim": 2, "vectors": [[3.0, 4.0] for _ in req["texts"]]}) + "\n").encode())
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    gateway_called = []
    monkeypatch.setattr(v, "_gateway_dev_token", lambda: gateway_called.append(1) or "t")
    model, vecs = v.embed(["一", "二"], 2.0)
    assert model == "local:bge-m3" and len(vecs) == 2 and abs(vecs[0][0] - 0.6) < 1e-6
    srv.close()
    # Companion 关了: 有本机模型 → 不走网关, 这一轮没向量
    assert v.embed(["三"], 1.0) is None
    assert gateway_called == []
    v._reset_for_tests()
