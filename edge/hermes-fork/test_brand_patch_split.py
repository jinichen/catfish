"""apply_brand_patch 拆分后的守卫 (8/15)。

# 病历

apply_brand_patch.py 959 行, 过了 CLAUDE.md §1 的 800 红线。拆成:

    brand_rules.py   432  四张规则表 + 公共坐标 (零逻辑)
    brand_hooks.py   213  git hook 装/卸
    apply_brand_patch.py 448  apply / revert / verify / apply_patches / main

# 这个文件主要钉一件事: 钩子必须指向 apply_brand_patch.py

拆的时候差点埋进去一个**只会在几周后现形**的 bug。

`install_hooks` 原来用 `Path(__file__).resolve()` 拿"要写进钩子的脚本路径"。
在 apply_brand_patch.py 里这是对的。搬到 brand_hooks.py 之后, `__file__` 就
变成 brand_hooks.py —— **而它没有 --apply**。

如果照搬会发生什么:

    1. `--install-hooks` 跑得好好的, 一句错都不报
    2. 写出去的钩子是 `python3 .../brand_hooks.py --apply`
    3. 下次 git pull, 钩子报 "unrecognized arguments: --apply"
    4. 但钩子里有 `set +e` 和 `exit 0` —— git 操作照样成功, 输出干净
    5. 品牌补丁从此再没重打过

真正的后果要等到某次 hermes 升级把界面上的字样冲回 "Hermes Agent" 才有人
发现, 而那时已经过去几周, 没人会联想到是拆文件那天。

这跟今天在 catfish-cli / identity users.py 上踩的是同一族: **护栏还在, 但
不再起作用, 而且不报错**。区别只是这次的沉默期以周计。

所以: brand_hooks.ENTRYPOINT 显式指名兄弟文件, _hook_body 里断言一遍,
这里再钉一遍。三道, 因为这条真出事的话没有任何别的信号。
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))

import brand_hooks  # noqa: E402
import brand_rules  # noqa: E402

ENTRY = _DIR / "apply_brand_patch.py"


# ── 钩子指向 ────────────────────────────────────────────────


def test_ENTRYPOINT_指向入口脚本而不是本模块():
    assert brand_hooks.ENTRYPOINT.name == "apply_brand_patch.py", (
        f"ENTRYPOINT 指向 {brand_hooks.ENTRYPOINT.name} —— 只有 apply_brand_patch.py "
        "认 --apply。指错了 git pull 不会报错, 品牌补丁会静默停摆。"
    )
    assert brand_hooks.ENTRYPOINT.exists(), f"入口脚本不存在: {brand_hooks.ENTRYPOINT}"


def test_钩子内容里写的是入口脚本():
    """★★★ 这条是这个文件的重点。"""
    body = brand_hooks._hook_body(ENTRY)
    assert "apply_brand_patch.py" in body
    assert "brand_hooks.py" not in body, (
        "钩子内容里出现了 brand_hooks.py —— 它没有 --apply, 装上去 git pull "
        "会静默失败 (钩子里有 set +e + exit 0)。"
    )
    assert "--apply" in body, "钩子应该调 --apply"


def test_hook_body_拒绝错误的脚本():
    """指错脚本要**当场炸**, 不能等到几周后员工发现界面变回英文。"""
    with pytest.raises(AssertionError, match="apply_brand_patch.py"):
        brand_hooks._hook_body(_DIR / "brand_hooks.py")
    with pytest.raises(AssertionError):
        brand_hooks._hook_body(_DIR / "根本不存在.py")


def test_入口脚本真的认_apply(tmp_path):
    """别只信文件名 —— 真跑一次看它认不认这个参数。

    用一个空的假 hermes 树, 走 dry-run 分支 (不加 --apply 就是 dry-run),
    只要不是 "unrecognized arguments" 就说明参数是通的。
    """
    fake = tmp_path / "hermes-agent"
    (fake / "hermes_cli").mkdir(parents=True)
    r = subprocess.run(
        [sys.executable, str(ENTRY), "--apply"],
        capture_output=True, text=True,
        env={"HERMES_DIR": str(fake), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert "unrecognized arguments" not in r.stderr, (
        f"入口脚本不认 --apply 了?\n{r.stderr[:400]}"
    )


def test_brand_hooks_不认_apply_所以不能当入口():
    """反向确认上面那条不是空谈: brand_hooks.py 直接跑确实是没用的。"""
    r = subprocess.run([sys.executable, str(_DIR / "brand_hooks.py"), "--apply"],
                       capture_output=True, text=True)
    assert r.returncode != 0 or not r.stdout.strip(), (
        "brand_hooks.py 现在能当入口跑了? 那 ENTRYPOINT 那套说明要更新。"
    )


# ── 规则表 ──────────────────────────────────────────────────


def test_规则表条数():
    """拆分时的条数。hermes 升级加规则是正常的 —— 加完顺手改这里的数字,
    这样"规则被误删"和"规则被有意增补"就区分得开。"""
    assert len(brand_rules.RULES) == 26, f"RULES 现在 {len(brand_rules.RULES)} 条 (拆分时 26)"
    assert len(brand_rules.REGEX_RULES) == 3
    assert len(brand_rules.VERIFY_MARKERS) == 4


def test_brand_rules_里没有逻辑():
    """它是数据表。哪天有人往里塞函数, 就该重新想想那个函数属于谁。"""
    tree = ast.parse((_DIR / "brand_rules.py").read_text(encoding="utf-8"))
    defs = [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert not defs, f"brand_rules.py 里出现了 {defs} —— 这个文件只放数据"


def test_PATCHES_DIR_还指着同一个目录():
    """它是 `Path(__file__).parent / "patches"`。brand_rules.py 跟
    apply_brand_patch.py 是同目录兄弟, 所以搬过去不改变指向 —— 但哪天有人把
    brand_rules.py 挪进子目录, patches/ 就找不到了, 而 apply_patches 找不到
    patch 目录时是**静默跳过**的。"""
    assert brand_rules.PATCHES_DIR == _DIR / "patches"
    assert brand_rules.PATCHES_DIR.exists(), "patches/ 目录不见了"


# ── 依赖方向 ────────────────────────────────────────────────


@pytest.mark.parametrize("fname", ["brand_rules.py", "brand_hooks.py"])
def test_子模块不许反向import入口(fname: str):
    """apply_brand_patch.py 是当脚本跑的 (git hook 里就是), 模块名是 __main__。
    子模块再 import 它会把同一份源码再执行一遍, 拿到第二个 module 对象。"""
    tree = ast.parse((_DIR / fname).read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            bad += [a.name for a in n.names if a.name == "apply_brand_patch"]
        elif isinstance(n, ast.ImportFrom) and n.module == "apply_brand_patch":
            bad.append(f"from apply_brand_patch import ...")
    assert not bad, f"{fname} 反向 import 了 apply_brand_patch: {bad}"


def test_brand_rules_只依赖标准库():
    tree = ast.parse((_DIR / "brand_rules.py").read_text(encoding="utf-8"))
    bad = [ast.unparse(n) for n in ast.walk(tree)
           if isinstance(n, (ast.Import, ast.ImportFrom))
           and ("brand_" in ast.unparse(n) or "apply_brand" in ast.unparse(n))]
    assert not bad, f"brand_rules 是最底层, 不该依赖兄弟: {bad}"


@pytest.mark.parametrize("fname", ["apply_brand_patch.py", "brand_rules.py", "brand_hooks.py"])
def test_都在红线以下(fname: str):
    n = len((_DIR / fname).read_text(encoding="utf-8").splitlines())
    assert n < 800, f"{fname} {n} 行, 过了 CLAUDE.md §1 的 800 红线"
