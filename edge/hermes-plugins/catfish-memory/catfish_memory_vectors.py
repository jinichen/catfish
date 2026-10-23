"""长期记忆的向量检索 (10/23 P1.5): 按意思找, 不只按字面。

# 为什么

catfish_memory_recall 的 BM25 只认字面: 员工问「你帮我起草的邮件我是怎么发的」, 记忆里写的是
「草稿…Mail.app核对发送」, 一个词都对不上。10/23 评测 51 题, 词面检索漏的 5 题全是这种。
向量 (BGE-M3) 管得了同义改写; 两路合起来 50/51。

# 做法

  · 向量由网关 /v1/embeddings 算 (不带 model = 中央配的默认向量模型, 跟 Companion 远程模式
    同一个)。Intel Mac / Windows 没有本机向量运行时, 走网关处处能用。记忆本来就经网关蒸馏,
    没有多发到新的地方。
  · 每条记忆的向量按文本 sha1 存在 ~/.catfish/memory_vectors.db。第一次全量 ~30 秒 (3300 条),
    后台线程跑; 之后蒸馏每天只新增几十条, 也只算这几十条。
  · 每轮对话: 问题算一次向量 (~0.1 秒, 超时 2 秒), 跟内存里的向量逐条点积 (纯 Python,
    3300 条 0.05 秒 —— hermes 环境没有 numpy)。
  · 任何一步不行 (网关不通 / 没配向量模型 / 索引还没建好) → 返 None, recall 照旧只用词面。
    网关失败后 5 分钟内不再试, 不给每轮对话加 2 秒。
  · 中央换了向量模型 (返回的 model 名变了) → 旧向量全部作废, 重建。不同模型的向量不能比。
"""
from __future__ import annotations

import hashlib
import logging
import math
import operator
import sqlite3
import threading
import time
from array import array
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    from .catfish_memory_gateway import _gateway_dev_token, _gateway_url, with_source
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_gateway import _gateway_dev_token, _gateway_url, with_source

logger = logging.getLogger("catfish-memory.vectors")

_BATCH = 64
_BUILD_TIMEOUT = 60.0
_QUERY_TIMEOUT = 2.0
#: 网关失败后多久不再试 (秒)
_COOLDOWN = 300.0

_lock = threading.Lock()
_building = False
_down_until = 0.0
#: db 路径 → (模型名, {sha1: 向量})
_loaded: Dict[str, Tuple[str, Dict[str, array]]] = {}


def text_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _endpoint() -> str:
    base = _gateway_url().split("/v1/")[0]
    return with_source(f"{base}/v1/embeddings", "plugin:memory-recall-embed")


def _normalize(v: Sequence[float]) -> array:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array("f", (x / n for x in v))


