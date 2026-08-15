"""catfish-cli 拆分后的分层与 patch 目标守卫 (8/15)。

# 病历

catfish.py 1875 行, 过了 CLAUDE.md §1 的 800 红线。8/15 拆成 7 个文件:

    catfish_config.py    常量 / 路径 / logger   ← 最底层, 只依赖标准库
    catfish_token.py     TokenStore / 存盘 / JWT / refresh
    catfish_oauth.py     浏览器 OAuth flow
    catfish_hermes.py    往 hermes 同步凭据
    catfish_proxy.py     死代理检测
    catfish_privacy.py   员工隐私自查
    catfish.py           命令层 + argparse + re-export

拆的过程中真实踩到、且**将来一定会有人再踩一次**的坑有两个, 这个文件各钉一条。

## 坑 1: patch 打在 re-export 的绑定上, 等于没打

`from X import name` 建的是**新绑定**, 不是别名。函数体里查自由变量, 查的是
**定义它的那个模块**的 globals。所以:

    monkeypatch.setattr(catfish, "_check_proxy_alive", fake)   # 打在 catfish 上
    catfish_proxy._handle_proxy_cleanup(...)                   # 它查 catfish_proxy 的

patch 打空了。而且**不报错** —— 测试要么假绿, 要么去干真事:

  · `_check_proxy_alive` 打空 → 真去连 127.0.0.1:7890
  · `_restart_hermes_with_clean_env` 打空 → 真跑 `hermes gateway restart`
  · `_patch_hermes_config` 打空 → 真写开发机的 `~/.hermes/config.yaml`
  · `_fetch_edge_tool_list` 打空 → 真发网络请求 (拆的当天这条让测试从 0.4s
    变成 8.7s, 那是唯一的外部症状)

拆的时候 hermes 那两条**直接红了**, 代理那三条也红了 —— 算运气好。但
`_patch_hermes_config` 那条是**绿着的**: 调用链被 try/except 包住, 真去写
config.yaml 也不抛。要不是顺手全查了一遍, 它会一直绿到某天有人发现自己的
hermes 配置被单测改了。

## 坑 2: 子模块反过来 import catfish

catfish.py 是**当脚本直接跑**的 (`python3 catfish.py login`, launchd 里那条
定时任务也是)。这时它的模块名是 `__main__`。子模块里再写 `import catfish`,
Python 会把**同一份源码再执行一遍**, 得到第二个 module 对象 —— 两份常量、
两份 logger、两份一切, 而且各自自洽。这种病测试基本看不出来 (8/15 在
catfish-memory 上刚见过同型的)。

所以依赖方向必须是单向的: 子模块只往下依赖 catfish_config / catfish_token,
谁都不 import catfish。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent

#: 谁允许 import 谁。空集合 = 只能依赖标准库。
#: 改这张表之前先想清楚: 加一条边就可能加一个环。
ALLOWED_DEPS = {
    "catfish_config.py": set(),
    "catfish_proxy.py": set(),
    "catfish_token.py": {"catfish_config"},
    "catfish_oauth.py": {"catfish_config", "catfish_token"},
    "catfish_hermes.py": {"catfish_config", "catfish_token"},
    "catfish_privacy.py": {"catfish_config", "catfish_token"},
}


def _submodules() -> list[Path]:
    """本次拆出来的兄弟模块。用 glob 推, 不写死名单 —— 写死的话新加一个模块
    就悄悄逃过了所有守卫 (今天在 catfish-memory 的 no-double-load 守卫上
    栽过一次, 第一版硬编码了 6 个名字)。"""
    return sorted(p for p in _DIR.glob("catfish_*.py") if not p.name.startswith("test_"))


def test_每个兄弟模块都在依赖表里():
    """新加模块必须显式登记, 不能默默绕过下面两条。"""
    found = {p.name for p in _submodules()}
    assert found == set(ALLOWED_DEPS), (
        f"依赖表和实际文件对不上。\n  只在磁盘上: {sorted(found - set(ALLOWED_DEPS))}"
        f"\n  只在表里:   {sorted(set(ALLOWED_DEPS) - found)}\n"
        "新拆出模块请在 ALLOWED_DEPS 里加一行, 顺便想一下它该依赖谁。"
    )


@pytest.mark.parametrize("path", _submodules(), ids=lambda p: p.name)
def test_子模块不许反向import主文件(path: Path):
    """坑 2。catfish.py 直接跑时叫 __main__, 再 import catfish 会拿到第二个
    module 对象 —— 两份全局各自自洽, 测试看不出来。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            bad += [a.name for a in n.names if a.name == "catfish"]
        elif isinstance(n, ast.ImportFrom) and n.module == "catfish":
            bad.append(f"from catfish import {', '.join(a.name for a in n.names)}")
    assert not bad, (
        f"{path.name} 反向 import 了 catfish: {bad}\n"
        "依赖方向必须单向往下。要用的东西请沉到 catfish_config / catfish_token。"
    )


@pytest.mark.parametrize("path", _submodules(), ids=lambda p: p.name)
def test_子模块只依赖表里允许的兄弟(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            used |= {a.name for a in n.names if a.name.startswith("catfish")}
        elif isinstance(n, ast.ImportFrom) and (n.module or "").startswith("catfish"):
            used.add(n.module)
    extra = used - ALLOWED_DEPS[path.name]
    assert not extra, (
        f"{path.name} 依赖了表外的兄弟: {sorted(extra)}\n"
        f"表里允许的是 {sorted(ALLOWED_DEPS[path.name]) or '(只标准库)'}。\n"
        "确实需要的话改 ALLOWED_DEPS, 但先确认不会成环。"
    )


# ── 坑 1: patch 目标 ────────────────────────────────────────


def _all_modules() -> list[Path]:
    return [_DIR / "catfish.py"] + _submodules()


def _defines(name: str) -> set[str]:
    """哪些文件**定义**了这个顶层名字 (不算 re-export)。"""
    out = set()
    for p in _all_modules():
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if getattr(n, "name", None) == name:
                out.add(p.stem)
            elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets
            ):
                out.add(p.stem)
    return out


