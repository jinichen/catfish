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

# HERMES_DIR env 优先 (B.3 测试 + admin 场景), 默认 ~/.hermes/hermes-agent
HERMES_ROOT = Path(os.environ.get("HERMES_DIR") or (Path.home() / ".hermes" / "hermes-agent"))
BACKUP_SUFFIX = ".before-catfish"

# .patch 文件目录 (5/19 BL-HERMES-PATCH-AUTOMATION)
# 跟 RULES 字符串替换并存 — RULES 改单行字符串字面量, PATCHES 改代码块
# (加常量 / 加 helper / 加分支等 git diff 能表达的多行 hunk).
# 命名规范: NNNN-描述.patch, 按字典序逐个 apply.
PATCHES_DIR = Path(__file__).resolve().parent / "patches"


# 规则：(相对 HERMES_ROOT 的路径, 原字符串, 新字符串, 说明)
# 只改**用户在 UI 里看得见的字符串字面量**，不碰代码语法、不碰测试
RULES: list[tuple[str, str, str, str]] = [
    # ---------- TS: ui-tui/src/theme.ts ----------
    (
        "ui-tui/src/theme.ts",
        "name: 'Hermes Agent',",
        "name: '鲶鱼',",
        "theme.ts: brand name",
    ),
    (
        "ui-tui/src/theme.ts",
        "icon: '⚕',",
        "icon: '🐟',",
        "theme.ts: brand icon",
    ),
    (
        "ui-tui/src/theme.ts",
        "goodbye: 'Goodbye! ⚕',",
        "goodbye: '再见 🐟',",
        "theme.ts: exit message",
    ),
    # ---------- TS: ui-tui/src/bootBanner.ts (5/5 删除) ----------
    # hermes 0.11+ 起 bootBanner.ts 文件被删, 字符串迁到 branding.tsx 下面.
    # 5/5 阶段 A.3 dry-run 在 0.12 上验证: branding.tsx 规则已覆盖该字符串.
    # 删了原 2 条 (避免 SKIP 警告), 内容靠 branding.tsx 那 2 条规则同步.
    # ---------- TS: ui-tui/src/components/branding.tsx ----------
    (
        "ui-tui/src/components/branding.tsx",
        "Nous Research · Messenger of the Digital Gods",
        "鲶鱼平台 · 员工的数字副手",
        "branding: tagline in main banner",
    ),
    (
        "ui-tui/src/components/branding.tsx",
        " · Nous Research",
        " · 鲶鱼平台",
        "branding: model row suffix",
    ),
    # 0.12 真机检查 (5/5 B.2 brand check 抓到漏点): 当终端列数 < LOGO_WIDTH 时
    # branding.tsx 的 Banner 组件不渲染 ASCII 大字, fallback 到一行 Text:
    #   {t.brand.icon} NOUS HERMES
    # 之前 bootBanner.ts 的"NOUS HERMES → CATFISH" 字符串以为已被 branding.tsx
    # 现有 2 条规则覆盖 (tagline + model row), 实际**没**覆盖这条 fallback. 必须独加.
    (
        "ui-tui/src/components/branding.tsx",
        "{t.brand.icon} NOUS HERMES",
        "{t.brand.icon} 鲶鱼",
        "branding: ASCII fallback when terminal too narrow (LOGO_WIDTH 不够)",
    ),
    # ---------- TS: ui-tui/src/components/appLayout.tsx ----------
    # ⚕ 在这里作为状态栏前缀，改成 🐟
    (
        "ui-tui/src/components/appLayout.tsx",
        "⚕ {ui.status}",
        "🐟 {ui.status}",
        "appLayout: status bar icon",
    ),
    # ---------- Python: hermes_cli/banner.py ----------
    (
        "hermes_cli/banner.py",
        "[dim {dim}]Nous Research[/]",
        "[dim {dim}]鲶鱼平台[/]",
        "banner.py: model row suffix",
    ),
    # ---------- Python: cli.py ----------
    (
        "cli.py",
        "- Nous Research",
        "- 鲶鱼平台",
        "cli.py: title row suffix",
    ),
    # ---------- Python: hermes_cli/skin_engine.py ----------
    # 5 处相同 "Welcome to Hermes Agent!..." 全部替换
    (
        "hermes_cli/skin_engine.py",
        "Welcome to Hermes Agent! Type your message or /help for commands.",
        "欢迎回来。输入消息或 /help 看命令。",
        "skin_engine: welcome message (5 处都会命中)",
    ),
    # ---------- Python: cli.py welcome fallback ----------
    (
        "cli.py",
        '"Welcome to Hermes Agent! Type your message or /help for commands."',
        '"欢迎回来。输入消息或 /help 看命令。"',
        "cli.py: welcome fallback",
    ),
    (
        "cli.py",
        'get_branding("welcome", "Welcome to Hermes Agent! Type your message or /help for commands.")',
        'get_branding("welcome", "欢迎回来。输入消息或 /help 看命令。")',
        "cli.py: welcome in get_branding",
    ),
    # ---------- banner.py 主标题（line 243）----------
    # 这条是启动 banner 顶部的 "Hermes Agent v0.10.0 (2026.4.16) · upstream xxxx"
    # 真源头就在这里，改了整个 banner 标题立即变鲶鱼
    (
        "hermes_cli/banner.py",
        'base = f"Hermes Agent v{VERSION} ({RELEASE_DATE})"',
        'base = f"鲶鱼 v{VERSION} ({RELEASE_DATE})"',
        "banner.py: 启动 banner 标题",
    ),
    # ---------- banner.py agent_name fallback (5/5 删除) ----------
    # hermes 0.11+ 起这条字面量没了 (skin fallback 路径换实现).
    # 5/5 阶段 A.3 dry-run: MISS 不致命 - agent_name 主路径还在 skin_engine.py 4 处全命中.
    # 删了原规则避免 MISS 警告.

    # ---------- 0.12 新增: cli.py default skin banner ----------
    # hermes 0.12 cli.py:1727-1728 把 default skin 的 banner line 写死了
    # ⚠️ 顺序: 长字面量先 replace (line1), 再 replace 短的 (tiny_line).
    # 否则 tiny_line 的 "⚕ NOUS HERMES" 会先把 line1 里的 "⚕ NOUS HERMES" 部分改了.
    (
        "cli.py",
        '"⚕ NOUS HERMES - AI Agent Framework"',
        '"🐟 鲶鱼 - 员工的数字副手"',
        "cli.py: default skin banner line1 (0.12 新加)",
    ),
    (
        "cli.py",
        '"⚕ NOUS HERMES"',
        '"🐟 鲶鱼"',
        "cli.py: default skin banner tiny_line (0.12 新加)",
    ),

    # ---------- 0.12 新增: cli.py goodbye 副本 ----------
    # cli.py:9343-9345 在 hermes_cli/skin_engine.py 之外又写了一份 goodbye fallback.
    # 用精确的赋值/调用左侧绑死, 防跟 skin_engine.py 的 '"goodbye": "Goodbye! ⚕"'
    # 字面量重叠 (那条是 'goodbye": ' 前缀, 这两条是 get_active_goodbye(...) 跟
    # goodbye = ...).
    (
        "cli.py",
        'get_active_goodbye("Goodbye! ⚕")',
        'get_active_goodbye("再见 🐟")',
        "cli.py: get_active_goodbye 的 default 入参 (0.12 新加)",
    ),
    (
        "cli.py",
        'goodbye = "Goodbye! ⚕"',
        'goodbye = "再见 🐟"',
        "cli.py: goodbye fallback 赋值 (0.12 新加)",
    ),

    # ---------- 0.12 新增: hermes_cli/main.py --version 输出 ----------
    # hermes 0.12 加 cmd_version 子命令在 main.py:5099 直接 print 版本字符串.
    # 跟 banner.py:327 那条 'f"Hermes Agent v{VERSION}..."' 用的不是同一个变量
    # (main.py 用 __version__ / __release_date__, banner.py 用 VERSION / RELEASE_DATE),
    # 所以是独立字面量, 必须单独加规则.
    (
        "hermes_cli/main.py",
        'print(f"Hermes Agent v{__version__} ({__release_date__})")',
        'print(f"鲶鱼 v{__version__} ({__release_date__})")',
        "main.py: --version 子命令输出 (0.12 新加)",
    ),

    # ---------- 0.12 新增: rl_cli.py 退出语 ----------
    # rl_cli 是 RL 训练用的入口, 普通员工不直接进, 但脱敏一致性还是要改.
    (
        "rl_cli.py",
        '"\\n👋 Goodbye!"',
        '"\\n👋 再见!"',
        "rl_cli.py: 正常退出语 (0.12 新加)",
    ),
    (
        "rl_cli.py",
        '"\\n\\n👋 Interrupted. Goodbye!"',
        '"\\n\\n👋 中断. 再见!"',
        "rl_cli.py: 中断退出语 (0.12 新加)",
    ),

    # ---------- skin_engine.py skin 品牌字符串（同一字符串多处一次性替换）----------
    # default / mono / slate / light 四个 skin 都有 `"agent_name": "Hermes Agent"`
    # content.replace() 会把所有 occurrences 都改掉
    (
        "hermes_cli/skin_engine.py",
        '"agent_name": "Hermes Agent"',
        '"agent_name": "鲶鱼"',
        "skin_engine: agent_name 全部改成鲶鱼（4~5 处）",
    ),
    # default / mono / slate / light 的 goodbye 都是 "Goodbye! ⚕"
    (
        "hermes_cli/skin_engine.py",
        '"goodbye": "Goodbye! ⚕"',
        '"goodbye": "再见 🐟"',
        "skin_engine: goodbye ⚕ 改鲶鱼（4 处）",
    ),
    # paperwhite skin 的 goodbye 用了 unicode escape
    (
        "hermes_cli/skin_engine.py",
        '"goodbye": "Goodbye! \\u2695"',
        '"goodbye": "再见 🐟"',
        "skin_engine: goodbye \\u2695 改鲶鱼",
    ),
    # (已回滚) 原本想改 default skin 的 prompt / dim 提高对比度，
    # 但 default skin 是为**深色终端**设计的。正确做法是让员工用 /skin daylight
    # 或 /skin warm-lightmode 切换到浅色终端专用 skin。
    # 所以保留 default skin 原色（#FFF8DC 象牙白 / #B8860B 暗金）。
    # docstring 里的示例字符串（影响很小，但保持一致性）
    (
        "hermes_cli/skin_engine.py",
        'print(skin.get_branding("agent_name"))  # "Hermes Agent"',
        'print(skin.get_branding("agent_name"))  # "鲶鱼"',
        "skin_engine: docstring 示例",
    ),
]