def embed(texts: List[str], timeout: float) -> Optional[Tuple[str, List[array]]]:
    """网关算向量 → (模型名, 归一化向量)。失败返 None 并进入冷却。"""
    global _down_until
    if time.time() < _down_until:
        return None
    token = _gateway_dev_token()
    if not token:
        return None
    try:
        import httpx  # 懒 import, 跟 catfish_memory_llm 一样

        resp = httpx.post(
            _endpoint(),
            headers={"Authorization": f"Bearer {token}", "X-Catfish-Skip-Identity": "1"},
            json={"input": texts},
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
        data = resp.json()
        rows = sorted(data["data"], key=lambda d: d.get("index", 0))
        if len(rows) != len(texts):
            raise RuntimeError(f"返回 {len(rows)} 条, 要的是 {len(texts)} 条")
        return str(data.get("model") or ""), [_normalize(r["embedding"]) for r in rows]
    except Exception as e:  # noqa: BLE001
        _down_until = time.time() + _COOLDOWN
        logger.info("记忆向量: 网关算不了向量, %d 秒内只用词面检索 (%s)", int(_COOLDOWN), e)
        return None


def _open(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db), timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS vec (key TEXT PRIMARY KEY, model TEXT NOT NULL, vector BLOB NOT NULL)")
    return conn


def _load(db: Path) -> Tuple[str, Dict[str, array]]:
    """库里的向量读进内存 (只留同一个模型的那批; 混了说明换过模型, 取最多的那个)。"""
    hit = _loaded.get(str(db))
    if hit is not None:
        return hit
    model, vecs = "", {}
    if db.exists():
        conn = _open(db)
        try:
            row = conn.execute("SELECT model FROM vec GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
            if row:
                model = row[0]
                for key, blob in conn.execute("SELECT key, vector FROM vec WHERE model = ?", (model,)):
                    v = array("f")
                    v.frombytes(blob)
                    vecs[key] = v
        finally:
            conn.close()
    _loaded[str(db)] = (model, vecs)
    return model, vecs


def _store(db: Path, model: str, pairs: List[Tuple[str, array]], reset: bool) -> None:
    conn = _open(db)
    try:
        with conn:
            if reset:
                conn.execute("DELETE FROM vec")
            conn.executemany(
                "INSERT OR REPLACE INTO vec (key, model, vector) VALUES (?, ?, ?)",
                [(k, model, v.tobytes()) for k, v in pairs],
            )
    finally:
        conn.close()


def build(db: Path, texts: List[str], model_hint: str = "") -> int:
    """给还没有向量的记忆补上 (同步; 调用方放后台线程)。返回新算的条数。

    model_hint: 已知网关现在用的模型 (问题向量带回来的)。跟库里的不一样 → 全部重算。
    """
    global _building
    with _lock:
        if _building:
            return 0
        _building = True
    try:
        model, vecs = _load(db)
        uniq = list(dict.fromkeys(texts))
        stale = bool(model_hint) and bool(model) and model_hint != model
        pending = uniq if stale else [t for t in uniq if text_key(t) not in vecs]
        done = 0
        while pending:
            chunk, pending = pending[:_BATCH], pending[_BATCH:]
            got = embed(chunk, _BUILD_TIMEOUT)
            if got is None:
                break
            new_model, new_vecs = got
            reset = bool(model) and new_model != model
            if reset:
                logger.info("记忆向量: 向量模型从 %s 换成了 %s, 旧向量作废重建", model, new_model)
                vecs = {}
                in_chunk = set(chunk)
                pending = [t for t in uniq if t not in in_chunk]
            pairs = [(text_key(t), v) for t, v in zip(chunk, new_vecs)]
            _store(db, new_model, pairs, reset)
            model = new_model
            vecs.update(pairs)
            _loaded[str(db)] = (model, vecs)
            done += len(chunk)
        if done:
            logger.info("记忆向量: 新算 %d 条, 共 %d 条", done, len(vecs))
        return done
    finally:
        with _lock:
            _building = False


def build_in_background(db: Path, texts: List[str], model_hint: str = "") -> None:
    if _building:
        return
    threading.Thread(target=build, args=(db, texts, model_hint), name="catfish-recall-vectors", daemon=True).start()


def search(db: Path, texts: List[str], query: str, top_n: int) -> Optional[List[Tuple[float, int]]]:
    """按意思排: [(相似度, texts 下标)], 最相近在前。用不了向量时返 None。

    只排已经有向量的那些; 没有的 (刚蒸馏出来、后台还没算到) 这轮只靠词面。
    """
    model, vecs = _load(db)
    if not vecs:
        return None
    got = embed([query], _QUERY_TIMEOUT)
    if got is None:
        return None
    q_model, (qv,) = got
    if q_model != model:
        build_in_background(db, texts, q_model)  # 中央刚换模型: 后台重建, 这轮只用词面
        return None
    mul = operator.mul
    scored = []
    for i, t in enumerate(texts):
        v = vecs.get(text_key(t))
        if v is not None:
            scored.append((sum(map(mul, qv, v)), i))
    scored.sort(key=lambda x: -x[0])
    return scored[:top_n]


def _reset_for_tests() -> None:
    global _building, _down_until
    _loaded.clear()
    _building = False
    _down_until = 0.0
