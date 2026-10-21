"""鲶鱼策略的规则匹配器。

每个 matcher 负责判断某个工具调用是否命中某条规则：先看类型是否相关，
再从 args 里挑出要检查的字段（命令字符串、文件路径、host 等），然后
比对规则。

全部是纯函数 (tool_name, args) -> bool，不依赖 Hermes 运行时就能单测。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Helpers -- extract useful fields from a heterogeneous args dict
# ---------------------------------------------------------------------------

# Hermes tool names that execute shell commands or code
_SHELL_TOOL_NAMES = {
    "bash", "shell", "run_command", "execute_code", "terminal",
    "run_shell_command", "cmd", "exec",
}

# Arg keys commonly used to pass shell command strings
_SHELL_ARG_KEYS = ("command", "cmd", "code", "input", "script", "bash")

# Tools that read files
_FILE_READ_TOOL_NAMES = {
    "read_file", "cat", "tail", "head", "open_file", "view_file",
}

# Arg keys for file paths
_FILE_ARG_KEYS = ("path", "file", "filepath", "file_path", "filename")

# Tools that write files (hermes v0.22: write_file / patch)
_FILE_WRITE_TOOL_NAMES = {"write_file", "patch"}

# patch 工具 mode=patch 时路径写在补丁正文里: "*** Update File: <path>"
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$", re.MULTILINE)

# skill_manage 每个操作里放正文的字段 (create/patch 的 content, patch 的 new_string,
# write_file 的 file_content)。old_string 是"被替换掉的旧内容", 不算写进去的东西。
_SKILL_TEXT_KEYS = ("content", "new_string", "file_content")

# Tools that make network requests
_NETWORK_TOOL_NAMES = {
    "fetch_url", "web_fetch", "http_get", "http_post", "curl",
    "bash", "execute_code",   # these can embed curl/wget
}


def _extract_shell_command(tool_name: str, args: dict[str, Any]) -> Optional[str]:
    if tool_name not in _SHELL_TOOL_NAMES:
        return None
    for k in _SHELL_ARG_KEYS:
        if k in args and isinstance(args[k], str):
            return args[k]
    return None


def _extract_filepath(tool_name: str, args: dict[str, Any]) -> Optional[str]:
    if tool_name not in _FILE_READ_TOOL_NAMES:
        return None
    for k in _FILE_ARG_KEYS:
        if k in args and isinstance(args[k], str):
            return args[k]
    return None


def _extract_write_paths(tool_name: str, args: dict[str, Any]) -> list[str]:
    if tool_name not in _FILE_WRITE_TOOL_NAMES:
        return []
    paths = [args[k] for k in _FILE_ARG_KEYS if isinstance(args.get(k), str)]
    patch_text = args.get("patch")
    if isinstance(patch_text, str):
        paths.extend(_PATCH_FILE_RE.findall(patch_text))
    return paths


def _skill_ops(args: dict[str, Any]) -> list[dict[str, Any]]:
    """skill_manage 的操作列表。

    v0.22 只对外公布批量形式 ``{"operations": [{name, action, ...}, ...]}``;
    老的平铺形式 ``{action, name, content, ...}`` 仍然被接受 (老 transcript /
    审批后重放), 当成只有一个操作。
    """
    ops = args.get("operations")
    if isinstance(ops, list):
        default_name = args.get("name")
        out = []
        for op in ops:
            if isinstance(op, dict):
                if not op.get("name") and isinstance(default_name, str):
                    op = {**op, "name": default_name}
                out.append(op)
        return out
    if isinstance(args.get("action"), str):
        return [args]
    return []


def _skill_op_matches(rule: dict[str, Any], op: dict[str, Any]) -> bool:
    actions = rule.get("actions")
    if actions and op.get("action") not in actions:
        return False
    name_re = rule.get("name_regex")
    if name_re:
        name = op.get("name")
        # 允许 "productivity/catfish-email" 这种带分类的写法: 只看最后一段
        base = name.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(name, str) else ""
        if not re.search(name_re, base):
            return False
    patterns = rule.get("content_regex")
    if patterns:
        text = "\n".join(op[k] for k in _SKILL_TEXT_KEYS if isinstance(op.get(k), str))
        if not any(re.search(p, text) for p in patterns):
            return False
    return True


def _extract_candidate_hosts(tool_name: str, args: dict[str, Any]) -> list[str]:
    """Return a best-effort list of hostnames referenced in this call."""
    hosts: list[str] = []

    # Direct URL-taking tools
    for k in ("url", "endpoint", "address"):
        val = args.get(k)
        if isinstance(val, str):
            hosts.extend(_hosts_in_text(val))

    # Shell-executed commands -- scan embedded URLs
    if tool_name in _NETWORK_TOOL_NAMES:
        for k in _SHELL_ARG_KEYS:
            if k in args and isinstance(args[k], str):
                hosts.extend(_hosts_in_text(args[k]))
    return hosts


_URL_RE = re.compile(r"https?://([a-zA-Z0-9\-\.]+)", re.IGNORECASE)


def _hosts_in_text(text: str) -> list[str]:
    return [m.group(1).lower() for m in _URL_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Path glob matching with ~ expansion
# ---------------------------------------------------------------------------

def _path_matches(path_str: str, pattern: str) -> bool:
    """Does the (possibly ~-prefixed) path match the given ~ glob pattern?"""
    try:
        target = Path(os.path.expanduser(path_str)).resolve(strict=False)
    except Exception:
        target = Path(os.path.expanduser(path_str))

    pat_expanded = os.path.expanduser(pattern)

    # Fallback: use simple Path.match() + manual ** handling
    # Path.match doesn't understand ** cross-segment globs on older Pythons,
    # so we implement a conservative check.
    if "**" in pat_expanded:
        import fnmatch
        # Turn /a/** into /a/<anything>
        return fnmatch.fnmatch(str(target), pat_expanded.replace("**", "*"))
    return target.match(pat_expanded)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def match_rules(
    tool_name: str,
    args: dict[str, Any],
    rules_cfg: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Return the first triggered rule, or None."""
    for rule in rules_cfg.get("rules", []):
        if _rule_matches(rule, tool_name, args):
            return rule
    return None


