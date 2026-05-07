"""适配层 —— 把 hermes ToolRegistry 包装成 JSON-RPC 友好的接口。

核心两个动作:
    list_tools()         → OpenAI tool calling 兼容的 schema 数组
    dispatch_tool(...)   → 同步/异步 dispatch + 截断超大结果

为啥要适配:
    hermes 的 registry.dispatch 内部签名跟 hermes 自己的 agent context 强相关,
    我们 Companion 这边没有那个 context —— 所以这层做"最小可工作"的封装,
    把不需要 context 的 tool 直接调通,有 context 依赖的 tool 报清楚错。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import traceback
from typing import Any, Dict, List

import time

from . import audit, catfish_tools, sandbox, skill_watcher

logger = logging.getLogger("catfish.tool_bridge.adapter")

# bootstrap 后由 server 注入
_registry_module = None


def install_registry(registry_module) -> None:
    """server 启动后调一次,把 hermes 的 tools.registry 模块塞进来"""
    global _registry_module
    _registry_module = registry_module


def _r():
    """快捷拿 ToolRegistry singleton"""
    if _registry_module is None:
        raise RuntimeError("registry 还没 bootstrap — 先调 install_registry")
    return _registry_module.registry


# ============================================================
# tools/list
# ============================================================

# 五一 sprint 5/3 收尾: hermes 工具响应里含 ~/.hermes/... 路径, LLM 看到困惑后
# 跟员工说"memory tool 不可用因为 hermes 没初始化, 存到 ~/.hermes/...", 暴露品牌.
#
# **不能直接屏蔽** — memory_save 是真跨 session 永久记忆能力, 屏了鲶鱼就丢这个.
# 改用响应包裹: tool 真返回原样写存储, 但给 LLM 看的 response 把 hermes 字眼 + 路径
# 都过滤掉, LLM 不再因 path 困惑.
#
# 见 dispatch_tool 处理.
#
# 这些 hermes tool 名字本身可能也得 rebrand, 但改名涉及 hermes 内部映射, 风险大,
# Phase 2 后做.

# Hermes 暴露品牌字眼的工具 — 调度后 response 走 sanitizer.
_HERMES_TOOLS_NEEDS_BRAND_SCRUB = {
    "memory",
    "memory_save",
    "memory_load",
    "memory_search",
}


# ============================================================
# BL-MM3 (5/7): hermes memory_save 包一层版本化
# ============================================================
#
# 背景: hermes memory_save 默认是 silent overwrite — 同 name 第二次写直接覆盖,
#       LLM 看不到旧值, 跨 session 永久记忆"改不删, 留版本"纪律 (BL-MM1) 落不下来.
#
# 方案: read-modify-write 双调用模拟版本数组 — 在 dispatch 层包一层 wrapper:
#       1. 先 memory_recall 取旧值
#       2. 拼新 content + inline 旧值备注 ("---\n_(BL-MM3 上次值, 已废: ...)_)
#       3. 调真 hermes memory_save 写
#       4. 返字段对齐 BL-MM2 (previous_value / overwrite / no_change / summary),
#          让 SOUL § BL-MM1 "回员工时主动 quote 旧值" 纪律生效
#
# 兼容:
#   - hermes 0.10/0.11/0.12 memory_save 接口都是 (name, content), 无变化
#   - read 失败 (memory_recall 抛 / 不可用) → 兜底按"首次记"处理, 不阻塞写
#   - 同值再写 → 仍调 memory_save (因为 hermes 可能有 ts 元数据), 但不加 inline 备注
#
# 跟 BL-MM2 (catfish_remember) 区别:
#   BL-MM2 → 当前 session 内事实 (~/.catfish/session_facts.json), 真存版本数组
#   BL-MM3 → 跨 session 永久记忆 (~/.hermes/memories/*.md), 单 .md 文件 + inline 备注
#   两个一起用 = "session 内硬事实 + 跨 session 偏好" 双层版本化记忆.

# inline 备注前缀, 测试 / verify 用同一个常量
_MM3_INLINE_PREFIX = "_(BL-MM3 上次值, 已废"
_MM3_INLINE_MAX_OLD_LEN = 200  # 旧值塞 inline 时截到这么长


def _extract_recall_text(raw: Any) -> str:
    """从 hermes memory_recall 返回里提取文本.

    hermes 0.10-0.12 memory_recall 返回 shape 不固定:
      - dict: {"content": "..."} / {"text": "..."} / {"value": "..."}
      - list: [{"content": "..."}, ...]  (取第一个的 content)
      - str:  直接是文本
      - None / 空 dict: 没找到
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for k in ("content", "text", "value", "result"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    if isinstance(raw, list):
        if not raw:
            return ""
        first = raw[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return _extract_recall_text(first)
        return ""
    return ""


def _strip_old_inline_block(content: str) -> str:
    """剥掉上一轮 BL-MM3 加的 inline 备注块, 取出"真新值".

    每次 versioned save 都会拼上 `\\n\\n---\\n_(BL-MM3 上次值...)_` 备注.
    下次再写时, 我们 read 回来的 old_text 是"含上轮备注的版本",
    要把上轮备注剥掉, 不然 inline 块会越叠越长.
    """
    if not content:
        return content
    marker = f"\n\n---\n{_MM3_INLINE_PREFIX}"
    idx = content.find(marker)
    if idx == -1:
        return content
    return content[:idx].rstrip()


async def _memory_save_versioned(args: Dict[str, Any]) -> Dict[str, Any]:
    """BL-MM3: hermes memory_save 包一层版本化.

    返回标准 dispatch shape: {"ok", "tool", "result", "error"}.

    args 兼容两种字段名:
      hermes 原生: {"name": "...", "content": "..."}
      catfish 习惯: {"key": "...", "value": "..."} (兼容旧测试)
    """
    name = (args.get("name") or args.get("key") or "").strip()
    new_content = args.get("content") if args.get("content") is not None else args.get("value")
    if isinstance(new_content, str):
        new_content = new_content.strip()
    if not name:
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": "memory_save 需要 name (或 key) — BL-MM3 wrapper 拿不到 name 没法 read 旧值",
        }
    if not new_content or not isinstance(new_content, str):
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": "memory_save 需要 content (或 value) 字符串",
        }

    r = _r()

    # 1. read 旧值 (失败兜底: 按"首次记"处理, 不阻塞 write)
    old_text_raw = ""
    read_failed = False
    try:
        if "memory_recall" in r.get_all_tool_names():
            disp = r.dispatch
            if inspect.iscoroutinefunction(disp):
                old_resp = await disp("memory_recall", {"query": name})
            else:
                old_resp = await asyncio.to_thread(disp, "memory_recall", {"query": name})
            old_text_raw = _extract_recall_text(old_resp)
    except Exception as e:
        logger.info("memory_save_versioned: read 旧值失败 (%s), 按首次记兜底", e)
        read_failed = True
        old_text_raw = ""

    # 剥掉上一轮的 inline 备注块, 拿到"上一轮的真新值"
    old_text = _strip_old_inline_block(old_text_raw)

    # 2. 拼新 content
    is_overwrite = bool(old_text)
    no_change = is_overwrite and old_text.strip() == new_content.strip()
    if is_overwrite and not no_change:
        prev_truncated = old_text[:_MM3_INLINE_MAX_OLD_LEN].replace("\n", " ")
        ellipsis = "…" if len(old_text) > _MM3_INLINE_MAX_OLD_LEN else ""
        wrapped_content = (
            f"{new_content}\n\n"
            f"---\n"
            f"{_MM3_INLINE_PREFIX}, ts={time.strftime('%Y-%m-%d %H:%M')}: "
            f"{prev_truncated}{ellipsis})_"
        )
    else:
        # 首次 / 同值 → 不加 inline 块
        wrapped_content = new_content

    # 3. 调真 hermes memory_save (传 name + content, 兼容历史 args 里的额外字段)
    save_args = {k: v for k, v in args.items() if k not in ("key", "value")}
    save_args["name"] = name
    save_args["content"] = wrapped_content
    try:
        disp = r.dispatch
        if inspect.iscoroutinefunction(disp):
            raw_save = await disp("memory_save", save_args)
        else:
            raw_save = await asyncio.to_thread(disp, "memory_save", save_args)
    except Exception as e:
        logger.exception("memory_save_versioned: write 失败 (name=%s)", name)
        return {
            "ok": False, "tool": "memory_save", "result": None,
            "error": f"{type(e).__name__}: {e}",
        }

    # 4. 包装返回 — 字段对齐 BL-MM2 (previous_value / overwrite / no_change / summary)
    if no_change:
        summary = (
            f"'{name}' 跨 session 记忆已是这个值, 没改写历史. "
            f"(读旧值后发现 == 新值)"
        )
    elif is_overwrite:
        prev_preview = old_text[:80] + ("…" if len(old_text) > 80 else "")
        summary = (
            f"更新了跨 session 记忆 '{name}'. 上次值: {prev_preview!r}. "
            f"按 BL-MM1 纪律, 你回员工时**必须**主动 quote 旧值 "
            f"(\"我之前记的是 X, 现在改成 Y\"), 不要装作从来没记过."
        )
    else:
        suffix = " (read_old 失败已兜底)" if read_failed else ""
        summary = f"记下了跨 session 记忆 '{name}' (首次){suffix}."

    result_payload: Dict[str, Any] = {
        "type": "ok",
        "name": name,
        "previous_value": old_text if old_text else None,
        "overwrite": is_overwrite,
        "no_change": no_change,
        "read_old_ok": not read_failed,
        "summary": summary,
        # 真 hermes 返回也带回 (供 LLM 看到 path/状态; 主流程会经 scrub_brand 脱敏)
        "raw_save_response": raw_save,
    }
    return {"ok": True, "tool": "memory_save", "result": result_payload, "error": None}