# ============================================================
# 整函数替换：极简 build_welcome_banner
# ============================================================
# 原函数 220 行，构造左右双列 banner 含完整 Tools/Skills 列表。
# 员工每次启动都被 40 行信息刷屏，空间浪费 + 颜色对比度差。
# 新函数 4 行 Panel：标题 / 模型+路径 / 总览数字 / session。

NEW_BUILD_WELCOME_BANNER = '''def build_welcome_banner(console, model: str, cwd: str,
                         tools=None,
                         enabled_toolsets=None,
                         session_id=None,
                         get_toolset_for_tool=None,
                         context_length: int = None):
    """鲶鱼极简启动 banner：4 行 Panel + 高对比度颜色。"""
    from pathlib import Path as _Path

    # --- 模型简名 ---
    model_short = model.split("/")[-1] if "/" in model else model
    if model_short.endswith(".gguf"):
        model_short = model_short[:-5]
    if len(model_short) > 40:
        model_short = model_short[:37] + "..."

    # --- 路径简化（把 $HOME 替换成 ~）---
    cwd_short = str(cwd) if cwd else ""
    home = str(_Path.home())
    if cwd_short.startswith(home):
        cwd_short = "~" + cwd_short[len(home):]
    if len(cwd_short) > 60:
        cwd_short = "..." + cwd_short[-57:]

    # --- context 显示 ---
    ctx = f" · {_format_context_length(context_length)} ctx" if context_length else ""

    # --- 总览：工具数 + MCP server 数 ---
    n_tools = len(tools or [])
    n_mcp = 0
    try:
        from tools.mcp_tool import get_registered_mcp_servers
        n_mcp = len(get_registered_mcp_servers() or [])
    except Exception:
        n_mcp = 0

    # --- 构造极简内容（3~4 行）---
    lines = [
        f"[bold cyan]{model_short}[/][dim]{ctx}[/]  [dim cyan]·[/]  [bold]鲶鱼平台[/]",
        f"[dim]{cwd_short}[/]",
        f"[dim]{n_tools} tools · {n_mcp} MCP server{'s' if n_mcp != 1 else ''} · /help 看全部命令[/]",
    ]
    if session_id:
        lines.append(f"[dim]Session: {session_id}[/]")

    content = "\\n".join(lines)
    title = format_banner_version_label()

    outer_panel = Panel(
        content,
        title=f"[bold cyan]{title}[/]",
        border_style="cyan",
        padding=(0, 2),
        expand=True,
    )

    console.print()
    console.print(outer_panel)


'''


