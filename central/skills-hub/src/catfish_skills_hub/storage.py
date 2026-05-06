"""Skills Hub 存储层 — MVP: 文件系统, Phase 2 改 PG.

# 目录布局

  <CATFISH_HUB_ROOT>/                        ← 默认 ~/.catfish-hub/
  ├── skills/
  │   └── <namespace>/
  │       └── <name>/
  │           └── <version>/
  │               ├── SKILL.md
  │               ├── script.py (可选)
  │               └── ...其他文件
  ├── manifest.json                          ← 全局 skill 清单 (cache, lazy 重建)
  └── audit.jsonl                            ← 发布 / 删除 / 拉取 audit

# 设计

- 一个 skill 多版本: skills/department/leadership-briefing/1.0.0/, 1.1.0/, 2.0.0/
- 安全: namespace / name / version 都过路径检查 (无 .. / /)
- 审计: 任何写操作 (publish / delete) 都 append audit.jsonl

无审核流 (Phase 2 加 publish → pending → admin approve → live), MVP 直接 live.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.skills_hub.storage")

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._\-+]{1,32}$")


def hub_root() -> Path:
    """中央 hub 文件根. CATFISH_HUB_ROOT env 可 override (单测用)."""
    custom = os.environ.get("CATFISH_HUB_ROOT", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".catfish-hub"


def _skills_dir() -> Path:
    return hub_root() / "skills"


def _audit_path() -> Path:
    return hub_root() / "audit.jsonl"


def _validate_seg(seg: str, kind: str) -> None:
    """验证 namespace / name / version 段无 .. / / / 等危险字符."""
    if not seg or seg in ("..", ".") or "/" in seg or "\\" in seg:
        raise ValueError(f"{kind} 不合法: {seg!r}")
    pattern = _VERSION_RE if kind == "version" else _NAME_RE
    if not pattern.match(seg):
        raise ValueError(f"{kind} 含非法字符 (允许 [a-zA-Z0-9_-]+, version 加 .): {seg!r}")


@dataclass
class SkillVersion:
    """已发布 skill 的一个版本."""

    namespace: str
    name: str
    version: str
    published_at: str  # ISO8601
    published_by: str  # email
    deprecated: bool = False
    description: str = ""
    files: list[str] = field(default_factory=list)


def _write_audit(event: dict) -> None:
    """append 一行 jsonl. 失败静默不影响主流程."""
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("audit 写失败: %s", e)


def _parse_skill_md(skill_md_text: str) -> dict:
    """mini parse SKILL.md frontmatter — name / version / description / deprecated."""
    out = {"name": "", "version": "", "description": "", "deprecated": False}
    if not skill_md_text.startswith("---"):
        return out
    end = skill_md_text.find("\n---", 3)
    if end < 0:
        return out
    fm = skill_md_text[3:end].strip()
    for line in fm.split("\n"):
        ls = line.strip()
        if ":" not in ls or ls.startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue
        key, _, val = ls.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"|").strip()
        if key == "name":
            out["name"] = val
        elif key == "version":
            out["version"] = val
        elif key == "description":
            out["description"] = val[:500]
        elif key == "deprecated":
            out["deprecated"] = val.lower() in ("true", "yes", "1")
    return out


def list_skills(namespace_filter: str | None = None) -> list[dict]:
    """列出所有发布过的 skill (按 namespace/name 分组, 各取最新版本)."""
    out: list[dict] = []
    root = _skills_dir()
    if not root.exists():
        return out

    for ns_dir in sorted(root.iterdir()):
        if not ns_dir.is_dir() or ns_dir.name.startswith("."):
            continue
        if namespace_filter and ns_dir.name != namespace_filter:
            continue
        for skill_dir in sorted(ns_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            versions = sorted(
                [v.name for v in skill_dir.iterdir() if v.is_dir() and not v.name.startswith(".")],
                reverse=True,
            )
            if not versions:
                continue
            latest_dir = skill_dir / versions[0]
            skill_md = latest_dir / "SKILL.md"
            meta = {}
            if skill_md.exists():
                try:
                    meta = _parse_skill_md(skill_md.read_text(encoding="utf-8"))
                except Exception:
                    pass
            out.append({
                "namespace": ns_dir.name,
                "name": skill_dir.name,
                "latest_version": versions[0],
                "all_versions": versions,
                "description": meta.get("description", ""),
                "deprecated": meta.get("deprecated", False),
            })
    return out


def get_skill(namespace: str, name: str, version: str = "latest") -> dict | None:
    """获取 skill 元信息 + 文件列表 + SKILL.md 全文.

    version='latest' 取最新版.
    返 None = 不存在.
    """
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")

    skill_dir = _skills_dir() / namespace / name
    if not skill_dir.is_dir():
        return None

    if version == "latest":
        versions = sorted(
            [v.name for v in skill_dir.iterdir() if v.is_dir() and not v.name.startswith(".")],
            reverse=True,
        )
        if not versions:
            return None
        version = versions[0]
    else:
        _validate_seg(version, "version")

    version_dir = skill_dir / version
    if not version_dir.is_dir():
        return None

    skill_md = version_dir / "SKILL.md"
    skill_md_text = ""
    meta: dict = {}
    if skill_md.exists():
        try:
            skill_md_text = skill_md.read_text(encoding="utf-8")
            meta = _parse_skill_md(skill_md_text)
        except Exception as e:
            logger.warning("读 %s 失败: %s", skill_md, e)

    # 列文件 + 算 sha256 (5/6 安全 G2: 客户端拉文件时校验防中间人/篡改)
    files: list[str] = []
    files_sha256: dict[str, str] = {}
    for child in sorted(version_dir.rglob("*")):
        if child.is_file():
            rel = str(child.relative_to(version_dir))
            files.append(rel)
            try:
                # skill 文件最大几 MB (绝大多数 KB 级), 直接 read_bytes 算 hash
                files_sha256[rel] = hashlib.sha256(child.read_bytes()).hexdigest()
            except OSError as e:
                # 读不了就不放 hash, 客户端会当 unsigned 处理
                logger.warning("sha256 算 %s 失败: %s", child, e)

    return {
        "namespace": namespace,
        "name": name,
        "version": version,
        "description": meta.get("description", ""),
        "deprecated": meta.get("deprecated", False),
        "files": files,
        "files_sha256": files_sha256,  # 5/6 G2: 客户端 _install_from_hub 比对
        "skill_md": skill_md_text,
    }


def download_file(namespace: str, name: str, version: str, file_path: str) -> bytes | None:
    """下载 skill 某个文件 (二进制). 路径检查防 traversal."""
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")
    _validate_seg(version, "version")

    if ".." in file_path or file_path.startswith("/"):
        raise ValueError(f"file_path 不合法: {file_path!r}")

    target = _skills_dir() / namespace / name / version / file_path
    if not target.is_file():
        return None
    # 双重保险: resolve 后必须仍在 version_dir 下
    version_dir = (_skills_dir() / namespace / name / version).resolve()
    try:
        if not str(target.resolve()).startswith(str(version_dir)):
            raise ValueError("路径越界")
    except Exception:
        raise ValueError(f"file_path 越界: {file_path!r}")

    return target.read_bytes()


def publish_skill(
    namespace: str,
    files: dict[str, bytes],
    *,
    published_by: str,
) -> dict:
    """发布一个 skill — 写入 hub/skills/<ns>/<name>/<version>/.

    files: dict, 相对路径 → bytes. 必须含 SKILL.md.
    name + version 从 SKILL.md frontmatter 解析.

    返 {ok, namespace, name, version, files_count, error?}
    """
    _validate_seg(namespace, "namespace")

    if "SKILL.md" not in files:
        return {"ok": False, "error": "files 缺 SKILL.md"}

    try:
        skill_md_text = files["SKILL.md"].decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": "SKILL.md 不是 UTF-8"}

    meta = _parse_skill_md(skill_md_text)
    name = meta.get("name", "").strip()
    version = meta.get("version", "").strip()
    if not name or not version:
        return {"ok": False, "error": "SKILL.md frontmatter 缺 name 或 version"}

    try:
        _validate_seg(name, "name")
        _validate_seg(version, "version")
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    target_dir = _skills_dir() / namespace / name / version
    if target_dir.exists():
        return {
            "ok": False,
            "error": f"{namespace}/{name}/{version} 已发布过, 改 version 字段后重发",
        }

    # 写文件
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for rel_path, content in files.items():
            if ".." in rel_path or rel_path.startswith("/"):
                raise ValueError(f"file 路径不合法: {rel_path}")
            target_file = target_dir / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(content)
            written.append(rel_path)
    except Exception as e:
        # rollback
        try:
            shutil.rmtree(target_dir)
        except Exception:
            pass
        return {"ok": False, "error": f"写文件失败: {e}"}

    _write_audit({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "publish",
        "namespace": namespace,
        "name": name,
        "version": version,
        "published_by": published_by,
        "files_count": len(written),
    })

    return {
        "ok": True,
        "namespace": namespace,
        "name": name,
        "version": version,
        "files_count": len(written),
        "summary": f"已发布 {namespace}/{name}/{version} ({len(written)} 个文件)",
    }


def delete_skill_version(
    namespace: str, name: str, version: str, *, deleted_by: str, reason: str = "",
) -> dict:
    """删除一个版本 (整个 version 目录). 不删整个 skill (其他版本保留)."""
    _validate_seg(namespace, "namespace")
    _validate_seg(name, "name")
    _validate_seg(version, "version")

    version_dir = _skills_dir() / namespace / name / version
    if not version_dir.is_dir():
        return {"ok": False, "error": f"{namespace}/{name}/{version} 不存在"}

    try:
        shutil.rmtree(version_dir)
    except Exception as e:
        return {"ok": False, "error": f"删除失败: {e}"}

    # 如果该 skill 已无任何 version, 把 skill 目录也清掉
    skill_dir = version_dir.parent
    try:
        if skill_dir.exists() and not any(skill_dir.iterdir()):
            skill_dir.rmdir()
    except Exception:
        pass

    _write_audit({
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "delete_version",
        "namespace": namespace,
        "name": name,
        "version": version,
        "deleted_by": deleted_by,
        "reason": reason,
    })

    return {
        "ok": True,
        "summary": f"已删除 {namespace}/{name}/{version}",
    }


def read_audit(limit: int = 100) -> list[dict]:
    """读最近 N 行 audit (倒序). 给 admin 看历史."""
    path = _audit_path()
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning("读 audit 失败: %s", e)
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
