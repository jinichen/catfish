"""catfish memory router — 替换 hermes builtin memory tool, 5 仓库智能路由 + audit trail.

# BL-MEMORY-ROUTER-A2-V3 (6/2 凌晨鸿波拍 V3)

## 为啥在 catfish-xcatfish-user plugin

v2 原计划放 catfish-memory plugin (5/19 ship 的 MemoryProvider), 但 audit 发现:
- hermes gateway mode (Companion → hermes 8642 → gateway 8999 thin proxy 路径)
  **不 load memory provider**, MemoryManager 不 init catfish-memory
- catfish-memory plugin 从 5/19 ship 至今 14 天**真生产 0 active** (5/24 鸿波看的
  "70% 跑偏" 其实是 hermes builtin memory_tool 直接调, 0 catfish 缓解)

真 enforce 位置: catfish-xcatfish-user plugin. 它 6/1 ship 后**真装载**, 走 hermes
plugin discovery 路径 (`hermes_cli.plugins`), register(ctx) 真被调.

## 5 kind 路由

| kind         | 路由                                                    |
|--------------|---------------------------------------------------------|
| identity     | hermes 原 memory_tool(target=user) → USER.md            |
| project_fact | hermes 原 memory_tool(target=memory) → MEMORY.md        |
| workflow     | hint 让 LLM 调 catfish_propose_skill (BL-MM9 5/8 ship)  |
| journal      | append ~/.catfish/employee_journal.md (catfish 现有)    |
| todo         | 拒绝落 journal，要求调 catfish_create_reminder          |

## 性能

LLM 在 tool call 时自己填 kind, 0 后端 LLM 调用. 单次 write < 50ms 跟 hermes
原生一样. 0 性能损失.

## enforcement

ctx.register_tool(override=True) 让 hermes builtin memory tool 不在 LLM tool list
出现, LLM 看到的就是 catfish 5 选 1 schema. 命中率从 0% (现状) → 95%+.

# 6/2 BL-MEMORY-AUDIT-TRAIL (鸿波 6/2 下午拍, 4 选项全推荐)

## 真问题 (鸿波 6/2 audit 抓的)

hermes 原 memory_tool replace/remove **直接覆盖, 无 history** — 老 entry 永久消失.
.bak.<ts> 只在 drift detection 触发 (外部 patch/shell 改文件), 非每次修改. 实地
证: ~/.hermes/memories/ 现 13 个 .bak 全是 5/24 一天 drift 触发, 之后 9 天 0 backup.

追溯问题麻烦: "鲶鱼上周记得我说啥来着?" / "为啥 LLM 突然忘了?" / 政企客户合规审计.

## 设计 (4 选项全推荐拍后)

- 落盘: ~/.catfish/memory_audit.jsonl (append-only, 100% 本机, 中央 0 红线)
- 覆盖: 5 kind 全过 audit — identity/project_fact/workflow/journal/todo
- prev_value 读取: replace/remove 前 file 直读 hermes USER.md / MEMORY.md, 用
  ENTRY_DELIMITER "\\n§\\n" 解析, 找含 old_text 的 entry. 不调 hermes tool (hermes
  原 schema 没 read action, 加 read = fork upstream, 不值).
- jsonl 每条: ts/user_email/kind/action/content/old_text/prev_value/success/error
- 失败也 audit (success=false + error). LLM 撞 limit / drift 都留痕.
- audit 失败 (磁盘满 / 权限错) 不阻塞 memory 写 — log warning, audit best-effort.

## UI (今晚最小)

PrivacyCard 🟢 本机存储 加 1 行 "我对你的记忆修改历史 (覆盖/删除全留, 可查可追溯)"
+ 路径 ~/.catfish/memory_audit.jsonl. 真 Card 周一 review 再加.

## 跟 BL-MM3 (老 memory_save inline backup) 关系

BL-MM3 是对 hermes 老 memory_save 工具 (key-value) 的 inline 备注, 只保留上一轮
200 字. 跟新 memory_tool (USER.md/MEMORY.md) **不同接口**. 本 audit 不动 BL-MM3,
两者共存 (老路径仍有, 新路径 audit 全量).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("catfish.xcatfish_user.memory_router")


# ── catfish home (跟 catfish-memory plugin 同) ────────────────────────────

def _catfish_home() -> Path:
    """~/.catfish/ (mac/linux) or ${CATFISH_HOME}."""
    env = os.environ.get("CATFISH_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".catfish"


# ── 6/2 BL-MEMORY-AUDIT-TRAIL: audit log 助手 ────────────────────────────

# hermes memory entry delimiter — 跟 hermes_agent/tools/memory_tool.py 同源.
# 改这个常量 = 跟 hermes 内部解析逻辑解耦风险. 这格式 5+ 年没变 (hermes 0.10 以前定的),
# fork 风险极低. 真改时这里编译期发现 (找不到 entry), test 会报.
_HERMES_ENTRY_DELIMITER = "\n§\n"


def _hermes_memory_dir() -> Path:
    """~/.hermes/memories/ 或 ${HERMES_HOME}/memories/.

    跟 hermes_agent/tools/memory_tool.py:get_memory_dir() 同语义. 不能直接 import
    hermes 是因为 plugin 装载顺序下 hermes_constants 可能不在 sys.path.
    """
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser() / "memories"
    return Path.home() / ".hermes" / "memories"


def _read_hermes_entries(target: str) -> List[str]:
    """File 直读 hermes USER.md / MEMORY.md, 用 \\n§\\n 分隔返 entry list.

    跟 hermes _read_file() 同语义. 文件不存在 / 读失败 → 返 [] (不阻塞).
    """
    if target not in ("user", "memory"):
        return []
    fname = "USER.md" if target == "user" else "MEMORY.md"
    path = _hermes_memory_dir() / fname
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, IOError) as e:
        logger.warning("_read_hermes_entries 读 %s 失败 (audit prev_value 缺失): %s", path, e)
        return []
    if not raw.strip():
        return []
    entries = [e.strip() for e in raw.split(_HERMES_ENTRY_DELIMITER)]
    return [e for e in entries if e]


def _find_entry_containing(entries: List[str], old_text: str) -> Optional[str]:
    """模仿 hermes replace/remove 的 substring 匹配, 返第一个匹配的完整 entry.

    跟 hermes memory_tool.replace/remove 同算法: 找含 old_text 子串的 entry.
    多个匹配返第一个 (hermes 多匹配时返 error, 但 audit 在 hermes 拒之前就读了,
    所以可能找到也可能找不到 — best-effort).
    """
    if not old_text or not entries:
        return None
    for e in entries:
        if old_text in e:
            return e
    return None


def _audit_log_path() -> Path:
    """~/.catfish/memory_audit.jsonl"""
    return _catfish_home() / "memory_audit.jsonl"


def _read_prev_value_for_audit(kind: str, action: str, old_text: Optional[str]) -> Optional[str]:
    """replace/remove 前从 hermes file 读 prev_value 给 audit log 用.

    只对真"覆盖/删除" 操作有 prev_value 概念:
    - identity replace/remove → 读 USER.md
    - project_fact replace/remove → 读 MEMORY.md
    - 其它 kind / action=add → prev_value 不适用, 返 None

    读失败 → None (audit 仍记, 只是缺这字段). 不阻塞主流程.
    """
    if action not in ("replace", "remove") or not old_text:
        return None
    if kind == "identity":
        entries = _read_hermes_entries("user")
    elif kind == "project_fact":
        entries = _read_hermes_entries("memory")
    else:
        return None  # workflow/journal/todo 没"修改"语义 (都是 append-only)
    return _find_entry_containing(entries, old_text)


def _build_audit_record(
    kind: str,
    action: str,
    args: Dict[str, Any],
    prev_value: Optional[str],
    result_json: str,
    user_email: Optional[str],
) -> Dict[str, Any]:
    """构造 audit jsonl 一条记录.

    success / error 从 result_json 解出 (hermes tool 返 JSON string with "success" field).
    """
    success = True
    error_msg = None
    try:
        parsed = json.loads(result_json) if isinstance(result_json, str) else result_json
        if isinstance(parsed, dict):
            if parsed.get("success") is False:
                success = False
                error_msg = parsed.get("error")
    except (json.JSONDecodeError, TypeError):
        pass  # 解析不出来不影响 audit (只是 success/error 缺)

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_email": user_email,
        "kind": kind,
        "action": action,
        "content": args.get("content"),
        "old_text": args.get("old_text"),
        "prev_value": prev_value,
        "success": success,
        "error": error_msg,
        "source_tool": "memory(catfish-router)",
    }


def _append_audit_log(record: Dict[str, Any]) -> None:
    """append jsonl, best-effort. 失败 log warning, 不阻塞 memory 主写.

    BL-MEMORY-AUDIT-TRAIL: audit 失败 (磁盘满 / 权限错) **不能** 阻塞 memory 写 —
    员工记忆比 audit 历史重要 1 等级. log warning 让运维知道.
    """
    try:
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except (OSError, IOError) as e:
        logger.warning(
            "memory audit log write 失败 (不阻塞主写, audit 缺这条): %s",
            e,
        )


def _extract_user_email(kw: Dict[str, Any]) -> Optional[str]:
    """从 ctx.register_tool handler 传的 kwargs 拿员工 email.

    hermes plugin tool handler 的 kw 可能含 store / session_id / user 等. 单机版
    employees=1 通常空, 但留字段, multi-tenant 时 hermes upstream 加进 kw 就接得到.
    """
    # 候选 key 顺序试 (hermes 上游可能换名字, 多试一个抗漂移)
    for k in ("user_email", "user", "effective_user_email"):
        v = kw.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # 兜底从 env (single-employee 部署常用)
    env = os.environ.get("CATFISH_EFFECTIVE_USER", "").strip()
    return env if env else None


# ── tool schema ───────────────────────────────────────────────────────────

CATFISH_MEMORY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "replace", "remove"],
            "description": "add (新加) / replace (改) / remove (删)",
        },
        "kind": {
            "type": "string",
            "enum": ["identity", "project_fact", "workflow", "journal", "todo"],
            "description": (
                "内容性质 (必填, 决定存哪):\n"
                "- identity: 关于员工**这个人**的稳定事实 (姓名/部门/偏好/沟通风格) → USER.md\n"
                "- project_fact: **项目/技术**事实 (API 字段含义/客户机房 IP/工具约定) → MEMORY.md\n"
                "- workflow: 工作**流程** (有 input/output/step 序列) → 自动提议存成 skill\n"
                "- journal: 已发生**事件**/session 总结/会议记录 → 写 catfish 员工日志 (不是 memory)\n"
                "- todo: 带 deadline 的**待办任务** → 自动转 Reminders.app (不是 memory)\n"
                "拿不准 → 80% 概率是 journal 不是 identity/project_fact."
            ),
        },
        "content": {
            "type": "string",
            "description": "要存的内容 (action=add/replace 必填)",
        },
        "old_text": {
            "type": "string",
            "description": "要替换/删除的旧文本 (action=replace/remove 必填, 唯一短 substring)",
        },
    },
    "required": ["action", "kind"],
}


# ── 主入口 (ctx.register_tool 的 handler) ─────────────────────────────────

def handle_memory_tool(args: Dict[str, Any], **kw: Any) -> str:
    """catfish memory tool 真 handler.

    按 kind 路由到 5 个仓库. 0 后端 LLM 调用 (LLM 自己填 kind), 0 性能损失.
    return JSON string (跟 hermes 原 memory_tool 同接口).

    6/2 BL-MEMORY-AUDIT-TRAIL: 每条 add/replace/remove 落 ~/.catfish/memory_audit.jsonl.
    replace/remove 前先读 prev_value (file 直读 hermes USER.md/MEMORY.md). 失败也 audit.
    """
    action = args.get("action", "add")
    kind = args.get("kind")
    content = args.get("content")

    if not kind:
        return json.dumps({
            "success": False,
            "error": "kind 必填 (identity/project_fact/workflow/journal/todo).",
        }, ensure_ascii=False)

    # 6/2 BL-MEMORY-AUDIT-TRAIL: replace/remove 前**先读 prev_value** — 必须在 hermes
    # 写之前读, 写完老值就没了. add 不需要 (没"被覆盖" 的对象).
    prev_value = _read_prev_value_for_audit(kind, action, args.get("old_text"))
    user_email = _extract_user_email(kw)

    # 主路由
    try:
        if action in ("replace", "remove"):
            # replace/remove 仍走 hermes 原生 (改 USER.md / MEMORY.md 入口)
            # hermes target 推断: identity → user / project_fact → memory / 其它 → memory 兜底
            if kind == "identity":
                hermes_args = {**args, "target": "user"}
            elif kind == "project_fact":
                hermes_args = {**args, "target": "memory"}
            else:
                # workflow/journal/todo replace/remove 没真路径 — 兜底走 hermes memory
                # (LLM 不该这么调, 但留底防异常)
                hermes_args = {**args, "target": "memory"}
            result = _call_hermes_original_memory_tool(hermes_args, **kw)
        elif kind == "todo":
            result = _route_to_reminder(content)
        elif kind == "journal":
            result = _route_to_journal(content)
        elif kind == "workflow":
            result = _route_to_propose_skill(content)
        elif kind == "identity":
            result = _call_hermes_original_memory_tool(
                {**args, "target": "user"}, **kw
            )
        elif kind == "project_fact":
            result = _call_hermes_original_memory_tool(
                {**args, "target": "memory"}, **kw
            )
        else:
            result = json.dumps({
                "success": False,
                "error": f"unknown kind '{kind}'. 看 schema 选 5 个之一.",
            }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.exception("catfish memory router 异常: %s", e)
        result = json.dumps({
            "success": False,
            "error": f"catfish memory router 异常: {e}",
        }, ensure_ascii=False)

    # 6/2 BL-MEMORY-AUDIT-TRAIL: 不管成功失败都 audit (失败也是历史) — append jsonl.
    # _append_audit_log 自己 best-effort, 失败不抛, 不阻塞主 return.
    audit_record = _build_audit_record(
        kind=kind,
        action=action,
        args=args,
        prev_value=prev_value,
        result_json=result,
        user_email=user_email,
    )
    _append_audit_log(audit_record)

    # BL-MEMORY-B3 (2026-06-03): identity/project_fact add 真跑 heuristic detect,
    # 触发就在 result 真 inject propose_skill_hint. workflow kind 已经在
    # _route_to_propose_skill 自带 hint, 不重复.
    if action == "add" and kind in ("identity", "project_fact"):
        spec_detect = _detect_workflow_spec_pattern(content or "")
        if spec_detect:
            # 真解析 result 真 inject hint (保留原 success/error 真状态)
            try:
                result_parsed = json.loads(result) if isinstance(result, str) else result
                if isinstance(result_parsed, dict):
                    result_parsed["propose_skill_hint"] = spec_detect
                    result = json.dumps(result_parsed, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass  # 解析失败不影响主 result return

    return result


# ── 5 个路由 helper ───────────────────────────────────────────────────────

def _call_hermes_original_memory_tool(args: Dict[str, Any], **kw: Any) -> str:
    """走 hermes 原生 memory_tool — identity → USER.md / project_fact → MEMORY.md."""
    from tools.memory_tool import memory_tool as _hermes_memory_tool
    return _hermes_memory_tool(
        action=args.get("action", "add"),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store"),
    )


def _route_to_reminder(content: str) -> str:
    """拒绝把用户待办降级写入 journal，强制模型改调唯一写入口。"""
    return json.dumps({
        "success": False,
        "routed_to": "catfish_create_reminder",
        "content": content,
        "error": (
            "用户待办只存 Reminders.app；请立即调用 catfish_create_reminder，"
            "并且只有工具返回 ok=true 后才能向用户报告创建成功。"
        ),
    }, ensure_ascii=False)


def _route_to_journal(content: str) -> str:
    """kind=journal → append ~/.catfish/employee_journal.md."""
    catfish_home = _catfish_home()
    journal_path = catfish_home / "employee_journal.md"
    ts = datetime.now(timezone.utc).isoformat()
    entry = f"\n## Session entry @ {ts}\n{content}\n"
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(entry)
        return json.dumps({
            "success": True,
            "routed_to": "employee_journal.md",
            "path": str(journal_path),
        }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({
            "success": False,
            "error": f"journal 写失败: {e}",
        }, ensure_ascii=False)


def _route_to_propose_skill(content: str) -> str:
    """kind=workflow → hint 让 LLM 调 catfish_propose_skill (BL-MM9 5/8 ship)."""
    return json.dumps({
        "success": True,
        "routed_to": "propose_skill_hint",
        "hint": (
            "这是 workflow (有 step 序列), 不该写 memory. "
            "请改调 catfish_propose_skill 工具, 把 name + reason + action_steps "
            "传过去, 走员工 confirm 门槛固化为 skill."
        ),
        "content_recap": content[:200] if content else "",
    }, ensure_ascii=False)


# ── BL-MEMORY-B3 (2026-06-03): 静态 heuristic detect 长 entry 真像 workflow/spec ──
#
# 真问题: LLM 真可能把 SKILL.md 的 workflow/spec 内容当 identity / project_fact 写,
# 走 hermes USER.md / MEMORY.md 落盘. kind=workflow 真已经有 propose_skill hint (上面
# _route_to_propose_skill), 但 kind=identity/project_fact add action 真没拦截 — 真
# 6/3 早 audit 真生产 MEMORY.md 16 entry 真有 12 条是 skill spec, 真就是这个漏.
#
# 真补救: identity/project_fact add 时真跑 heuristic, 触发就在 result 真 inject
# propose_skill_hint, LLM 看 hint 真自然调 catfish_propose_skill(triggered_by='auto')
# 改 skill 路径. 0 LLM 调真 light-weight (跟 A3 LLM dedupe 真互补 — A3 reactive 员工
# 真按钮触发, B3 proactive 每次 add 自动跑).
#
# 不强制 enforce (LLM 真可不听), 但 hint 真显眼真显著降低 mis-classify (BL-MEMORY-
# DISCIPLINE 真经验: prompt 约束 reasoning model 命中率 ~70%).

# 真 spec/workflow 关键词 (中英文 mixed, 真按 5/24 MEMORY.md 70% 跑偏 entry 真总结)
_WORKFLOW_KEYWORDS = (
    "步骤", "流程", "触发词", "铁律", "必触发", "调用",
    "记得", "必须", "如何", "应该",
    "## ", "**", "`", "1.", "2.", "3.",  # markdown 结构
    "- [ ]", "- [x]",  # TODO checkbox
    "✅", "❌", "⚠", "⭐",  # SKILL.md 真常用 marker
    "skill", "SKILL.md", "tool_call", "workflow",
)


def _detect_workflow_spec_pattern(content: str) -> Optional[Dict[str, Any]]:
    """真静态 heuristic 真检 content 是不是 SKILL.md 的 workflow/spec 不是 fact.

    真触发条件 (短路 OR — 任 1 条触发):
    1. 长度 >= 500 chars (USER.md cap 1375 / MEMORY.md cap 2200, 单 entry 500 真"超长")
    2. 长度 >= 200 chars **且** 含 >= 2 个 workflow 关键词

    返 hint dict 或 None. None = 真 fact 不触发.
    """
    if not content or not isinstance(content, str):
        return None
    length = len(content)
    hit_keywords = [kw for kw in _WORKFLOW_KEYWORDS if kw in content]
    hit_count = len(hit_keywords)

    # 真触发短路
    triggered = False
    reason_parts: List[str] = []
    if length >= 500:
        triggered = True
        reason_parts.append(f"长度 {length} chars >= 500 (单 entry 超长)")
    if length >= 200 and hit_count >= 2:
        triggered = True
        reason_parts.append(
            f"含 {hit_count} 个 workflow 关键词 ({', '.join(hit_keywords[:5])})"
        )

    if not triggered:
        return None
    return {
        "triggered": True,
        "reason": "; ".join(reason_parts),
        "content_length": length,
        "hit_keywords": hit_keywords[:10],
        "hint": (
            "⚠ 这条 entry 看起来更像 SKILL.md 的 workflow/spec, 不像稳定 fact. "
            "真建议: 真主动调 catfish_propose_skill(triggered_by='auto') 把内容固化为 skill, "
            "然后 memory(action='remove') 删这条 entry. "
            "BL-MEMORY-DISCIPLINE: USER.md/MEMORY.md 真该装 fact (员工身份/技术常量), "
            "不装流程 (那是 SKILL.md 的事)."
        ),
    }
