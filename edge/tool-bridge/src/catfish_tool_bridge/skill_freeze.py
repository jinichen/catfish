"""skill_freeze — BL-MM9-FREEZE (5/12 鸿波拍板) 教学→凝固管道.

# 闭环

  教学:   员工说"我教你"  → LLM agent 调 catfish_browser_* / recognize_captcha
          / browser_locate 一步步操作 → trace_recorder 自动写 jsonl
  凝固:   员工说"凝固成 skill" → catfish_freeze_skill(name='eis-login', ...)
          → 读 trace → 模板化生成 script.py + SKILL.md → 落到
          catfish/skills/<ns>/<name>/ → spawn install_to_hermes.sh 同步到
          ~/.hermes/skills/productivity/catfish-<name>/
  复用:   下次员工说"上 EIS 看待办" → LLM 看 system prompt 注入的 skill 列表
          → 调 catfish_run_skill('department/eis-login', {...}) → run_skill
          load script.py → 全套 Playwright 跑完 → 返结果

# 这模块只管"凝固"这一段

输入: name (e.g. 'eis-login'), namespace (默认 'department'), description,
trace_start / trace_end (时间区间, 默认最近 1 小时所有成功 trace)

输出: script.py + SKILL.md + 副作用 (install_to_hermes 同步)

# 模板化逻辑 (朴素 + 务实 MVP)

| trace tool                       | script.py 行                                                         |
|----------------------------------|---------------------------------------------------------------------|
| catfish_browser_goto(url)        | page.goto(url, wait_until="load")                                   |
| catfish_browser_snapshot(...)    | (跳过 — snapshot 是给 LLM 看 DOM 用的, script 不需要)                 |
| catfish_browser_screenshot(...)  | (跳过)                                                              |
| catfish_browser_find_by_text(...)| (跳过 — selector 在 trace 里下一个 fill/click 已经带, 不再 find)     |
| catfish_browser_fill(sel, text)  | page.fill(sel, text)  [若 args 有 secret_ref → resolve_secret 调用] |
| catfish_browser_fill(#captcha,T) | T 若来自上一个 recognize_captcha 的 result.text → 改成实时调用       |
| catfish_browser_click(selector)  | page.click(selector)                                                |
| catfish_browser_click(coords=...)| page.mouse.click(x, y)                                              |
| catfish_recognize_captcha(...)   | retry loop {recognize_captcha_sync(...) if conf >= 0.6 break else click(#captchaImg)} |
| catfish_browser_locate(...)      | (跳过 — locate 是视觉定位用的, 留 selector 给后续 click 用)          |

# 数据流依赖解析

最关键: `fill('#captcha', 'cT92')` 里的 'cT92' 是上一个 recognize_captcha
result 的 text. trace 里这俩动作连着 → freeze 引擎识别 → 改成实时调用,
不 hard-code 'cT92' (那是临时验证码值).

# 不让 LLM 介入

凝固过程**纯模板**, 不让 LLM 写代码 — 不然又把不确定性引回来.

# 字段映射规则

  secret_ref 字段 → script.py 用 resolve_secret(...)
  其它字段 → 当 skill 参数 (用户名 / 等), 通过 render_<name>(...) 入参传

# 失败约束

  - trace 里有 ok=false 的步骤 → 不能凝固 (整个 trace 视为失败教学)
  - tool 类型未识别 → 跳过 + warn
  - secret_ref 检测失败 (e.g. 是明文密码 in args.text) → 拒绝凝固, 防把
    明文密码写进 script.py
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import trace_recorder

logger = logging.getLogger("catfish.tool_bridge.skill_freeze")

#: 5/21 方案 1: 教学产物本机路径 (默认 target='local')
from .skill_register import LOCAL_SKILLS_ROOT, ensure_external_dir_registered  # noqa: F401


def _resolve_workspace_skills_root() -> Path | None:
    """catfish 工程 skills 根目录 (git tracked 业务 skill 源码用).

    保留路径主要给业务 skill (leadership-briefing / weekly-report / project-approval)
    用. 教学产物**不**走这条 — 走 _resolve_local_skills_root().
    """
    import os  # noqa: PLC0415
    env_dir = os.environ.get("CATFISH_SKILLS_DIR")
    if env_dir:
        p = Path(env_dir).expanduser()
        if p.is_dir():
            return p
    # 常见路径
    candidates = [
        Path.home() / "person_task" / "catfish" / "skills",
        Path.home() / "catfish" / "skills",
        Path(__file__).resolve().parent.parent.parent.parent.parent / "skills",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _resolve_local_skills_root() -> Path:
    """5/21 方案 1: 教学产物本机路径 ~/.catfish/skills/. 不存在自动创建.

    设计: hermes 通过 config.yaml skills.external_dirs 看到此路径, Curator 不扫.
    """
    LOCAL_SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    return LOCAL_SKILLS_ROOT


# 旧名 alias 防破老 caller (其它模块如 catfish_tools 可能直接 import)
def _resolve_catfish_skills_root() -> Path | None:
    """[DEPRECATED 5/21] 老 caller 仍走 workspace path. 新调用用
    _resolve_local_skills_root() 走教学私有 path.
    """
    return _resolve_workspace_skills_root()


# ─── 模板片段 ────────────────────────────────────────────────────────



# 5/20 拆: _SCRIPT_HEADER / _SCRIPT_FOOTER / _CAPTCHA_RETRY_BLOCK 移到 skill_freeze_template.py
# (跟 _emit_step / _build_skill_md 一起, 它们 internal 引用)


# ─── 数据流依赖解析 ──────────────────────────────────────────────────


# 5/20 拆分: _CAPTCHA_SELECTORS 移到 skill_freeze_template.py



# ============================================================
# Template / sensitive detection helpers (5/20 拆: 333 行抽到 skill_freeze_template.py)
# ============================================================

from .skill_freeze_template import (  # noqa: F401
    _CAPTCHA_RETRY_BLOCK,
    _SCRIPT_FOOTER,
    _SCRIPT_HEADER,
    _build_skill_md,
    _emit_step,
    _format_params_block,
    _infer_params,
    _is_captcha_fill,
    _looks_like_password,
    _quote_str,
    _slugify,
)

# P3.5.128 E1 (6/26 鸿波拍板, 借鉴 Skill-DisCo arxiv 2606.26669 Compilation gate)
from .skill_verify import verify_frozen_script

def freeze_skill(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry: catfish_freeze_skill.

    BL-MM9-FREEZE-v2 (5/12): 只凝固**最近一个完成的 teach session** 的 trace,
    不再按时间窗口模糊取. 凝固前 session 必须 end (catfish_teach_end), 否则拒.

    5/21 方案 1 改动:
      - 加 target 参数: 'local' (默认, 落 ~/.catfish/skills/) 或 'workspace'
        (落 ~/person_task/catfish/skills/, 业务 skill 源码用)
      - run_install 默认改成 False (员工显式才装到 hermes / publish 到 Hub)
      - target='local' 时自动注册到 hermes config.yaml skills.external_dirs
        让 hermes registry 能扫到, 但 Curator 不动 (external_dirs 外)

    args:
      name: str, e.g. "eis-login"
      namespace: str, default "department"
      description: str
      target: 'local' (默认) | 'workspace' — 落盘路径选择 (5/21 加)
      overwrite: bool — 已存在的 skill 是否覆盖. 默认 false
      run_install: bool — 凝固后是否自动跑 install_to_hermes.sh. 默认 False (5/21 改).
                   仅 target='workspace' 时生效 (target='local' 已经在 external_dirs)
      session_archive_path: str (可选, 调试用) — 显式指定某 session archive 凝固
    """
    name = (args.get("name") or "").strip()
    if not name or not re.match(r"^[a-z][a-z0-9\-]*$", name):
        return {
            "ok": False,
            "error": "name 必填, 小写字母 / 数字 / 横线, 必须字母开头 (例 'eis-login')",
        }
    namespace = (args.get("namespace") or "department").strip()
    if namespace not in ("department", "personal", "team"):
        return {
            "ok": False,
            "error": f"namespace 必须是 department / personal / team, 不接受 {namespace!r}",
        }
    description = (args.get("description") or f"凝固 {name} 流程").strip()[:500]
    target = (args.get("target") or "local").strip().lower()
    if target not in ("local", "workspace"):
        return {
            "ok": False,
            "error": f"target 必须是 'local' / 'workspace', 不接受 {target!r}",
        }
    overwrite = bool(args.get("overwrite", False))
    # 5/21 默认改 False — 教学产物默认不进 hermes productivity, 也不自动 publish
    run_install = bool(args.get("run_install", False))

    # v2: 拒绝在 active session 期间凝固 (session 没 end 说明教学没完, 凝固
    # 早了会拿不全步骤)
    active = trace_recorder.get_active_session()
    if active:
        return {
            "ok": False,
            "error": (
                f"当前还有 active teach session ({active.get('name')}, 始于 "
                f"{active.get('started_at_iso')}). 先调 catfish_teach_end "
                f"结束教学, 再凝固."
            ),
        }

    # 1) 读 session trace (优先 args.session_archive_path → fallback last_completed)
    session_path = args.get("session_archive_path")
    if session_path:
        trace = trace_recorder.read_session_traces(session_path, only_ok=True)
        trace_path_used = str(session_path)
        if not trace:
            return {
                "ok": False,
                "error": f"显式 session archive 读不到 ok 步骤: {session_path}",
            }
    else:
        last = trace_recorder.get_last_completed_session()
        if not last or not last.get("archive_path"):
            return {
                "ok": False,
                "error": (
                    "没有 last_completed teach session 可凝固. 应该:\n"
                    "  1. catfish_teach_start(name='" + name + "', description='...')\n"
                    "  2. 员工指挥 LLM 跑教学步骤 (catfish_browser_*)\n"
                    "  3. catfish_teach_end()\n"
                    "  4. catfish_freeze_skill(name='" + name + "')"
                ),
            }
        trace_path_used = last["archive_path"]
        trace = trace_recorder.read_session_traces(trace_path_used, only_ok=True)
        if not trace:
            return {
                "ok": False,
                "error": (
                    f"last_completed session ({last.get('name')}) 里没 ok 步骤. "
                    f"教学时是不是 LLM 没调 catfish_browser_*? archive: {trace_path_used}"
                ),
            }
    # 排序
    trace.sort(key=lambda x: x.get("seq", 0))

    # 2) 解析 + 模板化
    params = _infer_params(trace)
    fn_name = _slugify(name)
    body_lines: list[str] = []
    prev_captcha = None
    # v2.1 (5/12): 标记第一步 goto, 加 chrome 状态预检
    first_goto_emitted = False
    for step in trace:
        is_first_goto = (
            not first_goto_emitted
            and step.get("tool") == "catfish_browser_goto"
        )
        if is_first_goto:
            first_goto_emitted = True
        lines, prev_captcha = _emit_step(step, prev_captcha, is_first_goto=is_first_goto)
        body_lines.extend(lines)

    # 安全检查: body 里如果出现 'refused to bake plaintext password' → 中止
    if any("refused to bake plaintext password" in line for line in body_lines):
        return {
            "ok": False,
            "error": (
                "trace 含明文密码 fill. 拒绝凝固. 教学时改用 "
                "catfish_browser_fill(selector='#pwd', secret_ref='keychain://xxx') "
                "再 freeze."
            ),
        }

    # 3) 落地 — 按 target 切换 root (5/21 方案 1)
    register_result: dict[str, Any] = {"ran": False}
    if target == "local":
        root = _resolve_local_skills_root()
        # target='local' 时自动注册到 hermes external_dirs (幂等)
        register_result = ensure_external_dir_registered(root)
        register_result["ran"] = True
    else:  # target='workspace'
        root = _resolve_workspace_skills_root()
        if root is None:
            return {
                "ok": False,
                "error": (
                    "target='workspace' 找不到 catfish 工程 skills 根目录 "
                    "(设 CATFISH_SKILLS_DIR env). 改 target='local' 落本机 "
                    "~/.catfish/skills/."
                ),
            }
    skill_dir = root / namespace / name
    if skill_dir.exists() and not overwrite:
        return {
            "ok": False,
            "error": (
                f"skill 已存在: {skill_dir}. 用 overwrite=true 覆盖, "
                f"或换 name."
            ),
        }
    skill_dir.mkdir(parents=True, exist_ok=True)

    # P3.5.128 E1: overwrite=true 时, 写入前备份旧 script.py / SKILL.md 到
    # .bak-<ts>/. 治"凝固失败把好 skill 覆盖丢" 的 silent 回归.
    backup_info: dict[str, Any] = {"backed_up": False}
    if overwrite:
        old_script = skill_dir / "script.py"
        old_md = skill_dir / "SKILL.md"
        if old_script.exists() or old_md.exists():
            bak_dir = skill_dir / f".bak-{time.strftime('%Y%m%d_%H%M%S')}"
            bak_dir.mkdir(parents=True, exist_ok=True)
            if old_script.exists():
                old_script.rename(bak_dir / "script.py")
            if old_md.exists():
                old_md.rename(bak_dir / "SKILL.md")
            backup_info = {"backed_up": True, "bak_dir": str(bak_dir)}

    script_py = _SCRIPT_HEADER.format(
        frozen_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        namespace=namespace,
        name=name,
        name_slug=fn_name,
        fn_name=fn_name,
        description=description,
        trace_path=trace_path_used,
        step_count=len(trace),
        params_block=_format_params_block(params),
    ) + "\n".join(body_lines) + _SCRIPT_FOOTER

    (skill_dir / "script.py").write_text(script_py, encoding="utf-8")

    # P3.5.128 E1 verify gate β — script.py 写完先 AST + 白名单 verify, 通过
    # 才让 SKILL.md ship. fail → script.py 重命名为 .unverified-<ts>, SKILL.md
    # 不写 (这样 catfish_run_skill 看不到坏 skill).
    # 注: trace_recorder 已在 module level 第 68 行 import (`from . import trace_recorder`),
    # 这里不要 inline import — Python 会把 trace_recorder 当 local 名字, 之前
    # 第 198 行 trace_recorder.get_active_session() 会 UnboundLocalError.
    verify_result = verify_frozen_script(
        script_py_text=script_py,
        allowed_tools=trace_recorder.RECORDED_TOOLS,
        expected_fn_name=fn_name,
    )
    if not verify_result.ok:
        unverified = skill_dir / f"script.py.unverified-{time.strftime('%Y%m%d_%H%M%S')}"
        (skill_dir / "script.py").rename(unverified)
        return {
            "ok": False,
            "error": (
                "凝固出来的 script.py 没通过 verify gate (P3.5.128 E1, AST + tool "
                "白名单). SKILL.md 不写, 坏 script 已重命名保留供 debug."
            ),
            "verify_errors": verify_result.errors,
            "verify_call_count": verify_result.call_count,
            "unverified_path": str(unverified),
            "skill_dir": str(skill_dir),
            "trace_steps_used": len(trace),
            "backup": backup_info,
        }

    skill_md = _build_skill_md(
        name=name,
        namespace=namespace,
        description=description,
        fn_name=fn_name,
        trace=trace,
        trace_path=trace_path_used,
        step_start=trace[0].get("seq", 0),
        step_end=trace[-1].get("seq", 0),
        ok_count=sum(1 for x in trace if x.get("ok")),
        total_count=len(trace),
        params=params,
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # 4) install_to_hermes — 仅 target='workspace' + run_install=True 时跑
    # 5/21 方案 1: target='local' 已经通过 external_dirs 让 hermes 看到了, 不需要
    # cp 到 ~/.hermes/skills/productivity/catfish-*/. 也不要 default 自动 install
    # (隐私: 教学产物默认本机, 显式才 install/publish).
    install_result: dict[str, Any] = {"ran": False}
    if run_install and target == "workspace":
        install_sh = root / "install_to_hermes.sh"
        if install_sh.exists():
            try:
                proc = subprocess.run(
                    ["bash", str(install_sh)],
                    capture_output=True, text=True, timeout=30,
                )
                install_result = {
                    "ran": True,
                    "returncode": proc.returncode,
                    "stdout_tail": proc.stdout[-500:] if proc.stdout else "",
                    "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
                }
            except Exception as e:
                install_result = {"ran": True, "error": repr(e)}
        else:
            install_result = {"ran": False, "reason": "install_to_hermes.sh 不存在"}
    elif run_install and target == "local":
        # target='local' 不该走 install_to_hermes — 已经通过 external_dirs 注册
        install_result = {
            "ran": False,
            "reason": (
                "target='local' 用 external_dirs, 不需要 cp 到 ~/.hermes/skills/. "
                "想强制装到 hermes productivity 改 target='workspace'."
            ),
        }

    # 5/21 方案 1 summary 区分 target
    if target == "local":
        summary = (
            f"✓ 凝固完成 (target=local, 教学私有路径). {namespace}/{name} → "
            f"{skill_dir}. hermes 通过 skills.external_dirs 自动识别. "
            f"想发布到团队 Hub: 显式调 catfish_skill_publish(skill_path='{skill_dir}', "
            f"namespace='{namespace}') (员工本机点按钮触发, 不要 LLM 自己调)."
        )
    else:  # workspace
        summary = (
            f"✓ 凝固完成 (target=workspace, 业务 skill 工程目录). {namespace}/{name} "
            f"({skill_dir}). 跑 install_to_hermes.sh 同步到 ~/.hermes/skills/"
            f"productivity/catfish-{name}: {'成功' if install_result.get('returncode') == 0 else '未跑或失败'}."
        )

    return {
        "ok": True,
        "name": name,
        "namespace": namespace,
        "target": target,
        "skill_path": f"{namespace}/{name}",
        "skill_dir": str(skill_dir),
        "hermes_name": f"catfish-{name}",
        "files": [
            str(skill_dir / "script.py"),
            str(skill_dir / "SKILL.md"),
        ],
        "trace_steps_used": len(trace),
        "params": [{"name": n, "type": t, "default": d} for n, t, d in params],
        "register_external_dir": register_result,
        "install": install_result,
        # P3.5.128 E1 — verify gate β 通过 + 备份信息 (overwrite=true 时)
        "verify": {
            "ok": True,
            "call_count": verify_result.call_count,
            "gate": "AST + RECORDED_TOOLS whitelist",
        },
        "backup": backup_info,
        "summary": summary,
    }


def freeze_inspect(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry: catfish_freeze_inspect — 查 trace + session 状态.

    v2 输出:
      - active_session: 当前是否有教学进行中, 多少步
      - last_completed: 最近完成的教学 session, archive 路径 + 步骤数
      - active_file: active.jsonl 文件状态
    """
    summary = trace_recorder.session_summary()
    active = summary.get("active_session")
    last = summary.get("last_completed")
    af = summary.get("active_file", {})

    # 如果 active session 在, 列最近 30 步给员工看
    recent_summary: list[dict[str, Any]] = []
    if active:
        trace = trace_recorder.read_traces(only_ok=True)
        recent_summary = [
            {
                "seq": x.get("seq"),
                "tool": x.get("tool"),
                "args_keys": list((x.get("args") or {}).keys()),
                "ts": x.get("ts"),
            }
            for x in trace[-30:]
        ]

    # 友好 summary
    if active:
        s = (
            f"📍 active 教学 session: '{active.get('name')}' (始于 "
            f"{active.get('started_at_iso')}, 已录 {af.get('lines', 0)} 步). "
            f"教学完调 catfish_teach_end 结束."
        )
    elif last:
        s = (
            f"📦 没 active 教学. 最近完成的: '{last.get('name')}' "
            f"({last.get('step_count')} 步, archive={last.get('archive_path')}). "
            f"调 catfish_freeze_skill(name='...') 凝固它."
        )
    else:
        s = (
            "❌ 没 active 教学, 也没 last_completed. 走 catfish_teach_start "
            "开新教学."
        )

    return {
        "ok": True,
        "active_session": active,
        "last_completed": last,
        "active_file": af,
        "recent_steps_summary": recent_summary,
        "summary": s,
    }


def teach_start(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry: catfish_teach_start — 开始一次教学 session.

    BL-MM9-FREEZE-v2 (5/12): 没 active session 时 catfish_browser_* 调用**不录**.
    必须显式 start 才开始录. 教学跟复用/探索物理隔离.

    args:
      name: skill 名 (例 'eis-login'). 用于命名归档文件 + 凝固时识别.
      description: 简介, 给 LLM 看的提示
    """
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    state = trace_recorder.start_session(name=name, description=description)
    return {
        "ok": True,
        "session_id": state.get("session_id"),
        "name": state.get("name"),
        "started_at_iso": state.get("started_at_iso"),
        "summary": (
            f"📍 教学开始: '{state.get('name')}'. 接下来你调的每个 "
            f"catfish_browser_* / catfish_recognize_captcha / catfish_browser_locate "
            f"都会被录, 教学完调 catfish_teach_end. **教学期间不要做无关探索** — "
            f"每个 tool call 都会进最终 skill."
        ),
    }


def teach_end(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry: catfish_teach_end — 结束教学 session, 归档 trace.

    args:
      reason: 可选, 进归档元信息. 默认 'manual'
    """
    reason = (args.get("reason") or "manual").strip()
    info = trace_recorder.end_session(reason=reason)
    if not info.get("ok"):
        return info
    return {
        **info,
        "summary": (
            f"✓ 教学结束: '{info.get('name')}' ({info.get('step_count')} 步, "
            f"{info.get('duration_s')}s). archive={info.get('archive_path')}. "
            f"下一步: catfish_freeze_skill(name='{info.get('name')}', "
            f"namespace='department', description='...') 凝固."
        ),
    }


# 兼容 v1 调用 (catfish_freeze_rotate). v2 不应该用了, 但保留兼容.
def freeze_rotate(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry: catfish_freeze_rotate (v1 legacy).

    v2 下用 catfish_teach_end 替代. 留兼容: 如果 active session 在就 end_session,
    否则 rotate active.jsonl.
    """
    if trace_recorder.is_session_active():
        return teach_end({"reason": args.get("reason") or "rotate-legacy"})
    reason = (args.get("reason") or "post-freeze").strip()
    archive = trace_recorder.rotate(reason=reason)
    return {
        "ok": bool(archive),
        "archived_to": archive,
        "summary": (
            f"trace 已 rotate 到 {archive}." if archive
            else "trace 为空, 没东西 rotate."
        ),
    }


__all__ = [
    "freeze_skill",
    "freeze_inspect",
    "teach_start",
    "teach_end",
    "freeze_rotate",
]
