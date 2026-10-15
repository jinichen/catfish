"""P59 · 点过「始终允许」就别再问, 删除等严重破坏的除外 (10/15 鸿波)。

员工原话: 「只要点过始终允许, 下一次就不要再审批了, 除非那种删除等严重破坏内容的指令,
其实一堆的指令给非程序员, 他也是不会去看的, 看也看不懂, 现在这种模式反倒造成困扰。」
起因是 EIS 资质待办 skill: 一轮任务弹好几次 execute_code 审批, 点了 9 次「始终允许」全白点
—— P20 (6/22) 把 execute_code 的永久批准拦掉了, 按钮却还在; 「本会话允许」在 Companion 里
也只管这一条消息 (hermes api_server 拿 completion id 当审批会话)。定时任务里 execute_code
又被 cron_mode=deny 一律拒掉, skill 没法放进定时自动化。

现在:
  · execute_code 点过「始终允许」(config.yaml command_allowlist 里有 execute_code) →
    以后的脚本直接放行, 聊天 / 微信 / 定时任务都一样, 记一笔 always_rule 审计。
  · 但 destructive_code 判出有删除 / 结束进程 / 格式化 / 改安全设置等动作的脚本 →
      - 有人在 (聊天 / 微信): 不管点没点过「始终允许」都问; 不走 smart 判断 (必须人点头);
        这次的「本会话 / 始终允许」不记 (只算这一次); 审批卡说明写「每次都要确认」+ 原因。
      - 没人在 (定时任务 / 无人值守): 直接不执行, 让模型告诉员工这一步没做。
  · 没点过「始终允许」的照旧: 只读免批 (P54) → smart 判断 → 问人。

终端命令: 只有 hermes 危险命令表里的才会弹审批, 其中删除类的 (destructive_code._zh 认得出来的)
P20 不许永久批准, 这里也一样每次问人、不走 smart、说明换成「每次都要确认」—— 同一条红线。

实现: 包 tools.approval.check_execute_code_guard (装在 P54 里面: 只读的先被 P54 放行);
「强制问人」靠一个 ContextVar —— 包 is_approved (此时不认已有批准) 和 _human_decision
(改说明、关 smart、不记会话 / 永久批准)。两处都是 check_execute_code_guard 运行时按模块全局名
取的, 所以包模块属性就生效。
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, List, Optional

logger = logging.getLogger("catfish.xcatfish_user.plugin")

#: 审批卡认这个前缀: 有它就不显示「本会话 / 始终允许」(Companion lib/approvalScope.ts)
EVERY_TIME_MARK = "⚠️ 每次都要确认"

#: 审批留痕 (approval_audit 模块), plugin.py 注入
audit = None

_FORCE_ASK: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar(
    "catfish_p59_force_ask", default=None)


def _load_destructive():
    """同目录的 destructive_code.py (包名带 dash, 跟 plugin_approval_ux._load_readonly 同套路)。"""
    try:
        from . import destructive_code  # type: ignore
        return destructive_code
    except Exception:  # noqa: BLE001
        pass
    try:
        import destructive_code  # type: ignore
        return destructive_code
    except ImportError:
        pass
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).with_name("destructive_code.py")
    spec = importlib.util.spec_from_file_location("catfish_destructive_code", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["catfish_destructive_code"] = mod
    spec.loader.exec_module(mod)
    return mod


def is_destructive_pattern_key(pattern_key: str) -> bool:
    """终端危险命令的 pattern_key 就是 hermes 的英文描述; 认得出是删除等破坏 → True。"""
    if not pattern_key or pattern_key == "execute_code":
        return False
    try:
        return _load_destructive()._zh(pattern_key) is not None
    except Exception:  # noqa: BLE001 — 判不了就当破坏 (不许永久批准)
        return True


def every_time_description(reasons: List[str]) -> str:
    return (f"{EVERY_TIME_MARK}: 这段代码会{'、'.join(reasons[:3])}。"
            "删除这类操作每次都要你同意, 「始终允许」管不到它。")


def _unattended(approval) -> bool:
    fn = getattr(approval, "_unattended_contexts", None)
    try:
        return bool(fn()) if fn else False
    except Exception:  # noqa: BLE001
        return False


def _always_allowed(approval) -> bool:
    fn = getattr(approval, "_is_permanently_approved", None)
    try:
        return bool(fn("execute_code")) if fn else False
    except Exception:  # noqa: BLE001
        return False


def _record(choice: str, code: str, description: str) -> None:
    if audit is not None:
        audit.record(choice, decided_by="always_rule", command=code, description=description,
                     pattern_key="execute_code", surface="p59")


def wrap_execute_code_guard(orig, finder, approval):
    def patched(code, env_type, has_host_access=False, *args, **kwargs):
        try:
            reasons = finder(code or "")
        except Exception as e:  # noqa: BLE001 — 查不了就当破坏, 问人
            logger.warning("P59: 破坏性检查出错, 按要问人处理 (%s)", e)
            reasons = ["做一件检查不出来的事"]
        if reasons:
            desc = every_time_description(reasons)
            if _unattended(approval):
                _record("blocked_unattended", code, desc)
                return {"approved": False, "pattern_key": "execute_code", "description": desc,
                        "message": ("BLOCKED: 这段代码会" + "、".join(reasons[:3]) +
                                    "。定时 / 无人值守任务里不执行删除等破坏性操作 (每次都要员工当面同意)。"
                                    "必须告诉员工这一步没做, 不要换写法绕过。")}
            token = _FORCE_ASK.set(reasons)
            try:
                return orig(code, env_type, has_host_access, *args, **kwargs)
            finally:
                _FORCE_ASK.reset(token)
        if _always_allowed(approval):
            _record("always_auto", code, "点过「始终允许」, 没有删除等破坏性操作")
            return {"approved": True, "message": None}
        return orig(code, env_type, has_host_access, *args, **kwargs)

    patched.__dict__.update(getattr(orig, "__dict__", {}))
    patched._catfish_p59 = True
    return patched


def wrap_is_approved(orig):
    def patched(session_key, pattern_key, *args, **kwargs):
        if pattern_key == "execute_code" and _FORCE_ASK.get():
            return False
        return orig(session_key, pattern_key, *args, **kwargs)

    patched._catfish_p59 = True
    return patched


def wrap_human_decision(orig):
    def patched(spec, *args, **kwargs):
        reasons = _FORCE_ASK.get()
        key = kwargs.get("pattern_key") or ""
        if reasons and key == "execute_code":
            kwargs["description"] = every_time_description(reasons)
        elif is_destructive_pattern_key(key):
            # 终端里的删除类命令: 同一条红线 (P20 已不许永久批准), 说明也换成大白话 + 标记
            why = _load_destructive()._zh(key) or "做有破坏性的操作"
            kwargs["description"] = (f"{EVERY_TIME_MARK}: 这条命令会{why}。"
                                     "删除这类操作每次都要你同意, 「始终允许」管不到它。")
        else:
            return orig(spec, *args, **kwargs)
        kwargs["pattern_keys"] = []          # 点「本会话 / 始终允许」也只算这一次
        kwargs["smart"] = False              # 必须人点头, 不让 smart 判断代批
        return orig(spec, *args, **kwargs)

    patched._catfish_p59 = True
    return patched


def _patch_p59_always_means_always() -> None:
    try:
        import tools.approval as _approval
    except ImportError as e:
        logger.warning("P59: tools.approval import 失败 (%s), skip", e)
        return
    targets: List[Any] = [("check_execute_code_guard", lambda o: wrap_execute_code_guard(
        o, _load_destructive().find_destructive, _approval)),
        ("is_approved", wrap_is_approved), ("_human_decision", wrap_human_decision)]
    for attr, wrap in targets:
        orig = getattr(_approval, attr, None)
        if orig is None:
            logger.warning("P59: tools.approval.%s 不在 (hermes 改了?), skip 这一处", attr)
            continue
        if not getattr(orig, "_catfish_p59", False):
            setattr(_approval, attr, wrap(orig))
    logger.info("P59 始终允许对 execute_code 生效 (删除等破坏性操作每次问 / 定时任务不执行) patched")
