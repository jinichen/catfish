"""加载和生成 search-scope.yaml 配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATFISH_HOME = Path.home() / ".catfish"
CONFIG_FILE = CATFISH_HOME / "search-scope.yaml"
DB_FILE = CATFISH_HOME / "search.db"


DEFAULT_CONFIG = """# 鲶鱼本地文件搜索 · 索引范围配置
#
# 只索引员工明确同意的目录，不会扫全盘。
# 改完保存后运行 `catfish-search index` 重新建索引。

include:
  # 个人常用目录
  - ~/Documents
  - ~/Desktop
  - ~/Downloads

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

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


def ensure_config_exists() -> Path:
    """如果配置文件不存在就用默认值创建，返回路径。"""
    CATFISH_HOME.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return CONFIG_FILE


def load_config() -> SearchConfig:
    """读 ~/.catfish/search-scope.yaml，首次自动创建默认配置。"""
    ensure_config_exists()
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}

    include_paths = [
        Path(p).expanduser().resolve()
        for p in data.get("include", [])
    ]
    include_paths = [p for p in include_paths if p.exists()]

    return SearchConfig(
        include=include_paths,
        exclude=data.get("exclude", []),
        max_file_size_mb=data.get("max_file_size_mb", 10),
        file_types={
            t.lower() if t.startswith(".") else "." + t.lower()
            for t in data.get("file_types", [])
        },
    )
