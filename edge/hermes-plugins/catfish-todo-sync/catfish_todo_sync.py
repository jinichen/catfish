"""catfish-todo-sync — hermes 内置 TodoStore 同步到 catfish-journal.

# 背景 (BL-CATFISH-TODO-SYNC, 5/20)

hermes 0.13+ 内置 `todo` tool (`tools/todo_tool.py:TodoStore`), 抢路由优先级
高于我们的 catfish-journal skill. 员工 chat 说"加TODO X" 时, LLM 直接调
内置 `todo` tool, 只存 in-memory (TodoStore._items), 不写文件. 重启 hermes 丢.

我们的早安 tab BriefingCard 工作计划行只读 `~/.catfish/employee_journal.md`,
所以 chat 里加的 TODO 看不到.

# 修法

monkey-patch `TodoStore.write` — plugin initialize 时把类方法替换成包装版:
  1. 调原 write (in-memory 不影响)
  2. 同步调 `catfish-journal add "<content>"` 真写 journal 文件
  3. 出错静默 (catfish-journal CLI 没装/挂掉, 不阻塞 hermes)

调用方式: plugin 在 catfish_memory 同套路 lifecycle, hermes 启动加载 plugin
时调 initialize 一次, 之后所有 TodoStore.write 走 patched 版本.

# 红线

- 只 patch write 不读 read - 不影响 LLM 后续读 TodoStore
- 只同步 pending 状态的 TODO - completed/cancelled 不写 journal (member 自己跑
  `catfish-journal done --line N --hint X` 标完成)
- subprocess 调 CLI 跑 timeout 3s - 卡了不阻塞 hermes 主流程
- 失败完全静默 (log.debug), hermes todo tool 继续工作

# 不做的事

- 不同步 TodoStore.read 出来的内容到 journal (反方向不要)
- 不拦截 todo tool 的 response 给 LLM (LLM 看到 todo 列表是原版)
- 不写 ~/.catfish/ 其它文件 (journal 唯一同步目标)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("catfish-todo-sync")

# 模块级状态: 记 patch 应用与否, 避免重复 patch
_PATCHED = False


def _find_catfish_journal_bin() -> str | None:
    """找 catfish-journal CLI. 优先 hermes venv, 兜底 PATH."""
    # 1. hermes venv 同套路 (catfish-email install.sh 装到这里)
    home = Path.home()
    venv_bin = home / ".hermes/hermes-agent/venv/bin/catfish-journal"
    if venv_bin.exists() and venv_bin.is_file():
        return str(venv_bin)
    # 2. PATH 软链兜底
    return shutil.which("catfish-journal")


def _find_todo_line_in_journal(bin_path: str, content: str) -> tuple[int, str] | None:
    """跑 catfish-journal list 找该 content 对应的 (line, hint).

    返 None 表示 journal 里没该 TODO (从未 add 过 或已 delete).
    用于 v0.1.8 done/delete 路径定位 journal 行号.
    """
    import json
    try:
        result = subprocess.run(
            [bin_path, "list", "--format=json"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return None
        todos = json.loads(result.stdout.decode("utf-8") or "[]")
        # 精确匹配 content (strip 后比对)
        target = content.strip()
        for t in todos:
            if t.get("text", "").strip() == target:
                return (int(t.get("line", 0)), target[:30])  # hint 用前 30 字
        return None
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
        logger.debug("_find_todo_line_in_journal failed: %s", e)
        return None


def _sync_to_journal_batch(bin_path: str, todos: List[Dict[str, Any]]) -> bool:
    """v0.1.10 (5/20) 批量 sync: 一次 catfish-journal sync --stdin 处理所有 todos.

    返 True = batch 成功, False = batch 不可用 (老 CLI 没 sync 子命令 / timeout / 错).
    False 时 caller fallback 单调路径.

    单次 subprocess 比 N 次单调省 N-1 次 Python 启动开销 (~50-200ms 每次).
    """
    import json as _json
    if not todos:
        return True
    # 构造 jsonl (一行一 op)
    ops = []
    for t in todos:
        content = str(t.get("content", "")).strip()
        status = str(t.get("status", "pending")).strip().lower()
        if not content:
            continue
        ops.append({"content": content, "status": status})
    if not ops:
        return True
    stdin_data = "\n".join(
        _json.dumps(op, ensure_ascii=False) for op in ops
    ).encode("utf-8")
    try:
        result = subprocess.run(
            [bin_path, "sync", "--stdin"],
            input=stdin_data,
            capture_output=True,
            timeout=5,  # batch 比单调久, 5s 给 N todo + journal IO
            check=False,
        )
        # 老 CLI (< v0.1.10) 没 sync 子命令 → exit 2 + stderr 含 "invalid choice"
        if result.returncode == 2 and b"invalid choice" in result.stderr:
            logger.debug("catfish-journal 老版本无 sync 子命令, fallback 单调")
            return False
        if result.returncode != 0:
            logger.debug(
                "catfish-journal sync exit=%d stderr=%s, fallback 单调",
                result.returncode,
                result.stderr[:200],
            )
            return False
        logger.debug("catfish-todo-sync batch ok: %d ops, stats=%s",
                     len(ops), result.stdout.decode("utf-8", errors="replace")[:200])
        return True
    except subprocess.TimeoutExpired:
        logger.debug("catfish-journal sync timeout, fallback 单调")
        return False
    except Exception as e:
        logger.debug("catfish-journal sync 异常 (%s), fallback 单调", e)
        return False


def _sync_to_journal(todos: List[Dict[str, Any]]) -> None:
    """同步 todos 到 ~/.catfish/employee_journal.md.

    v0.1.8 (5/20): 支持完整 status lifecycle:
    - pending / in_progress → catfish-journal add (幂等, 不重复加)
    - completed → catfish-journal done (- [ ] → - [x])
    - cancelled → catfish-journal delete (整行删)

    v0.1.9 (5/20): completed 但 journal 找不到时, 调 add --done 补 [x] 历史
    (LLM 直接标完成没经 add 的场景, 让 BriefingCard 反映干过的事不只未来 TODO).

    v0.1.10 (5/20): 默认走 catfish-journal sync --stdin batch 路径
    (1 次 subprocess 处理所有 op), fallback 老 CLI 走 N 次单调路径.

    每个调用都 timeout, 出错静默不阻塞 hermes 主流程.
    """
    bin_path = _find_catfish_journal_bin()
    if not bin_path:
        logger.debug("catfish-journal CLI not found, skip sync")
        return

    # v0.1.10: 优先 batch 路径
    if _sync_to_journal_batch(bin_path, todos):
        return

    # fallback 老路径: 单调 N 次 (catfish-journal < v0.1.10)
    for t in todos:
        try:
            content = str(t.get("content", "")).strip()
            status = str(t.get("status", "pending")).strip().lower()
            if not content:
                continue

            if status in ("pending", "in_progress"):
                # 新增 / 维持未完成: add (catfish-journal core v0.1.7 幂等, 重复不加)
                subprocess.run(
                    [bin_path, "add", content],
                    capture_output=True,
                    timeout=3,
                    check=False,
                )
                logger.debug("catfish-todo-sync wrote pending: %s", content[:60])

            elif status == "completed":
                # 标完成: 查 journal line + hint, 调 done
                located = _find_todo_line_in_journal(bin_path, content)
                if located:
                    line, hint = located
                    subprocess.run(
                        [bin_path, "done", "--line", str(line), "--hint", hint],
                        capture_output=True,
                        timeout=3,
                        check=False,
                    )
                    logger.debug("catfish-todo-sync marked done: %s (line=%d)", content[:60], line)
                else:
                    # v0.1.9 (5/20): journal 里没这条 TODO (LLM 直接标完成, 没经 add)
                    # → 补一条 `- [x] <content>` 进 journal 做历史记录, 让早安播报
                    # 跟 BriefingCard 能完整反映员工干过的事 (不是只显未来 TODO).
                    # catfish-journal add --done 走幂等检查, 已存在不重复.
                    subprocess.run(
                        [bin_path, "add", content, "--done"],
                        capture_output=True,
                        timeout=3,
                        check=False,
                    )
                    logger.debug(
                        "catfish-todo-sync: completed 直接标 (没 add 过), 补历史 [x]: %s",
                        content[:60],
                    )

            elif status == "cancelled":
                # 取消: 查 line + hint, 调 delete (整行删)
                located = _find_todo_line_in_journal(bin_path, content)
                if located:
                    line, hint = located
                    subprocess.run(
                        [bin_path, "delete", "--line", str(line), "--hint", hint],
                        capture_output=True,
                        timeout=3,
                        check=False,
                    )
                    logger.debug("catfish-todo-sync deleted: %s (line=%d)", content[:60], line)
                else:
                    logger.debug("catfish-todo-sync: cancelled TODO 在 journal 找不到")
            # 其他 status (未知值): 静默跳过
        except subprocess.TimeoutExpired:
            logger.debug("catfish-journal subprocess timeout, skip todo: %s", content[:30] if content else "?")
        except Exception as e:
            logger.debug("catfish-todo-sync error (ignored): %s", e)


def _apply_patch() -> bool:
    """Monkey-patch hermes TodoStore.write. 返 True = patched, False = 已 patched / 找不到."""
    global _PATCHED
    if _PATCHED:
        return False
    try:
        from tools.todo_tool import TodoStore  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "catfish-todo-sync: hermes tools.todo_tool 没找到, "
            "可能 hermes 版本不带内置 todo (0.13 之前), plugin 不需要"
        )
        return False

    original_write = TodoStore.write

    def patched_write(self, todos, merge=False):
        # 1. 原 write 先跑 (in-memory 必须先生效, LLM 后续读靠它)
        result = original_write(self, todos, merge=merge)
        # 2. 同步到 journal (失败不影响原 write)
        try:
            _sync_to_journal(todos)
        except Exception as e:
            # 双重防御, _sync_to_journal 自己已 try/except, 这里再兜一层
            logger.debug("catfish-todo-sync outer guard caught: %s", e)
        return result

    # 替换类方法 (类替换, 所有 TodoStore 实例共享 patched 版本)
    TodoStore.write = patched_write  # type: ignore[method-assign]
    _PATCHED = True
    logger.info(
        "catfish-todo-sync: monkey-patched TodoStore.write, "
        "LLM 调 todo tool 加 TODO 时自动同步到 ~/.catfish/employee_journal.md"
    )
    return True


# ── Dummy MemoryProvider for hermes plugin loader 协议 ─────────────
#
# hermes plugin 通过 `register(ctx)` 入口加载, 期望注册一个 MemoryProvider.
# 我们 plugin 的真核心是 monkey-patch TodoStore.write, 不是 memory inject.
# 但为了满足 hermes plugin loader 协议, 注册一个 dummy provider, is_available
# 返 False 不抢 active.


try:
    from agent.memory_provider import MemoryProvider  # type: ignore[import-not-found]
except ImportError:
    # 单测 / dev fallback
    from abc import ABC, abstractmethod

    class MemoryProvider(ABC):  # type: ignore[no-redef]
        @property
        @abstractmethod
        def name(self) -> str: ...

        @abstractmethod
        def is_available(self) -> bool: ...

        @abstractmethod
        def initialize(self, session_id: str, **kwargs: Any) -> None: ...

        def system_prompt_block(self) -> str:
            return ""

        def prefetch(self, query: str, *, session_id: str = "") -> str:
            return ""

        @abstractmethod
        def get_tool_schemas(self) -> List[Dict[str, Any]]: ...


class CatfishTodoSyncDummyProvider(MemoryProvider):
    """Dummy provider 让 hermes plugin loader 满意.

    is_available() 返 False — hermes MemoryManager 不会把这个 provider 当 active 用.
    真核心工作 (monkey-patch TodoStore.write) 在 __init__.py register(ctx) 时已经
    执行, 跟这个 dummy provider 实例无关.
    """

    @property
    def name(self) -> str:
        return "catfish-todo-sync"

    def is_available(self) -> bool:
        # 永远返 False, 不抢 hermes builtin memory 也不抢其他 active provider
        return False

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        # no-op, monkey-patch 在 plugin register() 时已应用
        pass

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        """no-op. monkey-patch 拦截 TodoStore.write 已写文件, 这里不需要重复.
        hermes plugin.yaml 列了 hooks: [sync_turn], hermes loader 据此识别 memory plugin
        调 register(ctx), 但每轮 sync_turn 真调用时这里不做事."""
        return None

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """no-op. plugin 不维护状态, session 结束没需要 flush."""
        return None