def list_tools() -> List[Dict[str, Any]]:
    """返回 OpenAI tool calling 兼容的 tool definitions。

    输出顺序: catfish 原生 tools 排前面 (优先曝光给 LLM, prompt 里它们更早被
    扫到), 然后是 hermes 的 builtin tools 按字母序。
    """
    r = _r()
    out: List[Dict[str, Any]] = list(catfish_tools.CATFISH_NATIVE_TOOLS)

    names = sorted(r.get_all_tool_names())
    for name in names:
        # 防止重名 —— 极端情况下 catfish 想 "覆写" hermes 的 tool
        if catfish_tools.is_native(name):
            continue
        try:
            entry = r.get_entry(name)
            schema = r.get_schema(name)
            toolset = r.get_toolset_for_tool(name) or ""
            # 用 toolset 维度判可用性（hermes 的 check_tool_availability
            # 返回 tuple, 不是 bool, 不能直接用）
            available = bool(r.is_toolset_available(toolset)) if toolset else True
            out.append({
                "name": name,
                "description": getattr(entry, "description", "") or "",
                "input_schema": schema or {},
                "emoji": r.get_emoji(name) or "",
                "toolset": toolset,
                "available": available,
            })
        except Exception as e:
            logger.warning("list_tools: skip %s — %s", name, e)
    return out


