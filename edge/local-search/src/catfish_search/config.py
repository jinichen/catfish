"""加载和生成 search-scope.yaml 配置。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger("catfish.search.config")

CATFISH_HOME = Path.home() / ".catfish"
CONFIG_FILE = CATFISH_HOME / "search-scope.yaml"
DB_FILE = CATFISH_HOME / "search.db"

# BL-SEARCH-SCOPE-MIGRATION (7/27 鸿波定的原则): 索引范围**只有一个真相源** ——
# ~/.catfish/search-scope.yaml, 面板上看得见、改得动。代码里不留隐藏目录。
#
# 但 DEFAULT_CONFIG 只在 yaml **不存在**时写一次 (ensure_config_exists)，
# 往里加一条对已经有 yaml 的老员工毫无作用 —— 鸿波的 ~/.catfish/output 塞满
# 周报/汇报/对标报告，索引里一条没有，就是这么来的。
#
# 所以新增目录走这张迁移表：load_config() 时**追加进员工的 yaml 实体文件**
# (行级插入，保留原注释)，之后它就是一条普通条目，面板能看能删。
# marker 记在 yaml 的 _applied_migrations 里，员工删掉后不会被塞回来。
#
# 加新条目的规矩：往列表尾部加，永远不改已有条目的 id。
MIGRATIONS: list[tuple[str, str, str]] = [
    # (marker id, 要加的路径, 写进 yaml 的说明注释)
    (
        "catfish-output-2026-07",
        "~/.catfish/output",
        "鲶鱼替你生成的文档（周报 / 汇报 / 报告）—— 文书风格就是从这里学的",
    ),
    # 8/14: 产出目录统一到 outputs (复数)。
    #
    # 之前两个名字并存: output/ (weekly-report / leadership-briefing / 创意 skill /
    # 系统提示词) 和 outputs/ (advisor 草稿 / drafts.rs / briefing_context.rs)。
    # 后果不只是乱 —— catfish_list_my_outputs 只读 output/, 而 8 月的产出
    # 65 个在 outputs/、13 个在 output/, 那个工具漏掉了近期 83% 的东西。
    #
    # 按上面的规矩: **不动** catfish-output-2026-07 那条 (老员工 yaml 里的
    # output/ 保留, 历史文件照样搜得到), 只在尾部追加 outputs。
    (
        "catfish-outputs-2026-08",
        "~/.catfish/outputs",
        "鲶鱼替你生成的文档（统一产出目录）—— 文书风格就是从这里学的",
    ),
]
_MIGRATION_KEY = "_applied_migrations"


DEFAULT_CONFIG = """# 鲶鱼本地文件搜索 · 索引范围配置
#
# 只索引员工明确同意的目录，不会扫全盘。
# 改完保存后运行 `catfish-search index` 重新建索引。

include:
  # 个人常用目录
  - ~/Documents
  - ~/Desktop
  - ~/Downloads

  # BL-FILE-SESSION-INDEX-V1 Phase 4 (5/30): 员工拖给鲶鱼的附件
  # (Companion 解析 file 时落到这, file_parse.rs:265-276). 索引进 local_search
  # 让 "上次拖过来的 PDF 里说啥" 这种内容搜走统一 FTS5, 不再单独维护 BM25 sidecar
  # 跨会话搜. 元数据 + session 关联仍走 ~/.catfish/attachments.db.
  - ~/.catfish/uploads

  # 鲶鱼替你生成的文档（周报 / 汇报 / 报告）—— 文书风格就是从这里学的
  # 8/14: 统一用 outputs (复数)。老员工的 yaml 里可能还有 output/ (单数),
  # 那是 catfish-output-2026-07 迁移加的, 留着让历史文件仍可搜。
  - ~/.catfish/outputs

  # 按需打开下面这些（取消前面的 #）：
  # - ~/work
  # - ~/code
  # - ~/projects
  # - ~/Notes

