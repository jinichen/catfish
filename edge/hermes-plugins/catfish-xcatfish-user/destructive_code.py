"""execute_code 脚本里有没有「删除等严重破坏」的动作 (10/15 鸿波定: 点过始终允许就别再问, 删除类除外)。

为什么
------
员工原话: 「只要点过始终允许, 下一次就不要再审批了, 除非那种删除等严重破坏内容的指令;
一堆的指令给非程序员, 他也是不会去看的, 看也看不懂。」

所以 P59 让「始终允许」对 execute_code 真生效, 只留一道闸: 这里判出来有破坏性动作的脚本,
不管点没点过始终允许, 每次都问 (定时任务里没人可问, 直接不执行)。

原则跟 readonly_code 反过来 —— 那边是「认得出只读才放行」, 这边是「认得出破坏才拦」,
所以名单宁宽勿窄; 但不能宽到日常脚本 (写报表、改 HTML) 也被拦, 否则「始终允许」又形同虚设。

判什么
------
  · 删文件 / 删目录: os.remove / unlink / rmdir / removedirs, shutil.rmtree, Path.unlink / rmdir
  · 结束别的进程: os.kill / killpg
  · 动态执行 (eval / exec / compile / __import__): 看不出它要干什么, 当破坏处理
  · 字符串里的命令 / SQL: 每个字符串字面量 (列表形式的命令先拼成一行) 过 hermes 自己的
    危险命令表 (rm -rf / del /s / taskkill /f / format / DROP TABLE ...), 外加
    DELETE FROM (带 WHERE 也算删数据)、改 hermes 安全配置 / ssh 密钥 / .env
  · 代码解析不了: 当破坏处理 (拿不准就问)
不判: 写文件 / 覆盖文件 (生成报表、改 HTML 每天都在做)、联网、移动文件。
"""
from __future__ import annotations

import ast
import re
from typing import List

# os.xxx / shutil.xxx 这种「模块.函数」形式
_MODULE_CALLS = {
    ("os", "remove"): "删除文件", ("os", "unlink"): "删除文件",
    ("os", "rmdir"): "删除文件夹", ("os", "removedirs"): "删除文件夹",
    ("shutil", "rmtree"): "删除整个文件夹", ("os", "kill"): "结束其他程序",
    ("os", "killpg"): "结束其他程序",
}
# 任意对象上的方法名 —— 这几个名字只有删除一个意思 (list.remove 之类有歧义的不放进来)
_METHODS = {"unlink": "删除文件", "rmdir": "删除文件夹", "rmtree": "删除整个文件夹",
            "removedirs": "删除文件夹"}
# from os import remove 之后直接调 remove(...)
_BARE_NAMES = {"rmtree": "删除整个文件夹", "unlink": "删除文件", "rmdir": "删除文件夹",
               "removedirs": "删除文件夹"}
_DYNAMIC = {"eval", "exec", "compile", "__import__"}