# ── 品牌脱敏 (五一 sprint 5/3 加) ───────────────────────────────


def _scrub_brand_leaks(text: str) -> str:
    """从 tool response 文字里清掉 hermes 字眼 + 内部路径, 防 LLM 看到后向员工泄漏.

    替换 (大小写敏感, hermes 大小写都换):
      - ~/.hermes/...       → 鲶鱼本机存储
      - /Users/.../.hermes  → 鲶鱼本机存储
      - hermes / Hermes     → 鲶鱼  (注意: 工具名 hermes_xxx 不动, 只换独立词)
    """
    import re as _re
    # 先换路径 (优先级高于单词替换)
    text = _re.sub(r"~/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    text = _re.sub(r"/Users/[^/\s]+/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    text = _re.sub(r"/home/[^/\s]+/\.hermes(/[^\s'\")]*)?", "鲶鱼本机存储", text)
    # 再换独立的 hermes 词 (\b 词边界, 防误伤 hermes_xxx 工具名)
    text = _re.sub(r"\bhermes\b", "鲶鱼", text, flags=_re.IGNORECASE)
    return text


def scrub_brand_in_result(tool_name: str, result: Any) -> Any:
    """对会泄漏品牌字眼的 hermes tool 响应做脱敏.

    其他 tool 不动 (避免误伤 catfish_skill_install 等真路径返回).
    """
    if tool_name not in _HERMES_TOOLS_NEEDS_BRAND_SCRUB:
        return result
    if isinstance(result, str):
        return _scrub_brand_leaks(result)
    if isinstance(result, dict):
        out: Dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, str):
                out[k] = _scrub_brand_leaks(v)
            elif isinstance(v, (dict, list)):
                out[k] = scrub_brand_in_result(tool_name, v)
            else:
                out[k] = v
        return out
    if isinstance(result, list):
        return [scrub_brand_in_result(tool_name, item) for item in result]
    return result


