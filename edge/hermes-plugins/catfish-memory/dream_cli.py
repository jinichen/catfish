"""P3.5.1.3 (6/15 鸿波 Dream Engine): CLI 入口 — companion Tauri command spawn 这个.

设计 (方案 D, 6/15 鸿波拍):
  - 单文件 CLI, argparse --model
  - 内部 asyncio.run(catfish_memory.run_distill_for_dream_engine(model, progress_cb))
  - stdout 流式 JSON 行 (每行 1 个 event), companion Rust 端 line-by-line 解析
    转 Tauri event 'dream:progress' / 'dream:done' / 'dream:error'
  - stderr 用 logging, 给 debug

stdout event 协议 (companion src-tauri/commands/dream.rs 同步实现 parser):
  {"event":"start","total":<int>}                   - 拿到 total chunk 数
  {"event":"chunk","done":<int>,"total":<int>}      - 跑前/跑后 progress_cb
  {"event":"done","ok":true,"chunks":<int>,"bytes":<int>,"took_seconds":<float>}
  {"event":"done","ok":false,"reason":"<str>","took_seconds":<float>}
  {"event":"error","msg":"<str>"}                   - 未捕获异常 (理论不该出现)

跑法:
  python /path/to/catfish-memory/dream_cli.py --model catfish-public-deepseek-flash

P3.5.1.7 (6/15 鸿波撞 chunk 0/? 卡住): 老 `python -m catfish_memory.dream_cli` 不工作 —
catfish-memory/ 目录名带横线, 非法 Python module 名. -m 找不到 catfish_memory module.
真修: companion Rust 端 spawn 绝对路径 (dream_cli.py 全路径), 本文件 sys.path.insert(0, ...)
加自己 dirname → `from catfish_memory import ...` 拿同目录 catfish_memory.py 文件作 module.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sys
import traceback


# P3.5.1.7 (6/15 鸿波) v2: catfish_memory.py 内部用 `from .catfish_memory_helpers import ...`
# (relative import). hermes plugin loader 用 spec_from_file_location +
# submodule_search_locations trick 让 relative import 工作; 我们 dream_cli 不走 hermes
# 加载路径, 也得同款 trick.
#
# 错误模式: 之前 v1 只加 sys.path 然后 `from catfish_memory import ...` →
#   "attempted relative import with no known parent package"
#
# 修法: 注册 synthetic package "_catfish_memory_pkg" 含 __path__, 在它下面装载
# catfish_memory + catfish_memory_helpers 作 submodule. 这时 catfish_memory.py
# 里的 `from .catfish_memory_helpers import ...` 在 package 内找 sibling 工作.
def _load_catfish_memory_module():
    """importlib synthetic package trick — 跟 ~/.hermes/hermes-agent/plugins/memory/
    __init__.py:259-262 同款. 返 catfish_memory module (含 run_distill_for_dream_engine).
    """
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    pkg_name = "_catfish_memory_dream_pkg"

    # 1. 注册 synthetic parent package (含 __path__, 让 relative import 找 sibling)
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_loader(pkg_name, loader=None, is_package=True)
        pkg_mod = importlib.util.module_from_spec(pkg_spec)
        pkg_mod.__path__ = [plugin_dir]
        sys.modules[pkg_name] = pkg_mod

    # 2. 装载 catfish_memory_helpers (helpers 自给自足, 无 relative import)
    helpers_name = f"{pkg_name}.catfish_memory_helpers"
    if helpers_name not in sys.modules:
        helpers_path = os.path.join(plugin_dir, "catfish_memory_helpers.py")
        spec = importlib.util.spec_from_file_location(helpers_name, helpers_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"catfish_memory_helpers.py spec 创建失败: {helpers_path}")
        helpers_mod = importlib.util.module_from_spec(spec)
        sys.modules[helpers_name] = helpers_mod
        spec.loader.exec_module(helpers_mod)

    # 3. 装载 catfish_memory (内部 `from .catfish_memory_helpers import ...` 此刻能找到)
    mem_name = f"{pkg_name}.catfish_memory"
    if mem_name not in sys.modules:
        mem_path = os.path.join(plugin_dir, "catfish_memory.py")
        spec = importlib.util.spec_from_file_location(mem_name, mem_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"catfish_memory.py spec 创建失败: {mem_path}")
        mem_mod = importlib.util.module_from_spec(spec)
        sys.modules[mem_name] = mem_mod
        spec.loader.exec_module(mem_mod)

    return sys.modules[mem_name]


def _emit(event: dict) -> None:
    """单行 JSON + flush — Rust 端 BufReader.read_line 切."""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _build_progress_cb():
    """返一个 progress_cb(done, total). _call_distill_llm 每 chunk 前调一次 + 跑完调.

    我们用一个 flag 标 "已经 emit 过 start" — start 含 total, 第一次回调时 emit.
    """
    state = {"start_emitted": False}

    def cb(done: int, total: int) -> None:
        if not state["start_emitted"]:
            _emit({"event": "start", "total": int(total)})
            state["start_emitted"] = True
        _emit({"event": "chunk", "done": int(done), "total": int(total)})

    return cb


async def _main_async(model: str, force: bool) -> int:
    # 懒 import — 让 argparse 错也能被 _emit
    try:
        mem_mod = _load_catfish_memory_module()
        run_distill_for_dream_engine = mem_mod.run_distill_for_dream_engine
    except Exception as e:  # noqa: BLE001
        _emit({
            "event": "error",
            "msg": f"装载 catfish_memory 失败 (synthetic package trick): {e}\n{traceback.format_exc()}",
        })
        return 2

    cb = _build_progress_cb()
    try:
        result = await run_distill_for_dream_engine(
            model=model, force=force, progress_cb=cb,
        )
    except Exception as e:  # noqa: BLE001
        _emit({
            "event": "error",
            "msg": f"run_distill_for_dream_engine 抛错: {e}\n{traceback.format_exc()}",
        })
        return 1

    # 即使 ok=False (cooldown / empty_journal / llm_fail) 也走 done event
    # Rust / UI 端按 ok 字段区分.
    _emit({
        "event": "done",
        "ok": bool(result.get("ok")),
        "reason": result.get("reason", ""),
        "chunks": int(result.get("chunks_total", 0)),
        "bytes": int(result.get("bytes_written", 0)),
        "model": result.get("model", model),
        "took_seconds": float(result.get("took_seconds", 0.0)),
    })
    return 0 if result.get("ok") else 0  # 都返 0, UI 看 ok 字段


def main() -> int:
    parser = argparse.ArgumentParser(
        description="catfish-memory Dream Engine CLI — 员工主动触发蒸馏, 用 picker model.",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="LLM model name (companion picker 当前选的, 例如 catfish-public-deepseek-flash)",
    )
    parser.add_argument(
        "--no-force",
        action="store_true",
        help="不跳 24h cooldown (默认跳 — 员工主动触发就该立即跑)",
    )
    args = parser.parse_args()

    # logging → stderr, 不污染 stdout 协议
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        return asyncio.run(_main_async(args.model, force=not args.no_force))
    except KeyboardInterrupt:
        _emit({"event": "error", "msg": "interrupted"})
        return 130


if __name__ == "__main__":
    sys.exit(main())