_EXTRA_PATTERNS = [
    (re.compile(r"\bDELETE\s+FROM\b", re.I), "删除数据库里的数据"),
    (re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE)\b", re.I), "删除数据库的表"),
    (re.compile(r"\.hermes[/\\](config\.yaml|\.env)|command_allowlist", re.I), "改动小鲶的安全设置"),
    (re.compile(r"\.ssh[/\\]"), "动 SSH 密钥"),
    # 先把带删除的代码写进文件再跑 (写的时候它只是个字符串)
    (re.compile(r"\b(shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs|kill))\s*\(|\.(unlink|rmdir)\s*\("), "删除文件"),
]
# 字符串里的 shell 命令 —— 本地兜底表, 不靠 hermes (CI / hermes 改名时 detect_dangerous_command 拿不到,
# 安全判断不能整个押在别人的表上)。hermes 的表在时两份都查。
_SHELL_PATTERNS = [(re.compile(p, re.I), zh) for p, zh in (
    (r"(?:^|[\s;&|`(])rm\s+\S", "删除文件"),
    (r"(?:^|[\s;&|`(])(?:del|erase|rd|rmdir)\s+(?:/[a-z]\s+)*\S", "删除文件"),
    (r"\bremove-item\b", "删除文件"),
    (r"\bfind\b.*(?:-delete\b|-exec(?:dir)?\s+\S*rm\b)", "删除文件"),
    (r"\bgit\s+clean\s+-\S*f", "删除文件"),
    (r"\bgit\s+reset\s+--hard\b", "丢弃还没保存的改动"),
    (r"\b(?:taskkill|pkill|killall|stop-process)\b|\bkill\s+-\S+", "结束其他程序或服务"),
    (r"\b(?:mkfs|diskpart|format-volume|clear-disk)\b|\bformat\s+[a-z]:", "格式化 / 清空磁盘"),
    (r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b|\|\s*iex\b", "从网上下载并直接运行程序"),
)]

# 代码里点名的脚本文件 (exec(open('x.py').read()) / subprocess 跑 python x.py / skill 的脚本):
# 文件在就把它也查一遍 —— 跑 skill 脚本是常态, 不能一律当「看不懂」
_SCRIPT_EXT = re.compile(r"\.(py|sh|bat|cmd|ps1)$", re.I)
_SCRIPT_MAX_BYTES = 1_000_000

# hermes 危险命令描述 → 中文, 按顺序认。只认「删除等严重破坏」这几类; hermes 表里别的 (python -c、
# chmod +x、git push --force、docker 换守护进程 ...) 不算, 照「始终允许」放行。
# 按 hermes 描述原文写正则, test_p59 逐条核对 hermes 全表 —— 升级新加的条目会在那里报出来。
_HERMES_ZH = [(re.compile(p, re.I), zh) for p, zh in (
    (r"\b(sql drop|sql truncate)\b", "删除数据库的表"),
    (r"\bsql delete\b", "删除数据库里的数据"),
    (r"git reset --hard", "丢弃还没保存的改动"),
    (r"\b(wipe|format|disk copy|block device|diskpart)\b", "格式化 / 清空磁盘"),
    (r"\b(delete|deletes|remove-item|erase|rm)\b", "删除文件"),
    (r"\bkill|stop/restart|stop/delete|stop-service|lifecycle", "结束其他程序或服务"),
    (r"\buninstall\b", "卸载软件"),
    (r"remote (content|script)|iwr \| iex|invoke-expression", "从网上下载并直接运行程序"),
    (r"obfuscation|encoded command", "运行伪装过的命令"),
    (r"exfiltration|cloud metadata", "把密码 / 密钥发到外面"),
    (r"system config|system file|boot configuration|sudo|fork bomb", "改动系统"),
    (r"hermes config|hermes secrets|ssh|credential|project env", "改动安全设置或密钥"),
)]


def _hermes_detect():
    try:
        from tools.approval_detection import detect_dangerous_command  # type: ignore
        return detect_dangerous_command
    except Exception:  # noqa: BLE001 — 单测 / hermes 改名: 只用本地表
        return None


def _zh(description: str):
    for pat, zh in _HERMES_ZH:
        if pat.search(description or ""):
            return zh
    return None


def _call_target(func: ast.AST):
    if isinstance(func, ast.Attribute):
        base = func.value
        mod = base.id if isinstance(base, ast.Name) else None
        return mod, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _strings(tree: ast.AST) -> List[str]:
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, (ast.List, ast.Tuple)):
            parts = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(parts) >= 2:
                out.append(" ".join(parts))       # ['rm', '-rf', p] → "rm -rf"
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(v.value for v in node.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return out


def _is_read_of_literal_file(node: ast.AST) -> bool:
    """open('<字面路径>').read() —— exec 这种东西等于跑那个脚本, 脚本本身另查。"""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read" and isinstance(node.func.value, ast.Call)):
        return False
    inner = node.func.value
    mod, name = _call_target(inner.func)
    return name == "open" and bool(inner.args) and isinstance(inner.args[0], ast.Constant) \
        and isinstance(inner.args[0].value, str)


def _script_findings(path_str: str, depth: int) -> List[str]:
    import os
    path = os.path.expanduser(path_str.strip())
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _SCRIPT_MAX_BYTES:
            return []
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    if path.lower().endswith(".py"):
        return find_destructive(text, _depth=depth + 1)
    detect = _hermes_detect()
    out: List[str] = []
    for line in text.splitlines():
        for pat, z in _SHELL_PATTERNS:
            if pat.search(line) and z not in out:
                out.append(z)
        why = None
        if detect is not None:
            try:
                hit, _key, desc = detect(line)
            except Exception:  # noqa: BLE001
                hit, desc = False, ""
            why = _zh(desc) if hit else None
        if why and why not in out:
            out.append(why)
    return out


def find_destructive(code: str, _depth: int = 0) -> List[str]:
    """返回破坏性动作的中文说明 (去重有序); 空列表 = 没有。"""
    found: List[str] = []

    def add(why: str) -> None:
        if why not in found:
            found.append(why)

    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return ["代码看不懂 (解析失败)"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        mod, name = _call_target(node.func)
        if name is None:
            continue
        if (mod, name) in _MODULE_CALLS:
            add(_MODULE_CALLS[(mod, name)])
        elif isinstance(node.func, ast.Attribute) and name in _METHODS:
            add(_METHODS[name])
        elif mod is None and name in _BARE_NAMES:
            add(_BARE_NAMES[name])
        elif mod is None and name in _DYNAMIC:
            if not (name == "exec" and node.args and _is_read_of_literal_file(node.args[0])):
                add("动态执行看不出内容的代码")

    detect = _hermes_detect()
    for s in _strings(tree):
        if not s.strip():
            continue
        if detect is not None:
            try:
                hit, _key, desc = detect(s)
            except Exception:  # noqa: BLE001
                hit, desc = False, ""
            why = _zh(desc) if hit else None
            if why:
                add(why)
        for pat, why in _EXTRA_PATTERNS + _SHELL_PATTERNS:
            if pat.search(s):
                add(why)
        if _depth < 2 and _SCRIPT_EXT.search(s.strip()):
            for why in _script_findings(s, _depth):
                add(why)
    return found