# 鲶鱼 tips 替换块（替换 hermes_cli/tips.py 的整个 TIPS 列表）
CATFISH_TIPS_BLOCK = '''TIPS = [
    "鲶鱼是你的副手，不是工具 —— 说\\"帮我做 X\\"，不是\\"怎么做 X\\"",
    "catfish doctor 一键看平台健康：gateway / Chrome / 索引 / 身份",
    "找文件用自然语言：\\"帮我找上周那份合同\\"。catfish-search 比 find 快 100 倍，还能读 PDF/Word。",
    "context 到 70% 自动压缩，不用手动 /compress",
    "长上下文切 catfish-public-gemini-pro（2M tokens）",
    "浏览器任务直接说：\\"帮我去 Jira 看这 sprint 所有 close 的 ticket\\"",
    "飞书消息过滤：catfish-feishu 帮你看哪条领导在问你，生成草稿给你审核",
    "需要周报？鲶鱼代写，你审核后本人从飞书发 —— 领导不知道有 AI 帮忙",
    "你的数据归你：memory / 搜索索引 / 草稿全在 ~/.catfish/，不上传任何地方",
    "skill 可自生成 + 自测 + 自修复 + 自动 cronjob 注册（Self-Evolution）",
]'''


# 多行 ASCII block 的 regex 替换规则（一条规则干掉一整个变量赋值）
# 格式：(文件路径, pattern, replacement, 说明, detect_pattern)
#   detect_pattern 用于"已 patched" 检测：如果这个已经出现，跳过
REGEX_RULES: list[tuple[str, str, str, str, str]] = [
    # 删掉顶部那个 "HERMES-AGENT" 巨字 ASCII（6 行蓝字）
    (
        "hermes_cli/banner.py",
        r'HERMES_AGENT_LOGO = """.*?"""',
        'HERMES_AGENT_LOGO = ""',
        "banner.py: 清空 HERMES-AGENT 大字 LOGO",
        'HERMES_AGENT_LOGO = ""',
    ),
    # 删掉中间那个蛇杖 ASCII（15 行 braille）
    (
        "hermes_cli/banner.py",
        r'HERMES_CADUCEUS = """.*?"""',
        'HERMES_CADUCEUS = ""',
        "banner.py: 清空蛇杖 ASCII",
        'HERMES_CADUCEUS = ""',
    ),
    # tips.py TIPS 列表整块替换成鲶鱼 10 条
    (
        "hermes_cli/tips.py",
        r'TIPS = \[.*?\n\]',
        CATFISH_TIPS_BLOCK,
        "tips.py: 整个 TIPS 列表替换为鲶鱼 10 条",
        '鲶鱼是你的副手',  # detect marker
    ),
]


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


