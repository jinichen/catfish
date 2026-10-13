"""execute_code 脚本静态分析: 是否只读 + 用大白话说它要干什么 (10/13 鸿波定: 只读代码免批)。

为什么
------
微信里每段 execute_code 都要员工点「同意」。模型查资料爱写小脚本 (读几个 .md、列个目录),
一个问题弹五六次审批, 员工看不懂代码只能闭眼点 —— 审批变成噪音, 真危险的那次反而没人看。

原则: **只认得出来的才放行, 拿不准一律交给人。**
  · 静态分析 (AST), 不执行代码。
  · 白名单: 模块 / os 函数 / 外部命令都是列出来的才算只读; 不认识的模块算"有副作用"。
  · 危险对象 (subprocess 跑命令的函数 / os 的写函数 / hermes_tools 的写工具 / open / eval ...)
    只要被**引用**而不是在已知调用位置被直接调用 (赋给别的名字、当参数传、getattr / sys.modules
    取), 判不可知。
  · 动态手段 (eval / exec / __import__ / 非字面量 getattr / 双下划线属性 / import *) 判不可知。
  · 外部命令只放行一批只看不改的程序, 逐个查会写文件 / 跑别的程序的参数; 不许重定向 / 串联 / 换行。

describe() 给审批提示用: 不展示代码, 告诉员工「要做什么」和「为什么要你同意」。
"""
from __future__ import annotations

import ast
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional, Set

# ── 模块 ─────────────────────────────────────────────────────────────
SAFE_MODULES = {
    "os", "sys", "glob", "fnmatch", "json", "re", "datetime", "time", "math",
    "statistics", "collections", "itertools", "functools", "pathlib", "csv", "textwrap",
    "string", "pprint", "hashlib", "base64", "difflib", "decimal", "fractions", "random",
    "uuid", "typing", "dataclasses", "enum", "operator", "heapq", "bisect", "calendar",
    "zoneinfo", "locale", "platform", "getpass", "unicodedata", "html", "shlex", "io",
    "zipfile", "plistlib", "configparser", "tomllib", "struct", "binascii", "codecs",
    "copy", "numbers", "array", "warnings", "traceback",
    "subprocess", "hermes_tools",      # 调用另有专门检查
    "openpyxl", "docx", "pptx", "pypdf", "PyPDF2", "pdfplumber", "pandas", "numpy",
    "yaml", "markdown", "bs4", "lxml", "chardet", "dateutil", "pytz", "PIL",
}
NETWORK_MODULES = {
    "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl",
    "smtplib", "ftplib", "imaplib", "poplib", "telnetlib", "paramiko", "websocket",
    "websockets", "xmlrpc",
}
SIDE_EFFECT_MODULES = {
    "shutil": "移动 / 复制 / 删除文件", "tempfile": "创建临时文件", "tarfile": "打包 / 解包文件",
    "ctypes": "调用系统底层接口", "multiprocessing": "启动子进程", "signal": "给进程发信号",
    "importlib": "动态加载代码", "pickle": "反序列化任意对象", "marshal": "反序列化代码",
    "sqlite3": "读写数据库", "webbrowser": "打开浏览器", "pyautogui": "控制鼠标键盘",
    "keyring": "读写钥匙串", "winreg": "读写注册表", "builtins": "动态执行代码",
    "gzip": "压缩 / 解压文件", "bz2": "压缩 / 解压文件", "lzma": "压缩 / 解压文件",
    "shelve": "读写数据库", "dbm": "读写数据库", "threading": "启动后台线程",
    "asyncio": "启动后台任务", "atexit": "注册退出时的动作", "runpy": "运行别的代码",
    "code": "运行别的代码", "pty": "启动终端", "resource": "改系统资源限制",
}
READONLY_HERMES_TOOLS = {"read_file", "search_files", "web_search", "web_extract"}
_HERMES_TOOL_WHY = {"write_file": "写文件", "patch": "改文件", "terminal": "运行终端命令"}