def _resolvers(name: str) -> set[str]:
    """哪些模块里**有函数把这个名字当自由变量用**。

    这才是"打这个模块的这个名字有没有用"的判据。

    ⚠ 第一版判据写的是"必须打在定义它的那个模块上", 结果把两条**正确**的
      patch 判成了错 —— `_do_refresh` 定义在 catfish_token.py, 但调用它的
      `cmd_token` / `cmd_refresh` 还在 catfish.py 里, 所以那两个函数查的是
      catfish.py 的 globals, `monkeypatch.setattr(catfish, "_do_refresh", ...)`
      恰恰是对的。

      判据比真事宽了。真正决定 patch 有没有效的是**调用者**在哪, 不是定义在哪。
      按第一版的说法去改, 反而会把两条好测试改坏。这条注释留着, 因为下一个
      想收紧这条守卫的人很可能会再想一遍同样的错。
    """
    out = set()
    for p in _all_modules():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        top = {getattr(n, "name", None) for n in tree.body}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == name and node.name in top:
                    out.add(p.stem)
                    break
    return out


def _setattr_targets() -> list[tuple[int, str, str]]:
    """test_catfish.py 里所有 monkeypatch.setattr(<模块>, "<名字>", ...)。"""
    src = (_DIR / "test_catfish.py").read_text(encoding="utf-8")
    out = []
    for n in ast.walk(ast.parse(src)):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "setattr" and len(n.args) >= 2
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)):
            out.append((n.lineno, ast.unparse(n.args[0]), n.args[1].value))
    return out


def test_patch_的模块里必须真有人用这个名字():
    """★★★ 这条是这个文件的重点。

    打在 re-export 的绑定上不报错, 只是**悄悄失效**。后果不是红, 是测试跑去
    干真事 (连真代理 / 写真 ~/.hermes / 发真网络请求)。

    判据: `setattr(mod, name)` 成立, 当且仅当 **mod 里至少有一个函数把 name
    当自由变量用** —— 那样改 mod 的这个绑定才可能影响到什么。反过来, 如果
    mod 里没人用它, 这次 patch 就一定是打空的。
    """
    bad = []
    for lineno, mod, name in _setattr_targets():
        if not _defines(name):
            continue                       # 打的是标准库 / 别的东西, 不管
        who = _resolvers(name)
        if mod not in who:
            bad.append(
                f"  test_catfish.py:{lineno}  patch 了 {mod}.{name}, 但 {mod} 里"
                f"没有任何函数会去查这个名字。真正会查它的是: {sorted(who) or '(没有)'}"
            )
    assert not bad, (
        "monkeypatch 打在 re-export 的绑定上, 等于没打:\n" + "\n".join(bad) + "\n\n"
        "函数体查自由变量查的是**定义它的那个函数所在模块**的 globals。打错地方"
        "不会报错, 只会让被测代码去连真代理 / 真写 ~/.hermes / 真发网络请求。"
    )


def test_测试文件里不许再出现代理组和hermes组的catfish前缀():
    """比上一条宽一点的网: 连**读**也不许走 catfish 前缀。

    上一条只管 setattr。但 `catfish._handle_proxy_cleanup(...)` 这种调用虽然
    功能上等价 (re-export 指向同一个函数对象), 留着会让下一个人以为
    "打 catfish 是对的"。既然这一组已经整体搬家, 引用就一致指到新家。
    """
    src = (_DIR / "test_catfish.py").read_text(encoding="utf-8")
    moved = {}
    for p in _submodules():
        if p.name in ("catfish_config.py", "catfish_token.py"):
            continue          # 这两个是基础层, catfish.TokenStore 这种老写法保留
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if getattr(n, "name", None):
                moved[n.name] = p.stem
    bad = []
    for i, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue          # 注释里提旧写法是在解释为什么不能这么写
        for name, home in moved.items():
            if re.search(rf"\bcatfish\.{re.escape(name)}\b", line):
                bad.append(f"  :{i}  catfish.{name} → 应该写 {home}.{name}")
    assert not bad, "\n".join(bad)


def test_主文件把搬走的东西都re_export了():
    """老 caller 不能破 —— docs/ 和别的脚本里写的是 `catfish.xxx`。"""
    import sys
    sys.path.insert(0, str(_DIR))
    import catfish

    missing = []
    for p in _submodules():
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            nm = getattr(n, "name", None)
            if nm and not hasattr(catfish, nm):
                missing.append(f"{p.name}::{nm}")
    assert not missing, (
        f"这些名字搬走之后没在 catfish.py re-export: {missing}\n"
        "CLAUDE.md §3 的拆分协议要求保 import 兼容。"
    )


def test_主文件回到红线以下():
    """拆完的目的。回归了就该有人知道。"""
    n = len((_DIR / "catfish.py").read_text(encoding="utf-8").splitlines())
    assert n < 800, f"catfish.py 又涨到 {n} 行, 过了 CLAUDE.md §1 的 800 红线"


@pytest.mark.parametrize("path", _submodules(), ids=lambda p: p.name)
def test_每个子模块也在红线以下(path: Path):
    n = len(path.read_text(encoding="utf-8").splitlines())
    assert n < 800, f"{path.name} {n} 行, 过了 800 红线"