# ============================================================
# verify: 检查 branding 还在不在 (CI / hook 用)
# ============================================================
#
# 思路: 找几个**显眼到员工一眼能看出问题**的关键字符串. 任意一条还是英文 hermes
#      字面量, 就报错退出 1. hook 据此决定要不要重跑 --apply.
#
# 选关键字标准:
#   - 出现在启动 banner / 退出语 / 状态栏 — 员工每天看
#   - 改了之后字符串完全不一样 — 不会跟其他东西误命中
#   - 跨多个 hermes 版本都稳定存在 — 不会因为 0.13 把它删了报假错

VERIFY_MARKERS: list[tuple[str, list[str], list[str]]] = [
    # (rel_path, must_contain_any_of, must_NOT_contain_any_of)
    (
        "hermes_cli/banner.py",
        ["鲶鱼"],
        [
            'base = f"Hermes Agent v{VERSION}',  # banner 标题没改
            'HERMES_AGENT_LOGO = """',  # 大字 LOGO 没清
        ],
    ),
    (
        "hermes_cli/skin_engine.py",
        ["鲶鱼", "再见 🐟"],
        [
            '"agent_name": "Hermes Agent"',
            '"goodbye": "Goodbye! ⚕"',
        ],
    ),
    (
        "cli.py",
        ["鲶鱼", "再见 🐟"],
        [
            '"⚕ NOUS HERMES"',
            'goodbye = "Goodbye! ⚕"',
        ],
    ),
    (
        "ui-tui/src/components/branding.tsx",
        ["鲶鱼平台"],
        ["Nous Research · Messenger of the Digital Gods"],
    ),
]


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
        # 真**`stdin=DEVNULL`** 防 patch 真**`Reversed prompt 真`** 真**`等 tty hang`** (6/4 实测)
        already_applied = subprocess.run(
            ["patch", "-p1", "-R", "--dry-run", "-i", str(p)],
            cwd=HERMES_ROOT, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        ).returncode == 0

        if action == "apply":
            if already_applied:
                print(f"  DONE   {p.name} (已 apply)")
                continue
            r = subprocess.run(
                ["patch", "-p1", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
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
                ["patch", "-p1", "-R", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
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
                ["patch", "-p1", "--dry-run", "-i", str(p)],
                cwd=HERMES_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
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
    """生成 hook 脚本内容. patch_script 是 apply_brand_patch.py 的绝对路径."""
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
        patch_script = Path(__file__).resolve()

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
        Path(__file__).name,
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