OS_SAFE_ATTRS = {
    "listdir", "scandir", "walk", "stat", "lstat", "getcwd", "getenv", "cpu_count",
    "getpid", "getppid", "uname", "access", "readlink", "getlogin", "fspath", "fsdecode",
    "fsencode", "get_terminal_size", "getuid", "getgid", "times", "chdir",
    "path", "environ", "sep", "linesep", "name", "curdir", "pardir", "devnull", "pathsep",
    "extsep", "altsep", "R_OK", "W_OK", "X_OK", "F_OK", "DirEntry", "stat_result",
}
SUBPROCESS_RUNNERS = {"run", "check_output", "call", "check_call", "Popen", "getoutput",
                      "getstatusoutput"}
SUBPROCESS_SAFE_ATTRS = {"PIPE", "STDOUT", "DEVNULL", "CalledProcessError", "TimeoutExpired",
                         "CompletedProcess", "SubprocessError"}
SYS_UNSAFE_ATTRS = {"modules", "meta_path", "path_hooks", "settrace", "setprofile", "_getframe",
                    "addaudithook", "exc_info", "breakpointhook"}
DYNAMIC_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars",
                 "setattr", "delattr", "breakpoint", "input", "memoryview"}
# 这些内置名字只许出现在调用位置 (open(...)), 被引用 (o = open) 就看不出后面怎么用
_CALL_ONLY_BUILTINS = DYNAMIC_CALLS | {"open", "getattr", "__builtins__"}
_SAFE_DUNDERS = {"__name__", "__file__", "__doc__", "__init__", "__len__", "__version__",
                 "__main__", "__enter__", "__exit__"}
WRITE_METHODS = {
    "write_text", "write_bytes", "unlink", "rmdir", "mkdir", "makedirs", "removedirs",
    "remove", "rename", "renames", "replace", "touch", "chmod", "lchmod", "chown", "lchown",
    "symlink_to", "hardlink_to", "link_to", "save", "savetxt", "savez", "tofile", "savefig",
    "to_csv", "to_excel", "to_json", "to_parquet", "to_sql", "to_pickle", "to_feather",
    "to_html", "to_markdown", "to_clipboard", "truncate", "ftruncate", "kill", "killpg",
    "system", "popen", "startfile", "spawnl", "spawnv", "spawnlp", "spawnvp", "execv",
    "execl", "execvp", "execlp", "execve", "fork", "forkpty", "putenv", "unsetenv",
    "symlink", "link", "utime", "chflags", "mkfifo", "mknod", "setxattr", "removexattr",
    "extractall", "extract", "write", "writelines", "writestr", "dump", "terminate",
}
_KILL = {"kill", "killpg", "terminate"}
_FILE_OPENERS = {"open", "io.open", "codecs.open"}       # mode 在第 2 个参数

# ── 外部命令 ───────────────────────────────────────────────────────────
READONLY_PROGRAMS = {
    "ps", "ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "du", "df",
    "stat", "file", "which", "whereis", "uname", "date", "whoami", "id", "hostname",
    "sw_vers", "system_profiler", "mdfind", "mdls", "lsof", "pgrep", "uptime", "echo",
    "printf", "sort", "uniq", "cut", "tr", "awk", "sed", "nl", "column", "diff", "cmp",
    "shasum", "md5", "md5sum", "sha256sum", "otool", "nm", "strings", "printenv",
    "pwd", "basename", "dirname", "realpath", "readlink", "tree", "vm_stat", "iostat",
    "netstat", "ifconfig", "scutil", "pmset", "defaults", "launchctl", "sysctl", "git",
    "find", "plutil", "codesign", "xattr", "top", "tasklist", "where", "systeminfo",
    "python3", "python", "node", "npm",
}
# 参数必须全是字面量才能判断的程序
_STRICT_PROGRAMS = {"find", "git", "sysctl", "defaults", "launchctl", "sed", "awk", "pmset",
                    "scutil", "xattr", "codesign", "plutil", "top", "sort", "uniq", "tree",
                    "rg", "file", "date", "hostname", "ifconfig", "python3", "python",
                    "node", "npm"}
_GIT_READ = {"status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame",
             "remote", "describe", "shortlog", "tag", "config", "grep", "cat-file", "reflog"}
_GIT_LIST_FLAGS = {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--list", "-l",
                   "--show-current", "--verbose", "--no-color", "--merged", "--no-merged"}
_FIND_WRITE = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
               "-fprintf", "-fls"}
_VERSION_ARGS = {"--version", "-V", "-v", "version"}
_SYSTEM_BIN_DIRS = {"/bin", "/usr/bin", "/sbin", "/usr/sbin", "/usr/local/bin", "/opt/homebrew/bin"}