# ============================================================
# tools/dispatch
# ============================================================

#: execute_code 误用守卫 — sandbox 子进程拿不到 hermes session, 调 catfish_*
#: 必死锁. 这些子串只要在脚本里出现, 大概率是模型搞错 (踩过坑 2026-04-28 鸿波 demo).
_EXECUTE_CODE_FORBIDDEN_PATTERNS = (
    "catfish_browser_",
    "catfish_screenshot",
    "catfish_skill_",
    "catfish_tool_bridge",
    "import catfish_",
    "from catfish_",
)


def _check_execute_code_misuse(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 沙箱误调用 catfish 工具. 命中返回 friendly error 字符串.

    返回 None = OK; 字符串 = 应该立即拒绝 + 把字符串塞进 error 字段.

    为啥拦: hermes execute_code 是 bash/python sandbox 子进程, 跟 hermes 主进程
    完全隔离, 拿不到 tool-bridge unix socket / browser session. 模型在脚本里
    `import catfish_browser_*` 或调对应函数必死锁等 30s timeout, 浪费员工时间.
    SOUL.md § execute_code 红线已经写过纪律, 这里加工程兜底.
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
    # 拼起来: code / command / input 等常见字段
    text_parts: list[str] = []
    for key in ("code", "command", "input", "script", "args"):
        v = args.get(key)
        if isinstance(v, str):
            text_parts.append(v)
        elif isinstance(v, list):
            text_parts.extend(str(x) for x in v if isinstance(x, str))
    text = "\n".join(text_parts).lower()
    if not text:
        return None
    hits = [p for p in _EXECUTE_CODE_FORBIDDEN_PATTERNS if p.lower() in text]
    if not hits:
        return None
    return (
        f"⚠️ {name} 沙箱里检测到 catfish 工具调用 ({hits[0]}). "
        f"这必失败 — sandbox 子进程拿不到 hermes browser session / tool-bridge socket. "
        f"请用原生 tool calling 直接调 catfish_browser_* 等, 不要写脚本调. "
        f"详见 SOUL.md § execute_code 红线."
    )


# 5/6 安全 P1 G3: execute_code 安全守卫.
#
# 真正的 sandbox 在 hermes 那边 (我们这边只是 dispatcher), 但能在 dispatcher 层
# 拦"明显不合规"的脚本: 越权 path / 外联 / 危险 shell / 凭证读取.
# 不绝对完备 (能被 obfuscate 绕), 但显式拦截 = "鲶鱼明确禁止这种行为", audit 留痕.
#
# 命中时:
#   - 默认: 返回 error (LLM 看到, 不执行)
#   - env CATFISH_EXEC_GUARD=warn: 只 log, 不拦 (开发期调试用)
_EXECUTE_CODE_DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    # ── 凭证 / 敏感目录 ────────────────────────────
    ("~/.ssh", "读员工 SSH 私钥 — 严禁"),
    ("/.ssh/id_", "读员工 SSH 私钥 — 严禁"),
    ("~/.aws/credentials", "读 AWS 凭证 — 严禁"),
    ("~/.docker/config.json", "读 Docker registry 凭证 — 严禁"),
    ("/library/keychains", "读 macOS Keychain — 严禁 (用 secret_resolver / keychain://)"),
    ("/etc/shadow", "读 Linux 密码 hash — 严禁"),
    ("/etc/passwd", "读系统账户清单 — 严禁"),
    ("netrc", "读 ~/.netrc 凭证 — 严禁"),
    # ── 网络外联 (data exfil 风险) ────────────────
    ("curl http", "外联网络 — 严禁 (用 catfish_browser_* / catfish_fetch_url, 走 audit)"),
    ("curl -x", "外联网络 — 严禁"),
    ("wget http", "外联网络 — 严禁"),
    ("requests.get(", "Python 外联网络 — 严禁 (走 catfish_fetch_url 留 audit)"),
    ("requests.post(", "Python 外联网络 — 严禁"),
    ("urllib.request.urlopen(", "Python 外联网络 — 严禁"),
    ("urllib2.urlopen(", "Python 外联网络 — 严禁"),
    ("httpx.get(", "Python 外联网络 — 严禁"),
    ("httpx.post(", "Python 外联网络 — 严禁"),
    ("aiohttp.clientsession", "Python 外联网络 — 严禁"),
    ("socket.connect(", "Python raw socket — 严禁"),
    # ── 危险 shell ──────────────────────────────
    ("rm -rf /", "递归删根目录 — 严禁"),
    ("rm -rf ~", "递归删 home — 严禁"),
    (":(){:|:&};:", "fork bomb — 严禁"),
    ("dd if=/dev/", "raw disk 操作 — 严禁"),
    ("mkfs.", "格式化 — 严禁"),
    ("> /dev/sd", "写裸盘 — 严禁"),
    ("chmod 777 /", "全盘权限放开 — 严禁"),
)


