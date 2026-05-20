"""Catfish skill ops tools — 抽自 catfish_tools.py (5/20 拆分).

工具:
  catfish_propose_skill: agent 自动抽 skill (员工 confirm 门槛)
  catfish_propose_skill_revision: skill 改版 propose (SemVer + 红线字段冻)
  catfish_run_skill: 跑 ~/.catfish/skills/<ns>/<skill>/script.py

主文件 import 这些 + helpers 给 dispatch 用.
5/20: 805 行从 catfish_tools.py 抽出.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .catfish_tools_today import _hermes_dir, _home, _is_today, _unix_to_iso

logger = logging.getLogger("catfish.tool_bridge")


# ============================================================
# propose_skill + propose_skill_revision (5/21 拆: 426 行抽到 catfish_tools_propose.py)
# ============================================================

from .catfish_tools_propose import (  # noqa: F401
    SKILL_PROPOSALS_PATH,
    SKILL_REVISIONS_PATH,
    _append_proposal_event,
    _append_revision_event,
    _is_redline_skill_name,
    _read_proposals_history,
    _read_revisions_history,
    _semver_tuple,
    propose_skill,
    propose_skill_revision,
)

# ============================================================
# catfish_run_skill —— 调用 catfish/skills/ 下工程审定 skill
# ============================================================
#
# 设计:
#   - skill_path 必须在白名单 (扫 catfish_skills_root 得来)
#   - 不允许任意路径, 防止越界 import
#   - 加载 script.py, 找 render_* 函数, 用反射调用
#   - 捕获返回值里的文件路径, 拼到 'files' 字段


def _catfish_skills_root() -> Optional[Path]:
    """复用 gateway 的 skills 发现逻辑, 但 tool-bridge 独立运行不能 import gateway.

    优先级:
      1. CATFISH_SKILLS_DIR env
      2. 从本文件向上找
    """
    env_dir = os.environ.get("CATFISH_SKILLS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p

    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "skills"
        if cand.is_dir() and (cand / "department").is_dir():
            return cand
    return None


def _read_skill_metadata(skill_md: Path) -> Dict[str, Any]:
    """读 SKILL.md frontmatter 拿 version/deprecated/deprecated_reason.

    五一 sprint Day 2: Skill 全生命周期 4 步基础.
    tool-bridge 独立 venv 不能 import gateway 的 skills_loader, 这里写 mini 版本.

    返回 {version, deprecated, deprecated_reason}, 没 frontmatter 走默认值.
    """
    default = {"version": "0.1.0", "deprecated": False, "deprecated_reason": ""}
    if not skill_md.exists():
        return default
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return default

    # 找 --- ... --- frontmatter
    if not text.startswith("---"):
        return default
    end_idx = text.find("\n---", 3)
    if end_idx < 0:
        return default
    fm_text = text[3:end_idx].strip()

    # mini yaml 解析: 不引 yaml 依赖, 只支持 key: value 单行
    # SKILL.md 复杂字段 (description |- multiline) 这里跳过, 只关心 version/deprecated 单行
    result = dict(default)
    for line in fm_text.split("\n"):
        line = line.strip()
        if ":" not in line or line.startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t"):
            continue  # 缩进行 (description 子内容) 跳过
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key == "version" and val:
            result["version"] = val
        elif key == "deprecated":
            result["deprecated"] = val.lower() in ("true", "yes", "1")
        elif key == "deprecated_reason" and val:
            result["deprecated_reason"] = val
    return result


def _skill_audit_path() -> Path:
    """skill 调用审计 jsonl 路径. ~/.catfish/skill_audit.jsonl, 一行一个事件."""
    return Path.home() / ".catfish" / "skill_audit.jsonl"


def _write_skill_audit(event: Dict[str, Any]) -> None:
    """append 一行 JSON 到 ~/.catfish/skill_audit.jsonl. 失败静默, 不阻塞主流程."""
    try:
        path = _skill_audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 不存原始 params (可能含密码 / PII), 只存关键 metadata
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        # audit 写失败不阻塞用户操作
        try:
            import logging  # noqa: PLC0415
            logging.getLogger("catfish.tool_bridge").warning(
                "skill_audit 写失败: %s", e
            )
        except Exception:
            pass


def _load_skill_module(script_py: Path):
    """动态 import 一个 skill 的 script.py.

    技术坑:
      `from __future__ import annotations` + `@dataclass` 在 importlib 显式加载
      时, dataclass 装饰器会去 `sys.modules.get(cls.__module__)` 查模块的
      `__dict__`. 如果我们没把 module 提前 put 进 sys.modules, 这个 lookup 返回
      None, 抛 AttributeError("'NoneType' object has no attribute '__dict__'").

    解法: 先 sys.modules[name] = module, 再 exec_module. 失败时清理.

    用唯一 name 防 skill 之间冲突 (script.py 在不同 skill 都叫 script.py).
    """
    import importlib.util
    import sys

    mod_name = f"catfish_skill_{script_py.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, script_py)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {script_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # 失败清掉, 不留半截 module 污染 sys.modules
        sys.modules.pop(mod_name, None)
        raise
    return module


def _find_render_function(module) -> Optional[Tuple[str, Any]]:
    """在 skill module 里找 render_* 函数. 返回 (name, fn) 或 None."""
    for attr in dir(module):
        if attr.startswith("render_") and callable(getattr(module, attr)):
            return attr, getattr(module, attr)
    return None


def _function_signature_help(fn) -> Dict[str, Any]:
    """给 LLM 看的 function param schema —— 从 inspect.signature 推导."""
    import inspect

    sig = inspect.signature(fn)
    params: Dict[str, Any] = {}
    required: List[str] = []
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        info: Dict[str, Any] = {}
        if p.annotation is not inspect.Parameter.empty:
            info["type"] = str(p.annotation)
        if p.default is inspect.Parameter.empty:
            required.append(name)
        else:
            info["default"] = repr(p.default)
        params[name] = info
    return {
        "function": fn.__name__,
        "params": params,
        "required": required,
        "doc": (fn.__doc__ or "").strip()[:1000],
    }


def _extract_file_paths(result: Any) -> List[str]:
    """从 skill render 返回值里挖文件路径 (.docx / .xlsx / .pptx 等).

    支持几种返回形态:
      - {"docx": "/path/to/x.docx", ...}
      - {"files": [...]}
      - 字符串路径
    """
    paths: List[str] = []
    if isinstance(result, str):
        if "/" in result and "." in result:
            paths.append(result)
    elif isinstance(result, dict):
        for k, v in result.items():
            if isinstance(v, str) and "/" in v and "." in v:
                paths.append(v)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and "/" in item:
                        paths.append(item)
    elif isinstance(result, list):
        for item in result:
            if isinstance(item, str) and "/" in item:
                paths.append(item)
    # 去重保持顺序
    seen = set()
    deduped = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def run_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 调 catfish 工程审定 skill.

    args:
      skill_path: 相对路径, 例 'department/leadership-briefing'
      params: render 函数入参 dict; {'_help': true} 返回 schema

    返回 dict.
    """
    skill_path = (args.get("skill_path") or "").strip().strip("/")
    params = args.get("params") or {}

    if not skill_path:
        return {
            "ok": False,
            "error": "skill_path 必填",
            "files": [],
            "summary": "",
        }

    # 路径安全检查
    if ".." in skill_path.split("/"):
        return {
            "ok": False,
            "error": "skill_path 不允许 '..' 越界",
            "files": [],
            "summary": "",
        }

    root = _catfish_skills_root()
    if root is None:
        return {
            "ok": False,
            "error": (
                "找不到 catfish skills 目录. 设 CATFISH_SKILLS_DIR env "
                "或检查部署路径."
            ),
            "files": [],
            "summary": "",
        }

    skill_dir = root / skill_path
    if not skill_dir.is_dir():
        return {
            "ok": False,
            "error": f"skill 不存在: {skill_path}",
            "files": [],
            "summary": "",
        }
    script_py = skill_dir / "script.py"
    if not script_py.exists():
        return {
            "ok": False,
            "error": (
                f"{skill_path}/script.py 不存在. catfish_run_skill 只调凝固"
                f"好的 skill (有 script.py 的). 没凝固的工作流, 先让员工"
                f"教学一遍, 再用 catfish_freeze_skill 自动生成."
            ),
            "files": [],
            "summary": "",
        }

    # 加载 + 找 render_*
    try:
        module = _load_skill_module(script_py)
    except Exception as e:
        return {
            "ok": False,
            "error": f"加载 {script_py} 失败: {e!r}",
            "files": [],
            "summary": "",
        }

    pair = _find_render_function(module)
    if pair is None:
        return {
            "ok": False,
            "error": (
                f"{skill_path}/script.py 没有 render_* 函数 — skill 没正确暴露入口"
            ),
            "files": [],
            "summary": "",
        }
    fn_name, fn = pair

    # _help 模式: 不真跑, 返回参数 schema
    if params.get("_help"):
        help_info = _function_signature_help(fn)
        return {
            "ok": True,
            "help": help_info,
            "files": [],
            "summary": (
                f"{skill_path} 入口: {fn_name}({', '.join(help_info['required'])}). "
                f"完整 schema 在 help 字段; 详细规范看 {skill_dir / 'SKILL.md'}."
            ),
        }

    # 五一 sprint Day 2: 读 skill metadata (version/deprecated)
    skill_md = skill_dir / "SKILL.md"
    metadata = _read_skill_metadata(skill_md)
    deprecated_warning = None
    if metadata["deprecated"]:
        reason = metadata["deprecated_reason"] or "(未填原因)"
        deprecated_warning = (
            f"⚠️ skill '{skill_path}' 已下线 (deprecated). 原因: {reason}. "
            "本次仍执行但建议换用其他 skill."
        )

    # 真调 — 计时 + audit
    started_at = time.time()
    audit_event: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "skill_path": skill_path,
        "skill_version": metadata["version"],
        "deprecated": metadata["deprecated"],
        "param_keys": sorted([k for k in params.keys() if not k.startswith("_")]),
        # 注意: 不存 params 原始值 (可能含 PII), 只记 key 列表
    }
    try:
        result = fn(**params)
    except TypeError as e:
        audit_event.update({
            "ok": False,
            "error_type": "TypeError",
            "error_msg": str(e)[:500],
            "duration_ms": int((time.time() - started_at) * 1000),
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": (
                f"{fn_name} 参数不匹配: {e}. 用 params={{'_help': True}} 看 schema."
            ),
            "files": [],
            "summary": "",
        }
    except Exception as e:
        audit_event.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_msg": str(e)[:500],
            "duration_ms": int((time.time() - started_at) * 1000),
        })
        _write_skill_audit(audit_event)
        return {
            "ok": False,
            "error": f"{fn_name} 执行失败: {e!r}",
            "files": [],
            "summary": "",
        }

    duration_ms = int((time.time() - started_at) * 1000)
    files = _extract_file_paths(result)

    # 写 audit (成功)
    audit_event.update({
        "ok": True,
        "duration_ms": duration_ms,
        "file_count": len(files),
        "files": files[:10],  # 限制 10 个 path 防 audit 过大
    })
    _write_skill_audit(audit_event)

    response: Dict[str, Any] = {
        "ok": True,
        "result": result,
        "files": files,
        "summary": (
            f"已通过 {skill_path} (v{metadata['version']}) 生成 {len(files)} 个文件: " +
            (", ".join(files) if files else "(无文件输出, result 见 result 字段)")
        ),
    }
    if deprecated_warning:
        response["deprecated_warning"] = deprecated_warning
    return response