_FILE_EXT = re.compile(r"\.(md|txt|csv|tsv|json|jsonl|ya?ml|xlsx?|xlsm|docx?|pptx?|pdf|html?|"
                       r"xml|log|ini|toml|py|js|ts|sh|plist|png|jpe?g|zip)$", re.I)


@dataclass
class CodeVerdict:
    read_only: bool
    effects: List[str] = field(default_factory=list)   # 中文, 去重有序: 为什么要审批
    summary: Optional[str] = None                      # 代码第一行注释: 它说自己要干什么
    files: List[str] = field(default_factory=list)     # 代码里出现的文件名 (给 describe 用)
    reads_dirs: bool = False
    runs: List[str] = field(default_factory=list)      # 跑的只读命令 (ps / ls ...)

    def add(self, why: str) -> None:
        if why not in self.effects:
            self.effects.append(why)
        self.read_only = False


# 不是说明的注释: 编码声明 / lint / 类型标注 (10/13 实测「要做的事: -*- coding: utf-8 -*-」)
_PRAGMA = re.compile(r"-\*-|^(coding|vim?|noqa|type:|pylint|fmt:|pragma|flake8|mypy)\b", re.I)


def _first_comment(code: str) -> Optional[str]:
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("#") and not s.startswith("#!"):
            text = s.lstrip("#").strip()
            if len(text) >= 2 and not _PRAGMA.search(text):
                return text[:80]
    return None


def _lit_str(node) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def check_command(argv: List[Optional[str]]) -> Optional[str]:
    """argv 里 None = 非字面量参数。返回 None 表示只读; 否则返回中文原因。"""
    if not argv or argv[0] is None:
        return "运行一个事先看不出是什么的系统命令"
    if any(a is not None and ("\n" in a or "\r" in a or "\x00" in a) for a in argv):
        return "运行带换行的命令"
    head, _, prog = argv[0].rpartition("/")
    # 带路径的命令只认系统目录; /tmp/ls、./ls 这种可能是放进去的同名程序
    if head and head not in _SYSTEM_BIN_DIRS:
        return f"运行 {argv[0]} (不在系统目录)"
    if prog not in READONLY_PROGRAMS:
        return f"运行系统命令 {prog}"
    args = argv[1:]
    if prog in _STRICT_PROGRAMS and any(a is None for a in args):
        return f"运行 {prog} (参数事先看不出来)"
    a = [x for x in args if x is not None]
    pos = [x for x in a if not x.startswith("-")]
    if prog == "find" and any(x in _FIND_WRITE for x in a):
        return "用 find 删除或改动文件"
    if prog == "sort" and any(x == "-o" or x.startswith(("--output", "-o")) for x in a):
        return "用 sort 写文件"
    if prog == "uniq" and len(pos) >= 2:
        return "用 uniq 写文件"
    if prog == "tree" and any(x == "-o" or x.startswith("-o") for x in a):
        return "用 tree 写文件"
    if prog == "rg" and any(x.startswith("--pre") for x in a):
        return "用 rg 跑别的程序"
    if prog == "file" and any(x.startswith("-C") or x == "--compile" for x in a):
        return "用 file 写文件"
    if prog == "date" and any(not x.startswith("+") for x in pos):
        return "改系统时间"
    if prog == "hostname" and pos:
        return "改主机名"
    if prog == "ifconfig" and len(pos) > 1:
        return "改网络接口设置"
    if prog == "git":
        sub = next((x for x in a if not x.startswith("-")), None)
        before = a[:a.index(sub)] if sub in a else a
        if sub not in _GIT_READ or before:
            return f"运行 git {sub or ''}".strip()
        rest = a[a.index(sub) + 1:]
        if any(x.startswith("--output") or x.startswith("--ext-diff") for x in rest):
            return f"用 git {sub} 写文件或跑别的程序"
        if sub in {"branch", "tag"} and any(x not in _GIT_LIST_FLAGS and not x.startswith(
                ("--format", "--sort", "--contains", "--points-at")) for x in rest):
            return f"运行 git {sub} (可能新建 / 删除)"
        if sub == "remote" and any(x not in {"-v", "--verbose"} for x in rest):
            return "运行 git remote (可能改远端设置)"
        if sub == "config" and not (rest and rest[0] in {"--get", "--get-all", "--list", "-l",
                                                          "--get-regexp"}):
            return "运行 git config (可能改设置)"
    if prog == "sed" and any(
            (x.startswith("-") and not x.startswith("--") and ("i" in x[1:] or "f" in x[1:]))
            or x.startswith(("--in-place", "--file"))
            or (not x.startswith("-") and re.search(r"(^|[;}\s])[wWe]\b|/[wWe]\b", x)) for x in a):
        return "用 sed 直接改文件"
    if prog == "awk" and any(x == "-f" or x.startswith("-f") or re.search(r"system\s*\(|>|\||getline", x)
                             for x in a):
        return "用 awk 写文件或跑命令"
    if prog == "sysctl" and any(x == "-w" or "=" in x for x in a):
        return "改系统参数"
    if prog == "defaults" and (not a or a[0] not in {"read", "domains", "read-type", "find"}):
        return "改系统偏好设置"
    if prog == "launchctl" and (not a or a[0] not in {"list", "print", "print-disabled", "version"}):
        return "启停后台服务"
    if prog == "pmset" and (not a or a[0] != "-g"):
        return "改电源设置"
    if prog == "scutil" and not (a and a[0] in {"--get", "--dns", "--proxy", "--nwi"}):
        return "改网络 / 主机名设置"
    if prog == "xattr" and any(x.startswith("-") and set(x[1:]) & set("wdcr") for x in a):
        return "改文件扩展属性"
    if prog == "codesign" and not (a and a[0] in {"-d", "-dv", "-dvv", "-dvvv", "--display",
                                                  "-v", "--verify"}):
        return "给程序签名"
    if prog == "plutil" and not (a and a[0] in {"-p", "-lint"}):
        return "改 plist 文件"
    if prog in {"python3", "python", "node"} and not (len(a) == 1 and a[0] in _VERSION_ARGS):
        return f"运行 {prog} (可能执行别的代码)"
    if prog == "npm" and not (a and a[0] in _VERSION_ARGS | {"ls", "list", "view"}):
        return "运行 npm (可能安装或构建)"
    if prog == "top" and not any(x.startswith("-l") for x in a):
        return "运行交互式 top (不会自己退出)"
    return None