def _check_execute_code_security(name: str, args: Dict[str, Any]) -> str | None:
    """检测 execute_code 脚本里的危险操作 (越权/外联/凭证). 命中返回 error 字符串.

    限定 execute_code / shell_exec / python / bash 工具.
    跟 _check_execute_code_misuse 协同: misuse 拦"调错 catfish 工具" (功能错),
    security 拦"做坏事" (安全错).

    env CATFISH_EXEC_GUARD=warn 只 log 不拦 (开发期 / 信任环境用).
    """
    if name not in {"execute_code", "shell_exec", "python", "bash"}:
        return None
    text_parts: list[str] = []
    for key in ("code", "command", "input", "script", "args"):
        v = args.get(key)
        if isinstance(v, str):
            text_parts.append(v)
        elif isinstance(v, list):
            text_parts.extend(str(x) for x in v if isinstance(x, str))
    text = "\n".join(text_parts).lower()
    if not text:
        return None

    hits = [(p, reason) for p, reason in _EXECUTE_CODE_DANGEROUS_PATTERNS if p.lower() in text]
    if not hits:
        return None

    pattern, reason = hits[0]
    mode = (os.environ.get("CATFISH_EXEC_GUARD") or "deny").strip().lower()
    msg = (
        f"🛡️ execute_code 安全守卫拦截: 检测到 {pattern!r} — {reason}. "
        f"鲶鱼禁止 LLM 通过 execute_code 做这些. "
        f"如需读特定文件/调 API, 用对应的 catfish_* 工具走 audit log."
    )
    if mode == "warn":
        # warn 模式只记录不拦 (默认 deny, 开发期调试可设 warn)
        logger.warning("[exec_guard:warn] %s | text 前 200 字: %s", msg, text[:200])
        return None
    return msg


