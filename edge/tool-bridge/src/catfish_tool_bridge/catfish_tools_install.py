"""Catfish skill_install tool — 抽自 catfish_tools_install_and_ops.py (5/20 拆分).

Skills Hub MVP 本机版: 装 skill 从 URL / 本地路径 / Hub (URL/SHA256 验证).
helpers: _list_existing_skills / _check_skill_dedup / _dry_run_skill / _install_from_hub.

5/20: 575 行从 catfish_tools_install_and_ops.py 抽出.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .catfish_tools_today import _hermes_dir, _home, _is_today, _unix_to_iso
from .catfish_tools_skill_ops import (  # noqa: F401
    _catfish_skills_root,
    _read_skill_metadata,
    _skill_audit_path,
    _write_skill_audit,
)

logger = logging.getLogger("catfish.tool_bridge")

# ============================================================
# catfish_skill_install — Skills Hub MVP 本机版 (五一 sprint Day 3)
# ============================================================


def _list_existing_skills() -> List[Dict[str, Any]]:
    """枚举所有已装 skill, 返 [{path, name, description}].

    给 dedup 检查 (BL-C13) 用. path 形如 "department/leadership-briefing".
    """
    root = _catfish_skills_root()
    if root is None or not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for ns_dir in root.iterdir():
            if not ns_dir.is_dir() or ns_dir.name.startswith("."):
                continue
            for skill_dir in ns_dir.iterdir():
                if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                # mini parse: name + description 第一行
                name = skill_dir.name
                description_first_line = ""
                try:
                    text = skill_md.read_text(encoding="utf-8")
                    if text.startswith("---"):
                        end = text.find("\n---", 3)
                        if end > 0:
                            for line in text[3:end].strip().split("\n"):
                                ls = line.strip()
                                if ls.startswith("name:"):
                                    name = ls.partition(":")[2].strip().strip("'\"")
                                if ls.startswith("description:"):
                                    description_first_line = ls.partition(":")[2].strip().strip("|").strip()
                                    if not description_first_line:
                                        # description: |- 多行, 找下一非空行
                                        idx = text[3:end].split("\n").index(line)
                                        rest = text[3:end].split("\n")[idx + 1:]
                                        for rl in rest:
                                            if rl.strip() and not rl.strip().startswith("#"):
                                                description_first_line = rl.strip()
                                                break
                                    break
                except Exception:
                    pass
                out.append({
                    "path": f"{ns_dir.name}/{skill_dir.name}",
                    "name": name,
                    "description": description_first_line[:300],
                })
    except Exception as e:
        logger.warning("_list_existing_skills 失败: %s", e)
    return out


def _check_skill_dedup(new_name: str, new_description: str) -> List[Dict[str, Any]]:
    """BL-C13 重复检查 — 找跟新 skill 名/描述高度相似的已装 skill.

    简单 heuristic (够 demo 用):
    - name 完全相同 → 命中
    - description 前 50 字相同 → 命中
    - name 含彼此 (e.g. 'weekly-report' vs 'weekly-report-v2') → 命中

    返 [{path, name, similarity_reason}], 空 = 无重复.
    Phase 2 升级用 embedding 语义相似度.
    """
    if not new_name and not new_description:
        return []
    new_name_lower = (new_name or "").lower().strip()
    new_desc_short = (new_description or "")[:50].strip()

    hits: List[Dict[str, Any]] = []
    for existing in _list_existing_skills():
        ex_name = existing["name"].lower()
        ex_desc = existing["description"][:50]

        reason = ""
        if new_name_lower and ex_name and new_name_lower == ex_name:
            reason = f"name 完全相同 ({new_name})"
        elif (
            new_name_lower and ex_name
            and len(new_name_lower) >= 4 and len(ex_name) >= 4
            and (new_name_lower in ex_name or ex_name in new_name_lower)
        ):
            reason = f"name 互含 ({new_name} ↔ {existing['name']})"
        elif new_desc_short and ex_desc and new_desc_short == ex_desc:
            reason = "description 前 50 字相同"

        if reason:
            hits.append({
                "path": existing["path"],
                "name": existing["name"],
                "similarity_reason": reason,
            })
    return hits


def _dry_run_skill(skill_dir: Path) -> Dict[str, Any]:
    """BL-C12 dry-run 验证 — 试图 import skill 的 script.py 检查基础健康.

    检查:
    1. script.py 存在
    2. 能 import (语法 OK + 顶层依赖能 resolve)
    3. 至少有一个 render_xxx / run / main / 入口函数 (常见命名)

    返 {"ok": True} 或 {"ok": False, "error": "...", "stage": "..."}.
    不真跑 render — render 需 docx 等重依赖, 而且要参数, MVP 不验.
    """
    script_path = skill_dir / "script.py"
    if not script_path.exists():
        # 不是所有 skill 都有 script.py (有些 skill 可能纯 prompt 模板)
        # 没 script.py 视为无侵入 skill, dry-run 通过
        return {"ok": True, "note": "no script.py, skipping import check"}

    # 用 importlib.spec_from_file_location 加载, 模块名加 prefix 防撞
    import importlib.util
    spec_name = f"_dryrun_{skill_dir.parent.name}_{skill_dir.name}".replace("-", "_")
    try:
        spec = importlib.util.spec_from_file_location(spec_name, script_path)
        if spec is None or spec.loader is None:
            return {
                "ok": False,
                "error": f"无法加载 {script_path}",
                "stage": "spec_from_file_location",
            }
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "stage": "import_module",
        }

    # 找入口函数: render_xxx / run / main / execute
    entry_names = [n for n in dir(module) if not n.startswith("_") and callable(getattr(module, n, None))]
    entry_funcs = [
        n for n in entry_names
        if n.startswith("render_") or n in ("run", "main", "execute", "render")
    ]
    if not entry_funcs:
        return {
            "ok": False,
            "error": f"没找到入口函数 (render_xxx / run / main / execute), 只有 {entry_names[:5]}",
            "stage": "entry_function",
        }

    return {
        "ok": True,
        "entry_functions": entry_funcs[:3],
        "note": f"导入 OK, 找到入口 {entry_funcs[0]}",
    }


def _install_from_hub(
    hub_skill: str,
    hub_url: str,
) -> Dict[str, Any]:
    """从 Skills Hub server 拉 skill 到 ~/.catfish/skill-staging/<uuid>/.

    成功返 {ok: True, staging_dir, hub_namespace, hub_name, hub_version}.
    失败返 {ok: False, error}.

    流程:
      1. parse 'ns/name@version' → ns / name / version (version='latest' 默认)
      2. GET {hub_url}/skills/{ns}/{name}/{version} 拿元信息 + 文件列表
      3. mkdir staging dir
      4. 对每个 file, GET {hub_url}/.../files/{path} 写到 staging
      5. 返 staging_dir, caller 走原 install 流程 (dedup + dry-run + 复制)
    """
    import json as _json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    # parse 'ns/name@version'
    if "/" not in hub_skill:
        return {"ok": False, "error": f"hub_skill 格式错: 期望 'ns/name@version', 拿到 {hub_skill!r}"}
    ns_part, _, after_slash = hub_skill.partition("/")
    if "@" in after_slash:
        name_part, _, version_part = after_slash.partition("@")
    else:
        name_part = after_slash
        version_part = "latest"
    ns_part = ns_part.strip()
    name_part = name_part.strip()
    version_part = version_part.strip() or "latest"
    if not ns_part or not name_part:
        return {"ok": False, "error": f"hub_skill 缺 namespace 或 name: {hub_skill!r}"}

    hub_url = hub_url.rstrip("/")

    # 1. 拿元信息
    meta_url = f"{hub_url}/skills/{ns_part}/{name_part}/{version_part}"
    try:
        with urllib.request.urlopen(meta_url, timeout=10) as resp:
            meta = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {
                "ok": False,
                "error": f"hub 里找不到 {hub_skill}. URL: {meta_url}",
            }
        return {"ok": False, "error": f"hub GET 元信息失败 ({e.code}): {meta_url}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {
            "ok": False,
            "error": (
                f"hub 不可达 ({type(e).__name__}: {e}). 检查 {hub_url} 是否启动 "
                "(docker compose ps skills-hub) 或网络."
            ),
        }
    except Exception as e:
        return {"ok": False, "error": f"hub 元信息解析失败: {type(e).__name__}: {e}"}

    files_list = meta.get("files") or []
    if not files_list:
        return {"ok": False, "error": f"hub {hub_skill} 元信息里没 files 列表"}

    # 5/6 安全 P0 G2: 供应链防护. hub 元信息可附 files_sha256 字典:
    #   { "SKILL.md": "abc123...", "compute.py": "def456..." }
    # 客户端拉完每个文件 sha256, 跟 meta 对比, 不匹配 → rmtree + 拒装.
    # meta 没 files_sha256 → 当未签名处理: 严格模式 (CATFISH_HUB_REQUIRE_HASH=1) 拒装,
    # 默认模式只记 audit warning + 返回 unsigned=true.
    files_sha256: dict = meta.get("files_sha256") or {}
    require_hash_env = (os.environ.get("CATFISH_HUB_REQUIRE_HASH") or "").strip() == "1"
    if require_hash_env and not files_sha256:
        return {
            "ok": False,
            "error": (
                f"hub {hub_skill} 元信息没提供 files_sha256, "
                "CATFISH_HUB_REQUIRE_HASH=1 严格模式下拒装. "
                "联系 hub 维护者发布签名版本, 或临时取消 CATFISH_HUB_REQUIRE_HASH."
            ),
        }
    unsigned = not files_sha256

    import hashlib as _hashlib  # noqa: PLC0415

    # 2. 创 staging 目录
    staging_root = Path.home() / ".catfish" / "skill-staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / _uuid.uuid4().hex
    staging.mkdir(parents=True, exist_ok=True)

    # 3. 逐个下载文件 + sha256 校验
    real_version = meta.get("version") or version_part
    for file_path in files_list:
        if not isinstance(file_path, str) or ".." in file_path or file_path.startswith("/"):
            # 防 zip-slip 类路径越界
            continue
        file_url = (
            f"{hub_url}/skills/{ns_part}/{name_part}/{real_version}/files/{file_path}"
        )
        try:
            with urllib.request.urlopen(file_url, timeout=20) as resp:
                content = resp.read()
        except Exception as e:
            # 清 staging 防部分文件残留
            import shutil as _sh  # noqa: PLC0415
            _sh.rmtree(staging, ignore_errors=True)
            return {
                "ok": False,
                "error": f"hub 下载 {file_path} 失败 ({type(e).__name__}: {e})",
            }
        # sha256 校验 (有 expected hash 才校, 没 expected 走 unsigned 流程)
        expected = files_sha256.get(file_path)
        if expected:
            actual = _hashlib.sha256(content).hexdigest()
            if actual.lower() != str(expected).lower():
                import shutil as _sh  # noqa: PLC0415
                _sh.rmtree(staging, ignore_errors=True)
                return {
                    "ok": False,
                    "error": (
                        f"hub {hub_skill} 文件 {file_path} sha256 不匹配. "
                        f"预期 {expected}, 实际 {actual}. "
                        f"中间人攻击或 hub 被篡改, 拒装."
                    ),
                }
        target = staging / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    # 4. 验证关键文件
    if not (staging / "SKILL.md").exists():
        import shutil as _sh  # noqa: PLC0415
        _sh.rmtree(staging, ignore_errors=True)
        return {
            "ok": False,
            "error": f"hub 拿到的 {hub_skill} 缺 SKILL.md (元信息里 files={files_list})",
        }

    return {
        "ok": True,
        "staging_dir": str(staging),
        "hub_namespace": ns_part,
        "hub_name": name_part,
        "hub_version": real_version,
        "unsigned": unsigned,  # 5/6 G2: 没 sha256 校验时为 True
    }


def skill_install(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 本机安装 skill — 两种来源:
      (A) source_dir 本机目录 (Day 3 MVP)
      (B) hub_skill 'ns/name@version' (5/5 ship, BL-D1 Skills Hub 第 1 件)

    args:
        source_dir: 模式 A 的本机源目录, 跟 hub_skill 互斥
        hub_skill: 模式 B 的 hub 路径
        hub_url: 模式 B 的 hub server base URL, 默认 env CATFISH_HUB_URL or http://127.0.0.1:9001
        namespace: 安装到本机的 namespace, 默认 'personal'
        overwrite: 默认 False
    """
    source = (args.get("source_dir") or "").strip()
    hub_skill = (args.get("hub_skill") or "").strip()
    namespace = (args.get("namespace") or "personal").strip()
    overwrite = bool(args.get("overwrite", False))

    # 模式互斥
    if source and hub_skill:
        return {
            "ok": False,
            "error": "source_dir 跟 hub_skill 互斥, 二选一",
        }
    if not source and not hub_skill:
        return {
            "ok": False,
            "error": "必须传 source_dir (本机目录) 或 hub_skill (hub 路径) 之一",
        }

    install_source: str  # 'local' | 'hub'
    hub_meta: Dict[str, Any] = {}

    # 模式 B: hub URL — 先拉到 staging 目录, 当 source_dir 用
    if hub_skill:
        hub_url_raw = (args.get("hub_url") or "").strip()
        if not hub_url_raw:
            hub_url_raw = os.environ.get("CATFISH_HUB_URL") or "http://127.0.0.1:9001"
        hub_result = _install_from_hub(hub_skill, hub_url_raw)
        if not hub_result.get("ok"):
            return hub_result  # 错误透传
        source = hub_result["staging_dir"]
        install_source = "hub"
        hub_meta = {
            "hub_skill": hub_skill,
            "hub_url": hub_url_raw,
            "hub_namespace": hub_result["hub_namespace"],
            "hub_name": hub_result["hub_name"],
            "hub_version": hub_result["hub_version"],
        }
        # hub 自带 namespace 时, 如果员工没 explicit 传 namespace, 用 hub 的
        if "namespace" not in args or not args.get("namespace"):
            namespace = hub_result["hub_namespace"]
    else:
        install_source = "local"

    # 展开 ~
    source_path = Path(source).expanduser().resolve()

    # 安全检查: 不允许从系统目录装.
    # 注意: macOS 上 /etc 是 /private/etc 的 symlink, resolve 后变 /private/etc,
    # 所以 blocklist 同时含 / 和 /private/ 两套.
    _system_prefixes = ["/etc", "/usr", "/bin", "/sbin", "/System", "/Library/System"]
    blocked_prefixes = _system_prefixes + [f"/private{p}" for p in _system_prefixes]
    str_source = str(source_path)
    if any(str_source.startswith(p) for p in blocked_prefixes):
        return {
            "ok": False,
            "error": f"安全考虑: 不允许从系统目录安装 ({source_path})",
        }

    if not source_path.is_dir():
        return {"ok": False, "error": f"source_dir 不存在或不是目录: {source_path}"}

    # 必须含 SKILL.md
    skill_md = source_path / "SKILL.md"
    if not skill_md.exists():
        return {
            "ok": False,
            "error": f"{source_path}/SKILL.md 不存在 — 不是合法 skill 目录",
        }

    # 解析 SKILL.md 拿 name (用于决定安装目标路径)
    metadata = _read_skill_metadata(skill_md)
    # _read_skill_metadata 不返 name, 这里 mini parse 一下
    skill_name = ""
    try:
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].strip().split("\n"):
                    line = line.strip()
                    if line.startswith("name:") and not line.startswith(" "):
                        _, _, val = line.partition(":")
                        skill_name = val.strip().strip("'\"")
                        break
    except Exception as e:
        return {"ok": False, "error": f"读 SKILL.md 失败: {e}"}

    if not skill_name:
        return {
            "ok": False,
            "error": "SKILL.md frontmatter 缺 name 字段, 无法决定安装路径",
        }

    # ── BL-C13 dedup 检查 (五一 sprint 5/2 收尾) ──────────────
    # overwrite=True 跳过 dedup (员工显式说要覆盖). force_install=True 也跳过 (LLM 明确知重了还要装).
    if not overwrite and not args.get("force_install"):
        # 取 SKILL.md 第一行 description
        new_desc = ""
        try:
            text = skill_md.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end > 0:
                    for raw_line in text[3:end].strip().split("\n"):
                        ls = raw_line.strip()
                        if ls.startswith("description:"):
                            v = ls.partition(":")[2].strip().strip("|").strip()
                            if v:
                                new_desc = v
                            break
        except Exception:
            pass

        dups = _check_skill_dedup(skill_name, new_desc)
        if dups:
            audit_event_dedup = {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "event_type": "install_dedup_blocked",
                "skill_path": f"{namespace}/{skill_name}",
                "duplicates": dups,
            }
            _write_skill_audit(audit_event_dedup)
            return {
                "ok": False,
                "error": (
                    f"检测到 {len(dups)} 个高度相似 skill: "
                    + ", ".join(f"{d['path']} ({d['similarity_reason']})" for d in dups)
                    + ". 想强制装传 force_install=true; 想覆盖具体某个传 overwrite=true."
                ),
                "duplicates": dups,
            }

    # namespace 安全 (不允许 .. / 跨目录)
    if ".." in namespace or "/" in namespace:
        return {"ok": False, "error": f"namespace 不允许 '..' 或 '/' ({namespace})"}

    # 目标路径: catfish/skills/<namespace>/<skill_name>/
    root = _catfish_skills_root()
    if root is None:
        return {"ok": False, "error": "找不到 catfish skills 目录"}

    target_dir = root / namespace / skill_name
    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": "install",
        "skill_path": f"{namespace}/{skill_name}",
        "skill_version": metadata["version"],
        "source_dir": str(source_path),
        "overwrite": overwrite,
    }

    # 同名已存在?
    if target_dir.exists():
        if not overwrite:
            audit_event.update({
                "ok": False,
                "error_msg": "skill 已存在, overwrite=false",
            })
            _write_skill_audit(audit_event)
            return {
                "ok": False,
                "error": (
                    f"skill {namespace}/{skill_name} 已存在. "
                    "想覆盖请传 overwrite=true (会先 backup 到 skill-trash)."
                ),
            }
        # overwrite: 先 backup
        ts = int(time.time())
        trash_root = Path.home() / ".catfish" / "skill-trash"
        trash_root.mkdir(parents=True, exist_ok=True)
        backup_dir = trash_root / f"{ts}-{skill_name}-replaced"
        try:
            shutil.move(str(target_dir), str(backup_dir))
            audit_event["backup_path"] = str(backup_dir)
        except Exception as e:
            audit_event.update({
                "ok": False,
                "error_msg": f"backup 失败: {e}",
            })
            _write_skill_audit(audit_event)
            return {"ok": False, "error": f"backup 旧 skill 失败: {e}"}

    # 复制
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(source_path), str(target_dir))
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_msg": f"复制失败: {e}",
        })
        _write_skill_audit(audit_event)
        return {"ok": False, "error": f"复制 skill 失败: {e}"}

    # ── BL-C12 dry-run 验证 (五一 sprint 5/2 收尾) ──────────────
    # 复制完立即试 import script.py + 找入口函数, 失败 rollback (删 target_dir).
    # 保护 LLM 装坏 skill 后整个 catfish 链路炸. skip_dry_run=true 跳过 (老 skill / 不带 script).
    if not args.get("skip_dry_run"):
        dry = _dry_run_skill(target_dir)
        if not dry.get("ok"):
            # rollback: 删 target_dir
            try:
                shutil.rmtree(target_dir)
            except Exception as rm_e:
                logger.warning("dry-run 失败后 rollback 删目录失败: %s", rm_e)
            audit_event.update({
                "ok": False,
                "error_msg": f"dry-run 失败 ({dry.get('stage')}): {dry.get('error')}",
                "rolled_back": True,
            })
            _write_skill_audit(audit_event)
            return {
                "ok": False,
                "error": (
                    f"skill 装上后 dry-run 验证失败 (stage={dry.get('stage')}): "
                    f"{dry.get('error')}. 已 rollback 删目录, 不影响其他 skill."
                ),
                "dry_run": dry,
            }
        audit_event["dry_run"] = dry

    audit_event.update({
        "ok": True,
        "installed_path": str(target_dir),
        "install_source": install_source,  # 'local' | 'hub'
        **({"hub_meta": hub_meta} if hub_meta else {}),
    })
    _write_skill_audit(audit_event)

    # 5/5 BL-D1 第 1 件: hub 模式下 staging 目录用完清掉, 防 ~/.catfish/skill-staging/ 堆积
    if install_source == "hub" and source_path.parent.name == "skill-staging":
        try:
            import shutil as _sh  # noqa: PLC0415
            _sh.rmtree(source_path, ignore_errors=True)
        except Exception:
            pass

    dry_note = audit_event.get("dry_run", {}).get("note", "")
    source_label = (
        f"hub {hub_meta.get('hub_skill')}" if install_source == "hub" else str(source_path)
    )
    return {
        "ok": True,
        "installed_path": f"{namespace}/{skill_name}",
        "source": install_source,
        **({"hub_meta": hub_meta} if hub_meta else {}),
        "summary": (
            f"已安装 skill {namespace}/{skill_name} (v{metadata['version']}) "
            f"从 {source_label}. dry-run 通过 ({dry_note}). "
            f"仪表盘下次刷新会出现, gateway 重新扫到后 LLM 也能调."
            + (f" 旧版备份: {audit_event.get('backup_path')}" if overwrite else "")
        ),
        "dry_run": audit_event.get("dry_run", {}),
    }