exclude:
  # 系统 / 缓存
  - ~/Library
  - "**/.Trash"
  - "**/__pycache__"
  - "**/.pytest_cache"
  - "**/.ruff_cache"
  - "**/.mypy_cache"

  # 包管理 / 构建产物
  - "**/node_modules"
  - "**/.venv"
  - "**/venv"
  - "**/env"
  - "**/dist"
  - "**/build"
  - "**/target"
  - "**/.gradle"
  - "**/.idea"
  - "**/.vscode"

  # 版本控制
  - "**/.git"
  - "**/.svn"
  - "**/.hg"

  # 大文件类型
  - "**/*.zip"
  - "**/*.tar"
  - "**/*.tar.gz"
  - "**/*.tgz"
  - "**/*.rar"
  - "**/*.7z"
  - "**/*.iso"
  - "**/*.dmg"
  - "**/*.pkg"
  - "**/*.app"
  - "**/*.mp4"
  - "**/*.mov"
  - "**/*.avi"
  - "**/*.mkv"
  - "**/*.mp3"
  - "**/*.flac"

# 单文件最大大小，超过跳过（避免索引超大 log / DB 导出）
max_file_size_mb: 20

file_types:
  # Office 文档（新格式）
  - .docx
  - .xlsx
  - .pptx
  # Office 旧格式（markitdown 也能处理大多数）
  - .doc
  - .xls
  - .ppt
  # PDF
  - .pdf
  # 纯文本 / 笔记
  - .md
  - .txt
  - .rst
  - .tex
  # 代码（常用）
  - .py
  - .js
  - .ts
  - .tsx
  - .jsx
  - .vue
  - .go
  - .rs
  - .java
  - .kt
  - .swift
  - .c
  - .cpp
  - .cc
  - .h
  - .hpp
  - .cs
  - .rb
  - .php
  - .sh
  - .bash
  - .zsh
  # 配置 / 数据
  - .json
  - .yaml
  - .yml
  - .toml
  - .ini
  - .conf
  - .xml
  - .csv
  - .tsv
  # 前端
  - .html
  - .htm
  - .css
  - .scss
  - .less
  # 其他
  - .sql
  - .log
"""


@dataclass
class SearchConfig:
    """用户的索引范围配置。"""

    include: list[Path] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_file_size_mb: int = 10
    file_types: set[str] = field(default_factory=set)
    #: yaml 的 include 里写了、但当前进程拿不到的目录（不存在 / 没访问授权）。
    #: BL-SEARCH-MISSING-ROOT-SILENT (7/27)：老代码直接过滤掉不留痕，
    #: 员工面板上配 6 个只扫 2 个还不知道为什么。见 load_config 里的注释。
    missing: list[Path] = field(default_factory=list)

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


def ensure_config_exists() -> Path:
    """如果配置文件不存在就用默认值创建，返回路径。"""
    CATFISH_HOME.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return CONFIG_FILE


def _same_path(a: str, b: str) -> bool:
    """两条 yaml 路径条目是否指向同一处（展开 ~ 后比）。"""
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except (OSError, RuntimeError):
        return a.strip() == b.strip()


_MIGRATION_BANNER = "# 鲶鱼自动维护：已执行过的一次性配置迁移，别手工改"


def _ensure_in_include(lines: list[str], path: str, comment: str) -> bool:
    """保证 include 段里有 path。原地改 lines。

    返回「现在 include 里有这条了」—— 已经有 / 刚插进去 都是 True；
    只有**找不到 include 段**（配置形态不认识）才返 False，那种情况不硬塞。

    故意做**行级插入**而不是 yaml.safe_load + safe_dump 重写整个文件 ——
    DEFAULT_CONFIG 里那几十行中文注释是写给员工看的（哪些是系统目录、
    哪些按需打开），safe_dump 会全抹掉。
    """
    try:
        start = next(
            i for i, ln in enumerate(lines) if ln.rstrip() in ("include:", "include :")
        )
    except StopIteration:
        return False

    # include 段的结束 = 下一个顶格且非注释非空的行（即下一个顶层 key）
    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i]
        if s.strip() and not s.startswith((" ", "\t", "#")):
            end = i
            break

    # 员工可能自己手写过，别插重复的。同时记住最后一个**真实条目**的位置。
    last_item = start
    for i in range(start + 1, end):
        item = lines[i].strip()
        if not item.startswith("- "):
            continue  # 注释掉的 `# - ~/work` 不算条目
        if _same_path(item[2:].strip(), path):
            return True
        last_item = i

    # 插在最后一个真实条目之后，而不是整段末尾 —— DEFAULT_CONFIG 段尾是
    # "# 按需打开下面这些（取消前面的 #）" 那堆注释掉的候选项，插它们后面
    # 读起来像是那组的一员，很怪。
    lines[last_item + 1 : last_item + 1] = ["", f"  # {comment}", f"  - {path}"]
    return True


def _strip_migration_block(lines: list[str]) -> list[str]:
    """摘掉文件里已有的 _applied_migrations 块（banner + key + 列表项）。

    重写时先摘再加，免得每次迁移都往文件尾堆一份新的。
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.rstrip() == _MIGRATION_BANNER or ln.startswith(f"{_MIGRATION_KEY}:"):
            # 从 banner/key 起，吃掉后面所有缩进行和空行
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or lines[i].startswith((" ", "\t"))
            ):
                i += 1
            # 顺手把上面留下的尾随空行也收掉，避免文件尾越堆越空
            while out and not out[-1].strip():
                out.pop()
            continue
        out.append(ln)
        i += 1
    return out