async def dispatch_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """调用一个 tool。

    返回的字典固定 shape:
        {"ok": bool, "result": <jsonable> | None, "error": str | None,
         "tool": name, "stderr": str | None}

    每次调用末尾会写一行 audit 事件到 ~/.hermes/.catfish_audit.jsonl
    (用于 Skill lifecycle 阶段 4 健康面板 / 30 天提醒 / 失败率告警 / billing).
    audit 写失败不影响 dispatch 主流程.
    """
    # 给 skill_watcher 标记"现在 LLM 在繁忙地用工具", 防它在 LLM 调用循环中突然
    # 重启 tool-bridge. 这是廉价操作 (一次 lock + 时间戳更新)。
    skill_watcher.mark_dispatch()

    # 守卫: execute_code 沙箱误调用 catfish 工具 → 立即拒绝, 不让模型死等 timeout.
    misuse_msg = _check_execute_code_misuse(name, args)
    if misuse_msg:
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": misuse_msg,
            "stderr": None,
        }

    # 5/6 安全 G3 守卫: execute_code 危险操作 (越权/外联/凭证) → 拒绝 + audit 留痕.
    sec_msg = _check_execute_code_security(name, args)
    if sec_msg:
        # 写 audit 留痕 — 客户信安部门能查到"谁在 X 时间试图越权"
        try:
            audit.write_event(
                tool=name,
                ok=False,
                args=args,
                error=sec_msg,
                latency_ms=0.0,
                extra={"security_block": "exec_guard"},
            )
        except Exception:
            pass  # audit 写失败不影响拦截
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": sec_msg,
            "stderr": None,
        }

    start = time.time()
    result = await _do_dispatch(name, args)
    latency_ms = (time.time() - start) * 1000

    # 写 audit (永远不抛, 不影响主流程返回).
    # 工具结果里 'security_audit' 字段 (例: 'credential_field_filled') 也并入 audit log
    # 让 IT 事后能 grep 谁在啥时候填了密码字段.
    audit_extra: dict[str, Any] = {}
    inner_result = result.get("result")
    if isinstance(inner_result, dict):
        if "security_audit" in inner_result:
            audit_extra["security_audit"] = inner_result["security_audit"]
        # BL-S29.2 (5/7): 沙箱字段进 audit log, 客户信安能 grep
        # `cat ~/.hermes/.catfish_audit.jsonl | jq 'select(.sandbox_used == true)'`
        # 看哪次 execute_code 真在沙箱里跑过.
        if "sandbox_used" in inner_result:
            audit_extra["sandbox_used"] = inner_result["sandbox_used"]
        if "sandbox_kind" in inner_result:
            audit_extra["sandbox_kind"] = inner_result["sandbox_kind"]
        if inner_result.get("timed_out"):
            audit_extra["sandbox_timed_out"] = True

    audit.write_event(
        tool=name,
        ok=result["ok"],
        args=args,
        error=result.get("error"),
        latency_ms=latency_ms,
        extra=audit_extra or None,
    )

    # 五一 sprint 5/3: hermes memory_* 工具响应里含 ~/.hermes/... 路径 + 'hermes'
    # 字眼, LLM 看到后向员工泄漏品牌. audit 已经写了原始 (内部审计需要), 这里只对
    # 给 LLM 看的 result 和 error 做脱敏. 其他 tool 不动.
    if name in _HERMES_TOOLS_NEEDS_BRAND_SCRUB:
        if result.get("result") is not None:
            try:
                result["result"] = scrub_brand_in_result(name, result["result"])
            except Exception:
                logger.exception("scrub_brand_in_result failed for %s — leaving raw", name)
        # 失败路径 (raise 后 _do_dispatch 把 exception message 塞 error 字段),
        # error 里也常带 ~/.hermes / hermes 字眼, 必须脱敏
        if isinstance(result.get("error"), str):
            try:
                result["error"] = _scrub_brand_leaks(result["error"])
            except Exception:
                pass
    return result


