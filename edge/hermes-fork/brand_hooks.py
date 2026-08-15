"""往 hermes-agent/.git/hooks/ 装钩子, 让上游升级后自动重打品牌补丁
(BL-D14.5, 5/7)。

8/15 从 apply_brand_patch.py 搬出来。

# 为什么要有这套钩子

hermes 是 git clone 装的, 员工 / IT 一 `git pull` 就把我们替换过的字符串
全冲回英文。而这件事**没有任何提示** —— 界面上突然又叫 Hermes 了, 没人知道
是升级干的。

所以 post-merge / post-rewrite / post-checkout 三个钩子各挂一行, 每次
pull / rebase / checkout 后自动重跑 `--apply`。补丁是幂等的 (DONE/PATCH/MISS
三态), 重复跑无副作用。

# ⚠⚠ ENTRYPOINT 绝对不能写成 Path(__file__)

拆分前这段代码在 apply_brand_patch.py 里, 用的是 `Path(__file__).resolve()`
—— 那时候它指的就是入口脚本, 是对的。搬到本文件之后 `__file__` 变成
brand_hooks.py, **而本文件没有 --apply**。

如果照搬, 后果是:

    1. `--install-hooks` 跑得好好的, 一句错都不报
    2. 写出去的钩子内容是 `python3 .../brand_hooks.py --apply`
    3. 下次 git pull, 钩子跑起来报 "unrecognized arguments: --apply"
    4. 但钩子里有 `set +e` 和 `exit 0` —— **git 操作照样成功, 输出干净**
    5. 品牌补丁从此再没重打过。等到某次 hermes 升级把字样冲回英文,
       才有人发现, 而那时已经过去几周, 根本不会联想到是拆文件那天的事

所以这里显式指名兄弟文件, 并且在 _hook_body 里断言它存在。
test_brand_patch_split.py 里有守卫钉住"钩子内容必须指向 apply_brand_patch.py"。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from brand_rules import BACKUP_SUFFIX, HERMES_ROOT

#: 钩子要调的入口脚本。**是 apply_brand_patch.py, 不是本文件** —— 见上面的说明。
ENTRYPOINT = Path(__file__).resolve().parent / "apply_brand_patch.py"

# ============================================================
# install_hooks: 在 ~/.hermes/hermes-agent/.git/hooks/ 安装自动重 patch 钩子
# ============================================================
#
# 5/7 BL-D14.5 设计:
#   场景: 员工 / 同事 / cron 跑 `hermes update` 或 `cd ~/.hermes/hermes-agent && git pull`
#         上游 hermes 改了 banner.py / branding.tsx, catfish 品牌补丁被覆盖,
#         员工下次启动看到 "Hermes Agent" 大字, 跟产品故事不符.
#
#   解法: git 每次 merge / pull / rebase / checkout 后自动跑这个脚本 --apply,
#         把品牌补丁打回去. 因为 RULES 是幂等的 (已 patched 跳过), 重复运行无副作用.
#
#   钩子选 3 个:
#     post-merge    git pull (默认 merge 模式) / git merge 后跑
#     post-rewrite  git pull --rebase / git rebase 后跑
#     post-checkout 切分支 / git checkout -b 后跑 (catch worktree 操作)
#
#   钩子内容: 一行 exec — 调用本脚本 --apply, stderr 收掉, 失败不阻塞 git 操作.

HOOK_NAMES = ["post-merge", "post-rewrite", "post-checkout"]
HOOK_MARKER = "# managed by catfish/edge/hermes-fork/apply_brand_patch.py (BL-D14.5)"


def _hook_body(patch_script: Path) -> str:
    """生成 hook 脚本内容. patch_script 是 apply_brand_patch.py 的绝对路径.

    8/15 加的这条断言不是防御性编程, 是防**一种特定的静默失败**: 钩子里有
    `set +e` + `exit 0`, 指错脚本的话 git pull 什么都不会说, 品牌补丁悄悄
    停摆几周。宁可在装钩子的时候当场炸。
    """
    if patch_script.name != "apply_brand_patch.py":
        raise AssertionError(
            f"钩子要指向 apply_brand_patch.py, 拿到的却是 {patch_script.name} —— "
            "只有那个文件认 --apply。其他文件装上去, git pull 时会静默失败。"
        )
    if not patch_script.exists():
        raise AssertionError(f"入口脚本不存在: {patch_script}")
    return f"""#!/usr/bin/env bash
{HOOK_MARKER}
# 每次 git merge / pull / rebase / checkout 后自动重跑 catfish 品牌补丁,
# 让 hermes 升级不会再覆盖鲶鱼品牌. 失败不阻塞 git 操作 (品牌不是 git 的事).
set +e
PATCH_PY={shlex_quote(str(patch_script))}
if [ -f "$PATCH_PY" ]; then
    # 静默重跑 — 已 patched 的会被自动跳过 (幂等).
    # 只在真有改动 / 失败时打印, 平常 git pull 输出干净.
    OUT=$(python3 "$PATCH_PY" --apply 2>&1)
    EC=$?
    if [ $EC -ne 0 ] || echo "$OUT" | grep -qE '(MISS|ERROR|FAIL)'; then
        echo "🐟 catfish brand patch: 检测到 hermes 升级带来的新字符串"
        echo "$OUT" | grep -E '(PATCH|MISS|FAIL|ERROR)' | head -20
        echo "🐟 完整输出: python3 $PATCH_PY"
    fi
