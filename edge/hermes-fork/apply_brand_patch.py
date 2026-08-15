#!/usr/bin/env python3
"""对 ~/.hermes/hermes-agent/ 源码做品牌替换 —— 把 Hermes/Nous Research 字样换成鲶鱼。

设计要点：
    1. 幂等：已替换过的字符串跳过，不重复 patch
    2. 备份：每个被改的文件第一次会备份到 <file>.before-catfish
    3. dry-run：默认只打印不动，加 --apply 才真正写
    4. revert：加 --revert 还原所有 .before-catfish 备份
    5. 精准替换：只改用户看得见的字符串字面量，不动代码逻辑 / 注释 / 测试

使用：
    python3 apply_brand_patch.py            # dry-run，看会改什么
    python3 apply_brand_patch.py --apply    # 真的改
    python3 apply_brand_patch.py --revert   # 还原
    python3 apply_brand_patch.py --verify   # 检查 branding 还在不在（CI / hook 用）
    python3 apply_brand_patch.py --install-hooks  # 装 git hooks 到 ~/.hermes/hermes-agent/.git/hooks/

未来 hermes 升级保护 (5/7 BL-D14.5):
    --install-hooks 会装 3 个 git hook (post-merge / post-checkout / post-rewrite),
    每次 git pull / merge / rebase 后自动重跑 --apply, 让升级不再撞品牌补丁.
    install.sh 默认会调 --install-hooks, 一次装好长期生效.

代码 patch 自动重打 (5/19 BL-HERMES-PATCH-AUTOMATION):
    patches/NNNN-描述.patch 存我们对 hermes 上游的非品牌代码改动 (CORS / 性能 / bugfix).
    --apply / --revert / --verify 会同时跑 RULES 字符串规则 + patches/ 下的 .patch.
    跟 RULES 互补 — RULES 改字符串字面量 (单行), patches 改代码块 (加常量 / 加 helper).
    apply 用 `patch -p1` (而不是 `git apply`) — fuzzy matching 抗上游小改动.
    幂等: 已 apply 的 patch (reverse dry-run 能过) 自动跳过.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── 规则表 + 公共坐标搬去 brand_rules.py (8/15) ──────────────────
#
# 那边 347 行全是数据, 一行逻辑没有。分出去是因为 hermes 每次升级都要去加
# 规则 (0.13 加了 5-10 条, 0.14 又一批), 加规则的人不该被迫翻过 600 行 patch
# 逻辑才找到表在哪。
from brand_rules import (  # noqa: F401
    BACKUP_SUFFIX,
    CATFISH_TIPS_BLOCK,
    HERMES_ROOT,
    NEW_BUILD_WELCOME_BANNER,
    PATCHES_DIR,
    REGEX_RULES,
    RULES,
    VERIFY_MARKERS,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _ensure_backup(path: Path) -> None:
    backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)


def _check_already_patched(content: str, new_str: str, old_str: str) -> bool:
    """如果 new_str 已出现且 old_str 已消失 → 已 patched。"""
    return new_str in content and old_str not in content


def _replace_function(content: str, func_name: str, new_code: str) -> tuple[str, bool]:
    """把顶层函数 `def func_name(...)` 到下一个顶层 def/class 之间的所有行替换成 new_code。

    返回 (new_content, changed)。
    """
    lines = content.split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"def {func_name}("):
            start = i
            break
    if start is None:
        return content, False
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("def ") or lines[i].startswith("class "):
            end = i
            break
    # 构造新内容，new_code 已含尾部 \n
    new_lines = lines[:start] + [new_code.rstrip("\n")] + lines[end:]
    new_content = "\n".join(new_lines)
    return new_content, new_content != content


def apply(dry_run: bool) -> int:
    changed_files: set[Path] = set()
    already_patched = 0
    skipped_missing = 0

    # ---------- 简单字符串规则 ----------
    for rel_path, old, new, desc in RULES:
        target = HERMES_ROOT / rel_path
        if not target.exists():
            print(f"  SKIP  {rel_path:50s}  (文件不存在)")
            skipped_missing += 1
            continue
        content = _read(target)

        if _check_already_patched(content, new, old):
            print(f"  DONE  {desc}")
            already_patched += 1
            continue

        if old not in content:
            print(f"  MISS  {desc}  -- 找不到原字符串，可能 hermes 升级了")
            continue

        new_content = content.replace(old, new)
        if new_content == content:
            print(f"  NOOP  {desc}")
            continue

        if dry_run:
            print(f"  WILL  {desc}")
        else:
            _ensure_backup(target)
            _write(target, new_content)
            print(f"  PATCH {desc}")

        changed_files.add(target)

    # ---------- Regex 规则（多行 block 替换）----------
    for rel_path, pattern, replacement, desc, detect in REGEX_RULES:
        target = HERMES_ROOT / rel_path
        if not target.exists():
            print(f"  SKIP  {rel_path:50s}  (文件不存在)")
            skipped_missing += 1
            continue
        content = _read(target)

        # 已 patched 检测：detect 在文件里，原 pattern 已被替换
        if detect in content and not re.search(pattern, content, re.DOTALL):
            print(f"  DONE  {desc}")
            already_patched += 1
            continue

        if not re.search(pattern, content, re.DOTALL):
            print(f"  MISS  {desc}  -- regex 匹配不到")
            continue

        new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        if new_content == content:
            print(f"  NOOP  {desc}")
            continue

        if dry_run:
            print(f"  WILL  {desc}  (regex)")
        else:
            _ensure_backup(target)
            _write(target, new_content)
            print(f"  PATCH {desc}  (regex)")

        changed_files.add(target)

    # ---------- 整函数替换（极简 build_welcome_banner）----------
    target = HERMES_ROOT / "hermes_cli/banner.py"
    if target.exists():
        content = _read(target)
        # 已 patched 检测：看是否有"极简启动 banner"字串
        if "鲶鱼极简启动 banner" in content:
            print("  DONE  banner.py: build_welcome_banner 已替换为极简版")
            already_patched += 1
        else:
            new_content, changed = _replace_function(
                content, "build_welcome_banner", NEW_BUILD_WELCOME_BANNER,
            )
            if not changed:
                print("  MISS  banner.py: 找不到 build_welcome_banner 函数")
            elif dry_run:
                print("  WILL  banner.py: 替换 build_welcome_banner 为极简版（整函数替换）")
                changed_files.add(target)
            else:
                _ensure_backup(target)
                _write(target, new_content)
                print("  PATCH banner.py: build_welcome_banner 替换为极简版")
                changed_files.add(target)

    print()
    print(f"汇总：{len(changed_files)} 个文件{'将' if dry_run else '已'}改；"
          f"{already_patched} 条规则已 patched；"
          f"{skipped_missing} 个文件跳过")
    return 0 if not skipped_missing else 1


def revert() -> int:
    restored = 0
    for rel_path, _old, _new, _desc in RULES:
        target = HERMES_ROOT / rel_path
        backup = target.with_suffix(target.suffix + BACKUP_SUFFIX)
        if backup.exists():
            shutil.copy2(backup, target)
            backup.unlink()
            print(f"  REVERT {rel_path}")
            restored += 1
    print()
    print(f"还原了 {restored} 个文件")
    return 0




def verify() -> int:
    """branding 完好性检查. 返回 0 = OK, 1 = 退化/缺失."""
    bad = 0
    for rel_path, musts, must_nots in VERIFY_MARKERS:
        target = HERMES_ROOT / rel_path
        if not target.exists():
            # 文件被 hermes 升级删了 — 不算 catfish 的错, warning 但不挂
            print(f"  WARN  {rel_path}: 文件不存在 (hermes 可能改结构了)")
            continue
        try:
            content = _read(target)
        except Exception as e:
            print(f"  WARN  {rel_path}: 读不出来 ({e})")
            continue
        for needle in musts:
            if needle not in content:
                print(f"  FAIL  {rel_path}: 期望含 {needle!r} 但没有 (品牌退化)")
                bad += 1
        for stink in must_nots:
            if stink in content:
                print(f"  FAIL  {rel_path}: 出现了 {stink!r} (hermes 原字符串回归)")
                bad += 1
    if bad == 0:
        print("  OK    catfish branding 完好 ({} 个文件检查通过)".format(len(VERIFY_MARKERS)))
        return 0
    print()
    print(f"❌ 发现 {bad} 处品牌退化 — 需要 `python3 apply_brand_patch.py --apply` 修复")
    return 1


# ============================================================
# apply_patches: 重打 patches/ 目录下的 .patch 文件
# ============================================================
#
# 5/19 BL-HERMES-PATCH-AUTOMATION 设计:
#   场景: 像 RULES 那样自动维护我们对 hermes 上游的非品牌代码改动 (比如 CORS 修复).
#         RULES 适合单行字符串字面量替换, 多行代码块 / 加常量 / 加 helper 函数
#         那种结构化改动用 git diff -> .patch 文件更好.
#
#   工具: 用 `patch -p1` 命令而不是 `git apply` — 前者 fuzzy matching 抗上游小改动,
#         上下文略偏一两行还能成功 (git apply 是严格的, 一格不对就拒).
#
#   幂等检测: `patch -R --dry-run` 等价于"能否反向 apply" -> "是否已经 apply 过".
#         apply 前先这么测一下, 已 apply 就跳过, 跟 RULES 的 _check_already_patched
#         同 pattern.
#
#   命名规范: patches/NNNN-描述.patch (4 位数字前缀方便排序, 描述用 kebab-case).

def apply_patches(action: str) -> int:
    """对 patches/ 目录下所有 .patch 文件做 action.

    action:
        "apply"   真的打 patch (幂等: 已 apply 的跳过)
        "revert"  反向 apply (幂等: 没 apply 的跳过)
        "verify"  检查每个 patch 是否还在 (apply 后状态), 缺的 fail
        "dry-run" 只测试能否 apply, 不真写文件

    返回失败的 patch 个数 (0 = OK).
    """
    if not PATCHES_DIR.exists():
        return 0

    patches = sorted(PATCHES_DIR.glob("*.patch"))
    if not patches:
        return 0

    print()
    print(f"=== Catfish code patches ({action}) ===")
    print(f"目录: {PATCHES_DIR}")
    print(f"共 {len(patches)} 个 patch")
    print()

    fails = 0
    for p in patches:
        # 1. 检查是否已 apply: reverse dry-run 能过 = patch 已在文件里
        # `stdin=DEVNULL` 防 patch `Reversed prompt 真` `等 tty hang` (6/4 实测)
        already_applied = subprocess.run(
            ["patch", "-p1", "-R", "--dry-run", "--batch", "--force", "-i", str(p)],
            cwd=HERMES_ROOT, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        ).returncode == 0

        if action == "apply":
            if already_applied:
                print(f"  DONE   {p.name} (已 apply)")
                continue
            r = subprocess.run(
                ["patch", "-p1", "--batch", "--force", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
            timeout=30,
            )
            if r.returncode == 0:
                print(f"  PATCH  {p.name}")
            else:
                # patch 工具失败 — 通常是上游改了文件让锚点对不上
                err = (r.stderr or r.stdout).strip().splitlines()
                tail = err[-3:] if err else ["(no stderr)"]
                print(f"  FAIL   {p.name}: {' | '.join(tail)}")
                fails += 1

        elif action == "revert":
            if not already_applied:
                print(f"  SKIP   {p.name} (没 apply, 不用 revert)")
                continue
            r = subprocess.run(
                ["patch", "-p1", "-R", "--batch", "--force", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
            timeout=30,
            )
            if r.returncode == 0:
                print(f"  REVERT {p.name}")
            else:
                err = (r.stderr or r.stdout).strip().splitlines()
                tail = err[-3:] if err else ["(no stderr)"]
                print(f"  FAIL   {p.name}: {' | '.join(tail)}")
                fails += 1

        elif action == "verify":
            # 验证 patch 还在 — 等价于 reverse dry-run 成功
            if already_applied:
                print(f"  OK     {p.name}")
            else:
                print(f"  MISS   {p.name} (没 apply, 需要 --apply)")
                fails += 1

        elif action == "dry-run":
            # 看会不会 apply (注意: 已 apply 的会 fail, 所以加 already_applied 短路)
            if already_applied:
                print(f"  DONE   {p.name} (已 apply, dry-run 跳过)")
                continue
            r = subprocess.run(
                ["patch", "-p1", "--dry-run", "--batch", "--force", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
            timeout=30,
            )
            if r.returncode == 0:
                print(f"  WOULD  {p.name} (apply 能成功)")
            else:
                err = (r.stderr or r.stdout).strip().splitlines()
                tail = err[-3:] if err else ["(no stderr)"]
                print(f"  FAIL   {p.name}: {' | '.join(tail)}")
                fails += 1

        else:
            print(f"  ERROR  unknown action {action!r}")
            fails += 1

    print()
    if fails == 0:
        print(f"✓ {len(patches)} 个 patch 全部 {action} 成功")
    else:
        print(f"❌ {fails}/{len(patches)} 个 patch {action} 失败")
    return fails


# ── git hooks 搬去 brand_hooks.py (8/15) ────────────────────────
#
# ⚠ 搬的时候踩到一个真陷阱: install_hooks 原来用 `Path(__file__)` 拿"要写进
#   钩子的脚本路径"。在这个文件里那是对的; 搬到 brand_hooks.py 就变成指向
#   brand_hooks.py 自己 —— 而它没有 --apply。
#
#   而钩子里有 `set +e` + `exit 0`, 所以 git pull 会**完全正常、输出干净**,
#   只是品牌补丁再没重打过。等 hermes 升级把字样冲回英文才有人发现, 那时
#   已经过去几周, 不会有人联想到是拆文件那天。
#
#   现在 brand_hooks.ENTRYPOINT 显式指名本文件, _hook_body 里还断言了一遍,
#   test_brand_patch_split.py 里有守卫。
from brand_hooks import (  # noqa: F401
    HOOK_MARKER,
    HOOK_NAMES,
    _hook_body,
    install_hooks,
    shlex_quote,
    uninstall_hooks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真的应用替换（默认 dry-run）")
    parser.add_argument("--revert", action="store_true", help="还原所有 .before-catfish 备份")
    parser.add_argument("--verify", action="store_true",
                        help="检查 branding 是否完好 (CI / hook 用, 退化 exit 1)")
    parser.add_argument("--install-hooks", action="store_true",
                        help="装 git hooks 到 ~/.hermes/hermes-agent/.git/hooks/ "
                             "(post-merge/rewrite/checkout, 自动重跑 patch)")
    parser.add_argument("--uninstall-hooks", action="store_true",
                        help="卸 catfish 钩子, 恢复原钩子 (如果之前 chain 过)")
    args = parser.parse_args()

    if not HERMES_ROOT.exists():
        print(f"错误：找不到 {HERMES_ROOT}")
        return 1

    if args.revert:
        rc = revert()
        # 同时反向重打代码 patch (CORS 等). 即使 RULES revert 失败也跑, 否则 hermes 半干净.
        rc_p = apply_patches("revert")
        return rc if rc != 0 else (0 if rc_p == 0 else 1)
    if args.verify:
        print(f"=== Catfish brand verify ===")
        print(f"目标目录：{HERMES_ROOT}")
        print()
        rc = verify()
        rc_p = apply_patches("verify")
        return rc if rc != 0 else (0 if rc_p == 0 else 1)
    if args.install_hooks:
        print(f"=== 装 catfish git hooks ===")
        print(f"目标目录：{HERMES_ROOT}")
        print()
        return install_hooks()
    if args.uninstall_hooks:
        print(f"=== 卸 catfish git hooks ===")
        return uninstall_hooks()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== Catfish brand patch ({mode}) ===")
    print(f"目标目录：{HERMES_ROOT}")
    print(f"备份后缀：{BACKUP_SUFFIX}")
    print()
    rc = apply(dry_run=not args.apply)
    # 跑完字符串规则再跑代码 patch. apply 模式真打, 否则 dry-run.
    rc_p = apply_patches("apply" if args.apply else "dry-run")
    return rc if rc != 0 else (0 if rc_p == 0 else 1)


if __name__ == "__main__":
    sys.exit(main())