async def _do_dispatch(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """实际 dispatch 逻辑. 抽出来让 dispatch_tool 可以包 audit."""
    # 先看 catfish 原生 tool —— 这些不走 hermes registry, 也不要求 toolset
    # 可用性检查 (它们就是 catfish 自己的代码, 一定在)
    if catfish_tools.is_native(name):
        try:
            raw = await asyncio.to_thread(catfish_tools.dispatch_native, name, args)
            return {"ok": True, "tool": name, "result": raw, "error": None}
        except Exception as e:
            logger.exception("native dispatch failed: %s", name)
            return {
                "ok": False, "tool": name, "result": None,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[:2000],
            }

    # ============================================================
    # BL-S29.2 (5/7): execute_code/python/bash/sh 走 macOS sandbox-exec
    # 隔离子进程, 不再转给 hermes dispatch.
    #
    # 启用条件: env CATFISH_SANDBOX_EXEC=1 + macOS + sandbox-exec 在 PATH
    # 不启用 / 非 macOS / 沙箱不支持 → 走原 hermes dispatch (兼容)
    # 5/19 BL-S29.5 加 nsjail (Linux) + Docker fallback 双层 fallback
    # ============================================================
    sandbox_lang = sandbox.detect_lang_from_tool_name(name)
    if (
        sandbox_lang is not None
        and sandbox.is_sandbox_enabled()
        and sandbox.is_sandbox_supported()
    ):
        # 拿代码体: 不同 hermes 工具字段名不一样, 兜底逐个看
        code = (
            args.get("code")
            or args.get("script")
            or args.get("command")
            or args.get("input")
            or ""
        )
        if not code:
            return {
                "ok": False, "tool": name, "result": None,
                "error": f"沙箱模式: 工具 {name} 调用未提供 code/script/command 字段",
            }
        timeout_s = int(args.get("timeout_s") or args.get("timeout") or 30)
        try:
            sb_result = await asyncio.to_thread(
                sandbox.run_in_sandbox,
                code,
                lang=sandbox_lang,
                timeout_s=timeout_s,
            )
        except Exception as e:
            logger.exception("sandbox dispatch crashed: %s", name)
            return {
                "ok": False, "tool": name, "result": None,
                "error": f"sandbox crash: {type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[:2000],
            }
        # 把沙箱结果包装成跟 hermes execute_code 兼容的形状, LLM 看不出区别
        return {
            "ok": sb_result["ok"],
            "tool": name,
            "result": {
                "stdout": sb_result["stdout"],
                "stderr": sb_result["stderr"],
                "returncode": sb_result["rc"],
                "elapsed_ms": sb_result["elapsed_ms"],
                "timed_out": sb_result["timed_out"],
                # 这俩字段进 audit, 客户信安能看到 "这次 execute_code 跑在沙箱里"
                "sandbox_used": sb_result["sandbox_used"],
                "sandbox_kind": sb_result["sandbox_kind"],
            },
            "error": None if sb_result["ok"] else (
                f"代码非 0 退出 (rc={sb_result['rc']})"
                + (" [timeout]" if sb_result["timed_out"] else "")
            ),
        }

    r = _r()
    if name not in r.get_all_tool_names():
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"unknown tool: {name}",
        }

    # ============================================================
    # BL-MM3 (5/7): memory_save 走版本化 wrapper, 不直打 hermes
    # ============================================================
    # wrapper 内部用 r.dispatch 调真 memory_save / memory_recall, 不会死循环.
    # disable 开关: env CATFISH_DISABLE_MM3=1 (调试时跳过 wrapper, 直打 hermes)
    if name == "memory_save" and os.environ.get("CATFISH_DISABLE_MM3") != "1":
        return await _memory_save_versioned(args)

    # 用 toolset 维度判可用性 (而不是 check_tool_availability，因为它返回 tuple)
    toolset = r.get_toolset_for_tool(name)
    if toolset and not r.is_toolset_available(toolset):
        return {
            "ok": False, "tool": name, "result": None,
            "error": f"toolset '{toolset}' 在当前环境不可用（依赖未装/env 未配/平台不支持）",
        }

    try:
        dispatch_fn = r.dispatch
        if inspect.iscoroutinefunction(dispatch_fn):
            raw = await dispatch_fn(name, args)
        else:
            # to_thread 跑 sync dispatch 避免堵 asyncio loop
            raw = await asyncio.to_thread(dispatch_fn, name, args)

        # hermes 的 max_result_size 是 per-tool 配置，需要传 name
        try:
            max_size = r.get_max_result_size(name)
        except Exception:
            max_size = 100_000  # 兜底 100KB
        truncated = _truncate_for_ipc(raw, max_size)
        return {
            "ok": True, "tool": name, "result": truncated, "error": None,
        }
    except Exception as e:
        logger.exception("dispatch_tool failed: %s", name)
        return {
            "ok": False,
            "tool": name,
            "result": None,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[:2000],
        }


def _truncate_for_ipc(value: Any, max_size: int) -> Any:
    """把超大字符串/列表截断,免得 socket 一条 line 撑爆。"""
    import json
    try:
        as_json = json.dumps(value, ensure_ascii=False)
    except Exception:
        return repr(value)[:max_size]
    if len(as_json) <= max_size:
        return value
    return {
        "_truncated": True,
        "preview": as_json[:max_size],
        "original_size_bytes": len(as_json),
    }


# ============================================================
# health
# ============================================================

def health() -> Dict[str, Any]:
    r = _r()
    return {
        "ok": True,
        "tool_count": len(r.get_all_tool_names()) + len(catfish_tools.CATFISH_NATIVE_TOOLS),
        "native_tool_count": len(catfish_tools.CATFISH_NATIVE_TOOLS),
        "toolsets": list(r.get_registered_toolset_names()) + ["catfish_native"],
    }