def check_shell_string(cmd: str) -> Optional[str]:
    if re.search(r"[;&`<>\n\r\x00]|\$\(|\|\|", cmd):
        return "运行带重定向 / 串联的 shell 命令"
    for seg in cmd.split("|"):
        try:
            argv = shlex.split(seg)
        except ValueError:
            return "运行一个解析不了的 shell 命令"
        why = check_command(argv)
        if why:
            return why
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, v: CodeVerdict, call_funcs: Set[int]):
        self.v = v
        self.call_funcs = call_funcs          # 所有 Call.func 节点的 id: 区分"调用"和"引用"
        self.attr_bases: Set[int] = set()     # 作为 Attribute.value 出现的 Name 节点 id
        self.mod_alias = {}                   # 本地名 → 模块名 (import x as y)
        self.direct = {}                      # 本地名 → (模块, 属性) (from x import y as z)

    # 导入 -------------------------------------------------------------
    def _module(self, name: str):
        top = name.split(".")[0]
        if top in SIDE_EFFECT_MODULES:
            self.v.add(SIDE_EFFECT_MODULES[top])
        elif top in NETWORK_MODULES:
            self.v.add("联网收发数据")
        elif top not in SAFE_MODULES:
            self.v.add(f"用到不认识的模块 {top}")

    def visit_Import(self, node):
        for a in node.names:
            self._module(a.name)
            if a.asname:
                self.mod_alias[a.asname] = a.name
            else:
                top = a.name.split(".")[0]
                self.mod_alias[top] = top
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        if node.level:
            self.v.add("导入本地代码")
            return
        self._module(mod)
        for a in node.names:
            if a.name == "*":
                self.v.add("用 import * 导入 (看不出导入了什么)")
                continue
            self.direct[a.asname or a.name] = (mod, a.name)
            why = self._attr_risk(mod, a.name)
            if why:
                self.v.add(why)
        self.generic_visit(node)

    @staticmethod
    def _attr_risk(mod: str, attr: str) -> Optional[str]:
        """模块属性本身是否危险 (不管怎么用)。subprocess 的跑命令函数另算。"""
        if mod == "os" and attr not in OS_SAFE_ATTRS:
            return f"调用 os.{attr}"
        if mod == "sys" and attr in SYS_UNSAFE_ATTRS:
            return "动态执行代码"
        if mod == "hermes_tools" and attr not in READONLY_HERMES_TOOLS:
            return _HERMES_TOOL_WHY.get(attr, f"调用工具 {attr}")
        if mod == "subprocess" and attr not in SUBPROCESS_RUNNERS | SUBPROCESS_SAFE_ATTRS:
            return f"调用 subprocess.{attr}"
        if mod in {"io", "codecs"} and attr == "open":
            return None                       # 调用位置单独查 mode
        return None

    def _resolve(self, node):
        """Name / Attribute → (模块, 属性) 若它指向某模块的属性; 否则 None。"""
        if isinstance(node, ast.Name) and node.id in self.direct:
            return self.direct[node.id]
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.mod_alias:
                return self.mod_alias[node.value.id], node.attr
            if _dotted(node.value) == "os.path":
                return "os.path", node.attr
        return None

    # 引用 (不管是不是调用) ---------------------------------------------
    def visit_Name(self, node):
        if node.id in _CALL_ONLY_BUILTINS and id(node) not in self.call_funcs \
                and node.id not in self.direct and node.id not in self.mod_alias:
            self.v.add("动态执行代码")
        if self.mod_alias.get(node.id) == "subprocess" and id(node) not in self.attr_bases:
            self.v.add("间接调用 subprocess (看不出跑什么命令)")
        target = self.direct.get(node.id)
        if target and id(node) not in self.call_funcs and (
                (target[0] == "subprocess" and target[1] in SUBPROCESS_RUNNERS)
                or target in {("io", "open"), ("codecs", "open")}):
            self.v.add("间接调用 (看不出会做什么)")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name):
            self.attr_bases.add(id(node.value))
        if node.attr.startswith("__") and node.attr not in _SAFE_DUNDERS:
            self.v.add("动态执行代码")
        r = self._resolve(node)
        if r:
            mod, attr = r
            why = self._attr_risk(mod, attr)
            if why:
                self.v.add(why)
            elif id(node) not in self.call_funcs and (
                    (mod == "subprocess" and attr in SUBPROCESS_RUNNERS) or (mod, attr) in
                    {("io", "open"), ("codecs", "open")}):
                self.v.add("间接调用 (看不出会做什么)")
        self.generic_visit(node)

    # 调用 -------------------------------------------------------------
    def visit_Call(self, node):
        func = node.func
        name = _dotted(func)
        tail = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
        r = self._resolve(func)
        if name in DYNAMIC_CALLS and name not in self.direct:
            self.v.add("动态执行代码")
        elif name == "getattr":
            self._check_getattr(node)
        elif r and r[0] == "subprocess" and r[1] in SUBPROCESS_RUNNERS:
            self._check_subprocess(node, r[1])
        elif name == "open" or r in {("io", "open"), ("codecs", "open")}:
            self._check_mode(node.args[1] if len(node.args) >= 2 else None, node)
            self._note_file(node.args[0] if node.args else None)
        elif isinstance(func, ast.Attribute) and tail == "open" and not r:
            self._check_mode(node.args[0] if node.args else None, node)       # Path(...).open("w")
        elif tail == "ZipFile":
            self._check_mode(node.args[1] if len(node.args) >= 2 else None, node)
        elif r and r[0] in {"os", "os.path", "glob"} and r[1] in {"listdir", "scandir", "walk", "glob", "iglob"}:
            self.v.reads_dirs = True
        elif tail in WRITE_METHODS and not (r and r[0] == "os.path"):
            if tail in {"write", "writelines", "dump"} and _dotted(getattr(func, "value", None)) in {"sys.stdout", "sys.stderr"}:
                pass
            else:
                self.v.add("结束进程" if tail in _KILL else "写入 / 删除 / 改动文件")
        self.generic_visit(node)

    def _note_file(self, node):
        s = _lit_str(node) if node is not None else None
        if s:
            self.v.files.append(s)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and _FILE_EXT.search(node.value.strip()) and len(node.value) < 300:
            self.v.files.append(node.value.strip())

    def _check_getattr(self, node):
        attr = _lit_str(node.args[1]) if len(node.args) >= 2 else None
        if attr is None or attr in WRITE_METHODS or attr in DYNAMIC_CALLS or attr.startswith("__") \
                or attr in SUBPROCESS_RUNNERS or attr in SYS_UNSAFE_ATTRS or attr == "open":
            self.v.add("动态执行代码")
            return
        target = node.args[0] if node.args else None
        mod = self.mod_alias.get(target.id) if isinstance(target, ast.Name) else None
        if mod and self._attr_risk(mod, attr):
            self.v.add("动态执行代码")

    def _check_mode(self, mode, node):
        for kw in node.keywords:
            if kw.arg == "mode":
                mode = kw.value
        if mode is None:
            return
        m = _lit_str(mode)
        if m is None:
            self.v.add("打开文件 (读还是写看不出来)")
        elif set(m) & set("wax+"):
            self.v.add("写入 / 改动文件")

    def _check_subprocess(self, node, func):
        if not node.args:
            self.v.add("运行一个看不出是什么的系统命令")
            return
        cmd = node.args[0]
        shell = func in {"getoutput", "getstatusoutput"} or any(
            kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
            for kw in node.keywords)
        for kw in node.keywords:
            if kw.arg is None:
                self.v.add("运行一个看不出是什么的系统命令")       # **kwargs 展开
                return
            if kw.arg in {"executable", "preexec_fn", "stdin", "env", "cwd", "pass_fds"}:
                self.v.add("运行系统命令 (带特殊设置)")
                return
            if kw.arg in {"stdout", "stderr"} and not (
                    _dotted(kw.value).rsplit(".", 1)[-1] in {"PIPE", "DEVNULL", "STDOUT"}
                    or (isinstance(kw.value, ast.Constant) and kw.value.value is None)):
                self.v.add("运行系统命令并把输出写到别处")
                return
        if isinstance(cmd, (ast.List, ast.Tuple)):
            argv = [_lit_str(e) for e in cmd.elts]
            if shell:
                why = check_shell_string(" ".join(argv)) if all(x is not None for x in argv) \
                    else "运行一个事先看不出是什么的系统命令"
            else:
                why = check_command(argv)
            prog = argv[0] if argv and argv[0] else None
        elif _lit_str(cmd) is not None:
            s = _lit_str(cmd)
            try:
                why = check_shell_string(s) if shell else check_command(shlex.split(s) or [None])
            except ValueError:
                why = "运行一个解析不了的命令"
            prog = s.split()[0] if s.split() else None
        else:
            why, prog = "运行一个事先看不出是什么的系统命令", None
        if why:
            self.v.add(why)
        elif prog:
            self.v.runs.append(prog.rsplit("/", 1)[-1])