def _apply_migrations() -> None:
    """把 MIGRATIONS 里没跑过的条目追加进员工的 search-scope.yaml。

    BL-SEARCH-SCOPE-MIGRATION (7/27): 见 MIGRATIONS 上面的说明。
    任何一步出错都静默返回 —— 迁移失败不该让索引整个跑不起来，
    大不了少一个目录，员工在面板上自己加。
    """
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            return
        applied = data.get(_MIGRATION_KEY)
        applied = set(applied) if isinstance(applied, list) else set()

        pending = [m for m in MIGRATIONS if m[0] not in applied]
        if not pending:
            return

        lines = raw.splitlines()
        for marker, path, comment in pending:
            # 只有真办成了才记 marker。找不到 include 段就留着下次再试，
            # 不能标成"做完了"把这条永久吞掉。
            if _ensure_in_include(lines, path, comment):
                applied.add(marker)
        if not applied:
            return

        lines = _strip_migration_block(lines)
        lines += ["", _MIGRATION_BANNER, f"{_MIGRATION_KEY}:"]
        lines += [f"  - {m}" for m in sorted(applied)]

        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (OSError, yaml.YAMLError):
        return


def load_config() -> SearchConfig:
    """读 ~/.catfish/search-scope.yaml，首次自动创建默认配置。"""
    ensure_config_exists()
    _apply_migrations()
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}

    include_paths = [
        Path(p).expanduser().resolve()
        for p in data.get("include", [])
    ]
    # 去重 —— 员工可能在 yaml 里写了两条指向同一处的路径（~/x 和 /Users/me/x）
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in include_paths:
        if str(p) not in seen:
            seen.add(str(p))
            deduped.append(p)

    # BL-SEARCH-MISSING-ROOT-SILENT (7/27 鸿波实盘): 拿不到的根**必须留痕**。
    #
    # 老代码就一句 `[p for p in deduped if p.exists()]` —— 目录不存在就悄悄
    # 从 include 里消失。鸿波面板上配着 6 个目录，点"重建索引"只跑了 2 个
    # （uploads / output），CLI 打"开始索引 2 个目录"，没有任何一句话解释另外
    # 4 个去哪了。
    #
    # macOS 上这尤其阴 —— ~/Documents、~/Desktop、~/Downloads 没拿到「文件与
    # 文件夹」授权时，exists() 直接返 False（不是抛异常），跟"目录真的不存在"
    # 完全分不出来。Companion 是 GUI app，它 spawn 的索引进程继承 Companion.app
    # 的 TCC 授权，跟员工在终端里跑很可能是两种结果。
    #
    # 现在: 丢掉的根记进 cfg.missing，由 CLI / Dashboard 明着报出来。
    missing = [p for p in deduped if not p.exists()]
    include_paths = [p for p in deduped if p.exists()]
    for p in missing:
        logger.warning("include 里的目录拿不到，本次不扫: %s（不存在，或没有访问授权）", p)

    return SearchConfig(
        include=include_paths,
        exclude=data.get("exclude", []),
        max_file_size_mb=data.get("max_file_size_mb", 10),
        file_types={
            t.lower() if t.startswith(".") else "." + t.lower()
            for t in data.get("file_types", [])
        },
        missing=missing,
    )