def _rule_matches(rule: dict[str, Any], tool_name: str, args: dict[str, Any]) -> bool:
    kind = rule.get("kind")

    # ---- shell_command ----
    if kind == "shell_command":
        cmd = _extract_shell_command(tool_name, args)
        if not cmd:
            return False
        for pattern in rule.get("match", []):
            if pattern in cmd:
                return True
        for pattern in rule.get("match_regex", []):
            if re.search(pattern, cmd):
                return True
        return False

    # ---- file_write ----
    if kind == "file_write":
        for path in _extract_write_paths(tool_name, args):
            for glob_pat in rule.get("paths", []):
                if _path_matches(path, glob_pat):
                    return True
        return False

    # ---- skill_op (skill_manage 的单个操作) ----
    if kind == "skill_op":
        if tool_name != "skill_manage":
            return False
        return any(_skill_op_matches(rule, op) for op in _skill_ops(args))

    # ---- file_read ----
    if kind == "file_read":
        path = _extract_filepath(tool_name, args)
        if not path:
            return False
        for glob_pat in rule.get("paths", []):
            if _path_matches(path, glob_pat):
                return True
        return False

    # ---- network_request ----
    if kind == "network_request":
        hosts = _extract_candidate_hosts(tool_name, args)
        if not hosts:
            return False
        deny = {h.lower() for h in rule.get("deny_hosts", [])}
        for h in hosts:
            if h in deny:
                return True
            # Also match subdomain leaves (api.openai.com matches oai.com? no -- strict)
        return False

    # ---- tool_name (generic tool block) ----
    # 有 deny_args_pattern 时只在参数也命中时拦; 没有才是整个工具都拦。
    # (原来忽略了 deny_args_pattern, 一条只想拦删除的规则会把 skill_manage 全拦掉。)
    if kind == "tool_name":
        if tool_name not in rule.get("match", []):
            return False
        patterns = rule.get("deny_args_pattern")
        if not patterns:
            return True
        blob = json.dumps(args, ensure_ascii=False, sort_keys=True)
        return any(re.search(p, blob) for p in patterns)

    return False