def analyze(code: str) -> CodeVerdict:
    v = CodeVerdict(read_only=True, summary=_first_comment(code or ""))
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        v.add("代码解析不了")
        return v
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    _Visitor(v, call_funcs).visit(tree)
    return v


_RUN_NAMES = {"ps": "查看正在运行的程序", "pgrep": "查看正在运行的程序", "lsof": "查看打开的文件",
              "ls": "列出文件", "find": "查找文件", "mdfind": "搜索文件", "grep": "在文件里搜文字",
              "rg": "在文件里搜文字", "git": "查看代码仓库记录", "df": "查看磁盘空间",
              "du": "查看文件夹大小", "system_profiler": "查看电脑配置", "sw_vers": "查看系统版本"}


def describe(v: CodeVerdict) -> str:
    """一句大白话: 它要干什么。优先用代码自带的中文注释, 否则按代码里看到的东西拼。"""
    if v.summary:
        return v.summary
    parts = []
    names = []
    for f in v.files:
        b = os.path.basename(f.rstrip("/")) or f
        if b not in names:
            names.append(b)
    if names:
        shown = "、".join(names[:3])
        verb = "读取" if v.read_only else "处理"
        parts.append(f"{verb} {shown}" + (f" 等 {len(names)} 个文件" if len(names) > 3 else ""))
    elif v.reads_dirs:
        parts.append("查看文件夹里有哪些文件")
    for r in v.runs:
        label = _RUN_NAMES.get(r, f"运行 {r} 查看信息")
        if label not in parts:
            parts.append(label)
    return "、".join(parts) if parts else "处理数据 (代码里没写说明)"
