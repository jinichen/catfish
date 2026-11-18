"""「我学过的要点」的接口 (11/18 第 3 步, learning_graph.py)。

    learn/graph_view    {}                     → {sources: [{source, label, kind, points: [...]}], points, chains, running, last}
    learn/graph_import  {stage_id, name}       导入一份资料 (Word / PDF / 文本), 后台提炼; 立即返回
    learn/graph_delete  {source}               删一份材料的要点 (别的材料也提到的留着)

导入是长活 (一份资料分几段, 每段提炼 + 认同各一次模型调用), 后台跑; 同时只跑一份, 状态在 _STATE (界面轮询 graph_view)。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import learning_graph as G

logger = logging.getLogger("catfish.tool_bridge.learning_graph")

_STATE: dict[str, Any] = {"running": None, "last": None}
_TASKS: set[asyncio.Task] = set()
MAX_TEXT = 200_000
ACCEPT = (".docx", ".pdf", ".txt", ".md", ".pptx", ".xlsx")


async def handle(method: str, params: dict, home: str, llm_kw: dict) -> dict[str, Any]:
    if method == "learn/graph_view":
        return {**await asyncio.to_thread(G.view, home), "running": _STATE["running"], "last": _STATE["last"]}
    if method == "learn/graph_delete":
        source = str(params.get("source") or "")
        if not source:
            raise ValueError("删哪份材料?")
        if source.startswith("learn:"):
            raise ValueError("学习沉淀的要点跟着那次学习走: 删那次学习就会摘掉 (别的材料也提到的留着)")
        r = await asyncio.to_thread(G.delete_source, home, source)
        return {**await asyncio.to_thread(G.view, home), "running": _STATE["running"], "last": {"deleted": r}}
    if method == "learn/graph_import":
        if not llm_kw.get("auth_token"):
            raise RuntimeError("没登录 (拿不到访问令牌), 登录后再导入")
        if _STATE["running"]:
            raise ValueError(f"小鲶还在读「{_STATE['running']}」, 读完再导入下一份")
        from . import doc_text  # noqa: PLC0415
        from .colleague_rooms_bigfiles import staged  # noqa: PLC0415
        name = Path(str(params.get("name") or "资料")).name.strip()[:120]
        suffix = Path(name).suffix.lower()
        if suffix not in ACCEPT:
            raise ValueError(f"只收 {' / '.join(ACCEPT)} —— 老 .doc 请先另存为 .docx")
        src = staged(str(params.get("stage_id") or ""))
        # doc_text 按扩展名认格式, 暂存文件没有扩展名: 复制成带扩展名的临时文件再抽
        with tempfile.TemporaryDirectory(prefix="catfish-learn-import-") as td:
            tmp = Path(td) / f"incoming{suffix}"
            shutil.copyfile(src, tmp)
            text, why = await asyncio.to_thread(doc_text.extract_text, tmp)
        if not text.strip():
            raise ValueError(f"「{name}」读不出文字: {why or '空的'}")
        text = text[:MAX_TEXT]
        source = G.file_source(name, text)
        _STATE.update(running=name, last=None)

        async def go() -> None:
            try:
                r = await G.ingest(home, source, f"资料: {name}", "file", text, llm_kw)
                _STATE["last"] = {"name": name, **r}
            except Exception as e:  # noqa: BLE001
                logger.warning("learning_graph: 导入「%s」失败: %s", name, e)
                _STATE["last"] = {"name": name, "error": str(e)[:300]}
            finally:
                _STATE["running"] = None
        t = asyncio.get_running_loop().create_task(go())
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
        return {**await asyncio.to_thread(G.view, home), "running": _STATE["running"], "last": None,
                "chars": len(text), "parts": len(G.chunks(text))}
    raise ValueError(f"不认识的操作: {method}")