fi
exit 0
"""


def shlex_quote(s: str) -> str:
    """简化版 shlex.quote, 避免 import."""
    if not s or any(c in s for c in " \t\"'$`\\!"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s


def install_hooks(patch_script: Path | None = None) -> int:
    """在 hermes-agent/.git/hooks/ 装 post-merge/rewrite/checkout 钩子.

    - 已存在 catfish 钩子 → 覆盖 (确保 patch_script 路径是最新的)
    - 已存在非 catfish 钩子 (员工自己写的) → 备份成 <name>.before-catfish 再装
    - 没有 .git 目录 → 报错退出 (hermes 安装方式不一样, 钩子方案不适用)
    """
    git_dir = HERMES_ROOT / ".git"
    if not git_dir.exists():
        print(f"❌ {git_dir} 不存在 — hermes 不是 git clone 安装的, 钩子方案跳过")
        print("   建议: 改成手动跑 install.sh -y 兜底, 或者改 hermes 安装方式")
        return 1

    # git submodule / worktree 时 .git 是文件而不是目录, 内容是 "gitdir: ..."
    if git_dir.is_file():
        try:
            line = git_dir.read_text(encoding="utf-8").strip()
            if line.startswith("gitdir:"):
                actual = line.split(":", 1)[1].strip()
                git_dir = (HERMES_ROOT / actual).resolve()
        except Exception as e:
            print(f"❌ 解析 .git 文件失败: {e}")
            return 1

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    if patch_script is None:
        # ⚠ 这里**不能**用 Path(__file__) —— 本文件没有 --apply, 见模块 docstring。
        patch_script = ENTRYPOINT

    body = _hook_body(patch_script)

    installed = 0
    for name in HOOK_NAMES:
        hook = hooks_dir / name
        if hook.exists():
            existing = hook.read_text(encoding="utf-8", errors="replace")
            if HOOK_MARKER in existing:
                # 已经是 catfish hook, 检查 patch 路径有没有更新
                if str(patch_script) in existing:
                    print(f"  DONE  {name} 已装 (路径正确)")
                    continue
                # 路径变了 — 覆盖
                hook.write_text(body, encoding="utf-8")
                hook.chmod(0o755)
                print(f"  PATCH {name} 路径更新")
                installed += 1
                continue
            # 员工自己装的钩子 — 备份再 chained
            backup = hook.with_suffix(hook.suffix + BACKUP_SUFFIX)
            if not backup.exists():
                shutil.copy2(hook, backup)
            # 在原钩子基础上 append catfish 部分
            chained = existing.rstrip() + "\n\n" + body
            hook.write_text(chained, encoding="utf-8")
            hook.chmod(0o755)
            print(f"  PATCH {name} 已装 (chained 在原钩子后, 备份 .before-catfish)")
            installed += 1
        else:
            hook.write_text(body, encoding="utf-8")
            hook.chmod(0o755)
            print(f"  INSTALL {name}")
            installed += 1

    print()
    print(f"装了 {installed} 个 git hook 到 {hooks_dir}")
    print("以后 hermes 升级 (git pull / merge / rebase) 后会自动重跑品牌补丁.")
    print("验证: cd ~/.hermes/hermes-agent && git pull --quiet && python3 {} --verify".format(
        ENTRYPOINT.name,   # ⚠ 同上: 提示员工跑的是入口脚本, 不是本文件
    ))
    return 0


def uninstall_hooks() -> int:
    """卸载 catfish 钩子 (恢复 .before-catfish 备份, 或删除纯 catfish 的)."""
    git_dir = HERMES_ROOT / ".git"
    if git_dir.is_file():
        line = git_dir.read_text(encoding="utf-8").strip()
        if line.startswith("gitdir:"):
            git_dir = (HERMES_ROOT / line.split(":", 1)[1].strip()).resolve()
    hooks_dir = git_dir / "hooks"
    if not hooks_dir.exists():
        print("没装过钩子")
        return 0

    removed = 0
    for name in HOOK_NAMES:
        hook = hooks_dir / name
        if not hook.exists():
            continue
        content = hook.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER not in content:
            continue
        backup = hook.with_suffix(hook.suffix + BACKUP_SUFFIX)
        if backup.exists():
            shutil.copy2(backup, hook)
            backup.unlink()
            print(f"  RESTORE {name} (从 .before-catfish 还原)")
        else:
            hook.unlink()
            print(f"  REMOVE {name}")
        removed += 1
    print(f"卸了 {removed} 个钩子")
    return 0
