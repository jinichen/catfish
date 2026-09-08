"""P3.5.42 (6/18 鸿波 拍 '所有遵循 picker, 不乱改, 这事原则') — memory_update 误写 enforce.

# 鸿波 catch 真因

5/24 BL-MEMORY-DISCIPLINE catfish-memory plugin 已在 system prompt 注入"5 kind router
决策树 + 自查 4 反例", 但鸿波 6/18 实证截图: MEMORY.md 7.1 KB 内 LLM 仍把
'陈淡孜 6/19 飞抵福州' 这种**单次事件**写到 project_fact, 应该是 journal.

5/24 时 hermes 端无 pre_tool_call hook, 只能靠 prompt 自查 (~70% 命中). 6/18 hermes
上游已加 pre_tool_call block hook (hermes-agent/website/docs/user-guide/features/
hooks.md:398-422), **现在能 enforce**.

# 设计原则

1. **不硬编码词表** (鸿波 catch '今后变化是灾难') — 走 LLM 二次校验, 0 词表
2. **所有遵循 picker** (鸿波 6/18 拍 '原则') — model 走 picker_state.json
   chain (跟 catfish-memory `_get_summarize_model` 同模式), 不另搞配置
3. **fail-silent** — LLM 调挂 / 模型未装 → 放行, 不阻塞 LLM workflow
4. **复用现有 schema** — prompt 用 catfish-memory `_render_memory_schema` 的 5 kind router 教学
5. **audit log 永久** — 命中/放行都写 `~/.catfish/memory_audit.jsonl`, 给员工周扫

# 工作流

LLM 调 `memory(action=add, target=memory, content=X)` →
  hermes fire pre_tool_call hook →
    memory_enforce_hook 拦截 →
      复用 picker chain 拿 verifier model →
        gateway /v1/chat/completions classify →
          LLM 返 {"route": "memory"|"user"|"journal"|"todo"|"skill", "reason": "..."}
            route == target → 放行 + audit log allowed
            route in (memory/user) 但 != target → block + 建议改 target
            route 是 journal/todo/skill → block + 引导改调对应 tool
          调挂 / 异常 → 放行 (fail-silent) + audit log error

# 死循环防范

memory_enforce_hook 内 fetch gateway → gateway 调上游 LLM → 返结果. hermes
pre_tool_call hook 只 fire on tool_call (LLM 进 tool 调用阶段), gateway HTTP fetch
不是 tool_call, 不触发. 0 循环风险.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────

_AUDIT_LOG_FILENAME = "memory_audit.jsonl"
_LLM_HTTP_TIMEOUT_SECS = 30.0
# 鸿波拍 (6/18 '所有遵循 picker, 不乱改'): model 选 chain 严格走 picker, 不读 role_resolver
# 的 rate_fast / summarize (yaml 默认配公网 catfish-public-qwen-flash / catfish-public-
# gemini-pro, 走公网 = 员工真实数据出端 = 破 P3.5.27 红线). 用 chat_default 是**员工桌面
# chat 默认对齐** — 员工日常主力选啥这里就用啥.
#
# ⚠ 7/22 鸿波 (P3.5.79+ 军规): **杀了 P3 硬编 _FALLBACK_MODEL = "catfish-private-main"**.
# 老逻辑 picker 空 + roles.yaml chat_default 空 → 默默回 catfish-private-main, 员工无感
# (且 roles.yaml chat_default 现值已改公网 deepseek, 老兜底"红线一致"自欺). fail-loud
# 才是正解: 没设 picker + 没配 yaml → get_verifier_model 返 None, memory write 直接 block,
# 员工看到明确 error 逼他去设 picker / IT 去配 chat_default. **不硬编就不混乱**.
_FALLBACK_ROLE = "chat_default"


# ── picker chain (复用 catfish-memory plugin 模式, 不 cross-import) ────


def _catfish_home() -> Path:
    """~/.catfish/ 或 env CATFISH_HOME (跟 catfish-memory `_catfish_home` 对齐)."""
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


def _read_picker_state_model(catfish_home: Path) -> str:
    """读 ~/.catfish/picker_state.json → chat_model 字段.

    跟 catfish_memory_helpers._read_picker_state_model 同实现, 抄不 import 防 plugin
    跨 import 链断风险 (5/16 BL-MEMORY-PLUMBING 撞过).
    fail-silent: 文件缺 / parse 错 / 字段缺 → 空字符串. caller 走 fallback.
    """
    path = catfish_home / "picker_state.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            model = data.get("chat_model", "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("memory_enforce: read picker_state.json 失败: %s", e)
    return ""


def _resolve_role_via_gateway(role: str) -> str:
    """走 gateway /v1/roles 拿 role → model. 跟 catfish-memory role_resolver 同模式.

    fail-silent: httpx 没装 / gateway 挂 / 网络抖 → 空字符串.
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return ""
    gateway_url = os.environ.get("CATFISH_GATEWAY_URL", "http://127.0.0.1:8999")
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{gateway_url}/v1/roles")
            if resp.status_code != 200:
                return ""
            data = resp.json()
            roles = data.get("roles", {}) if isinstance(data, dict) else {}
            model = roles.get(role, "")
            if isinstance(model, str) and model.strip():
                return model.strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("memory_enforce: role_resolver 拉失败: %s", e)
    return ""


def get_verifier_model() -> Optional[str]:
    """picker chain — 严格遵循 picker (鸿波 6/18 原则 '所有遵循 picker, 不乱改').

    优先级:
      1. ~/.catfish/picker_state.json (员工 chat picker 选定, 最高优先)
      2. role_resolver(chat_default) (员工桌面 chat 默认对齐)
      → 都空返 None. **军规: 不硬编 model 兜底**. caller 负责 fail-closed
        (block memory write + 明确 error msg 逼员工去设 picker).

    不走 rate_fast / summarize 因为它们 yaml 默认配公网 model (catfish-public-qwen-flash /
    catfish-public-gemini-pro), 内容是员工真实数据走公网破 P3.5.27 数据零出端红线.

    ⚠ 7/22 鸿波 (P3.5.79+ 军规): 杀了老 P3 兜底 "return catfish-private-main". 老逻辑
    picker 空 + roles.yaml chat_default 空 → 静默回 catfish-private-main, 员工无感 + 掩盖
    真错. 现在 fail-loud: 返 None, caller block memory write, 员工必须去设 picker 或
    IT 去配 chat_default. 不硬编就不混乱.
    """
    home = _catfish_home()
    # P1: picker_state.json
    picker = _read_picker_state_model(home)
    if picker:
        return picker
    # P2: role_resolver(chat_default)
    role_model = _resolve_role_via_gateway(_FALLBACK_ROLE)
    if role_model:
        return role_model
    # P3: 军规 fail-loud — 不硬编兜底
    return None


# ── classify prompt (复用 catfish-memory 5 kind router 决策树) ────────

_CLASSIFY_SYSTEM_PROMPT = (
    "你是 catfish memory 路由审核员. 下面是 LLM 想写到 hermes 持久化记忆的一条 content. "
    "用 catfish 5 kind 决策树判定它该写到哪.\n\n"
    "## 决策树\n\n"
    "| kind | 路由 | 该存什么 |\n"
    "|---|---|---|\n"
    "| `memory` | ~/.hermes/memories/MEMORY.md | **项目/技术常量** — 一年后还成立 (例: ISO 27001 流程, "
    "API 字段约定, 客户机房 IP, 资质评估流程) |\n"
    "| `user` | ~/.hermes/memories/USER.md | **员工本人** — 身份/偏好/习惯/昵称/关系 (例: 喜欢直接输出, "
    "本名鸿波, 不喜欢确认) |\n"
    "| `journal` | ~/.catfish/employee_journal.md | **单次事件 / 已发生 / 含具体日期的状态变更** "
    "(例: 陈某 6/19 飞抵福州, X 会议结束, Y 已完成) |\n"
    "| `todo` | catfish_create_task | **用户行动** (例: 9 月底前提交 X 报告, "
    "周三前回复 Y) |\n"
    "| `skill` | ~/.hermes/skills/<name>/SKILL.md | **多步流程 / 操作指引** (例: 如何申报资质, "
    "如何跑测试) |\n\n"
    "## 判定原则\n"
    "- memory: 跨 session 稳定 + 没 deadline + 不是 skill workflow\n"
    "- 含具体日期 (X 月 X 日 / 飞抵 / 已完成) → 大概率 journal\n"
    "- 含 deadline (之前/之后/截止) → todo\n"
    "- 拿不准 → 80% 概率 journal, 不是 memory\n\n"
    "## 输出格式 (严格 JSON, 不要 markdown 包裹)\n"
    "{\"route\": \"memory\"|\"user\"|\"journal\"|\"todo\"|\"skill\", "
    "\"reason\": \"< 30 字短解释\", \"confidence\": 0.0-1.0}"
)


def _read_hermes_env_key(key: str) -> str:
    """读 hermes 管理的 env key. 双查 os.environ + ~/.hermes/.env 文件.

    BL-PLUGIN-AUTH-FIX (7/27 鸿波): 为什么必须双查 —
      hermes `hermes_cli/config.py:load_env()` 只**返回 dict 不写 os.environ**
      (写 os.environ 只发生在 `/reload` 命令或 set_env_value). plugin 光
      os.environ.get() 可能拿不到. hermes 自己的 `get_env_value():8186` 就是双查,
      本函数语义跟它对齐. 不 import hermes_cli.config (跨版本易断).

    parse 规则跟 Companion `dream.rs:read_hermes_dev_env()` 对齐.
    """
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        env_path = Path(os.path.expanduser("~")) / ".hermes" / ".env"
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith(f"{key}="):
                continue
            raw = line[len(key) + 1:].strip()
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
                raw = raw[1:-1]
            return raw.strip()
    except OSError as e:
        logger.debug("memory_enforce 读 ~/.hermes/.env 失败 (%s): %s", key, e)
    return ""


def _gateway_auth_token() -> str:
    """拿调 gateway 的鉴权 token.

    BL-PLUGIN-AUTH-FIX (7/27 鸿波): 老实现只读 CATFISH_INTERNAL_DEV_TOKEN — 那是
    **gateway 进程内 loopback 专用** (central/llm-gateway/.../auth/dev_token.py:11-15
    "启动时随机生成, 进程内存, 重启即变, 不写 .env"). 本 plugin 跑 **hermes 进程**
    (另一进程 · 生产还跨机), 永远拿不到 → 老代码 `if token:` 为假就不带
    Authorization 发出去 → gateway 401 → enforce 静默降级.

    正解 · 复用 **hermes → gateway 这一跳**的凭证 = .env OPENAI_API_KEY
    (aud=catfish-gateway · token_use=service · hermes-cli client_credentials 派发 ·
    refresh-jwt-hermes-env.sh 30 天刷新 · 生产分离时中央派发天然跨机).

    链路: Companion ─[API_SERVER_KEY]→ hermes:8642 ─[OPENAI_API_KEY]→ gateway:8999
                                          └── 本 plugin (in-process, 复用第 2 跳)

    优先级: 1. OPENAI_API_KEY  2. CATFISH_INTERNAL_DEV_TOKEN (本机 dev 兜底)
    """
    return (
        _read_hermes_env_key("OPENAI_API_KEY")
        or _read_hermes_env_key("CATFISH_INTERNAL_DEV_TOKEN")
    )


def _classify_memory_route_diag(content: str, model: str) -> tuple[Optional[dict], str]:
    """调 gateway /v1/chat/completions 让 LLM 判定 route. 返 (result, error_str).

    P3.5.42.5 (鸿波 6/20 catch '是不是代码有问题'): 拆出 diag 版本让 audit 脚本
    能拿到真错. hook 路径仍调 _classify_memory_route 包 fail-silent (不阻 LLM).

    返 ((dict | None), error_str):
      - 成功: (dict, "")
      - 失败: (None, "<具体错因>")

    response_format: json_object 强制 JSON. temperature 0 减少 LLM 自由发挥.
    """
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return None, "httpx 没装 (catfish-tool-bridge 应自带)"

    gateway_url = _read_hermes_env_key("CATFISH_GATEWAY_URL") or "http://127.0.0.1:8999"
    token = _gateway_auth_token()
    headers = {
        "Content-Type": "application/json",
        # 标记内部 enforce 流量 (gateway 可识别, audit 区分常规 LLM call)
        "X-Catfish-Memory-Enforce": "1",
        "X-Catfish-Skip-Identity": "true",
        "X-Catfish-Internal": "true",
    }
    if not token:
        # BL-PLUGIN-AUTH-FIX (7/27 鸿波): 老代码 `if token:` 为假时**连 Authorization
        # 都不带**就发出去 → gateway 401 → 静默降级, 员工永远不知道 enforce 没在跑.
        # 跟本文件 7/22 军规 ("不硬编就不混乱, fail-loud 才是正解") 一致: 报清楚.
        # 只记日志不 block (本 enforce 设计原则 3 是 fail-silent — LLM 调挂放行).
        logger.warning(
            "catfish-xcatfish-user memory_enforce: 拿不到 gateway 鉴权 token, "
            "LLM 二次校验 skip (放行 memory write). 查顺序 "
            "① ~/.hermes/.env OPENAI_API_KEY (hermes→gateway service token, 跑 "
            "central/llm-gateway/refresh-jwt-hermes-env.sh 刷新) "
            "② env/.env CATFISH_INTERNAL_DEV_TOKEN"
        )
        return None, "拿不到 gateway 鉴权 token (见 log)"
    headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=_LLM_HTTP_TIMEOUT_SECS) as client:
            # 8/15 晚: 打归属标记。这条 classify 每轮对话都可能跑, 之前落在账本的
            # 「(无标记)」栏里 (占 40%)。名字用 plugin: 前缀 —— 网关
            # metrics.py:274 的注释 5/17 就定了这个约定, 只是没人实现。
            resp = client.post(
                f"{gateway_url}/v1/chat/completions?catfish_source=plugin:memory-classify",
                               json=payload, headers=headers)
            if resp.status_code != 200:
                body_preview = resp.text[:300] if resp.text else "<empty>"
                return None, f"gateway HTTP {resp.status_code}: {body_preview}"
            try:
                data = resp.json()
            except (ValueError, json.JSONDecodeError) as e:
                return None, f"gateway 返非 JSON: {type(e).__name__}: {str(e)[:100]}"
            raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not isinstance(raw, str) or not raw.strip():
                return None, f"LLM 返空 content. response={str(data)[:200]}"
            try:
                parsed = json.loads(raw.strip())
            except (ValueError, json.JSONDecodeError) as e:
                return None, f"LLM 返非 JSON (response_format 没生效?): {raw[:150]}"
            route = parsed.get("route", "").strip().lower()
            if route not in ("memory", "user", "journal", "todo", "skill"):
                return None, f"route 字段无效: {parsed.get('route')!r} (该是 memory/user/journal/todo/skill)"
            return {
                "route": route,
                "reason": str(parsed.get("reason", ""))[:200],
                "confidence": float(parsed.get("confidence", 0.5)),
            }, ""
    except Exception as e:  # noqa: BLE001
        return None, f"HTTP 异常: {type(e).__name__}: {str(e)[:200]}"


def _classify_memory_route(content: str, model: str) -> Optional[dict]:
    """fail-silent 包装 — hook 路径用. 真错全吞返 None.

    audit 脚本应调 _classify_memory_route_diag 看具体错.
    """
    result, error = _classify_memory_route_diag(content, model)
    if error:
        logger.debug("memory_enforce: classify 失败 (fallback allow): %s", error)
    return result


# ── audit log ────────────────────────────────────────────────────


def _audit_log(entry: dict) -> None:
    """append ~/.catfish/memory_audit.jsonl. fail-silent (审计挂不阻塞 enforce)."""
    home = _catfish_home()
    path = home / _AUDIT_LOG_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("memory_enforce: audit log 写失败 (silent): %s", e)


# ── 主 hook ──────────────────────────────────────────────────────


def memory_enforce_hook(tool_name: str = "", args: Optional[dict] = None,
                        task_id: str = "", **kwargs: Any) -> Optional[dict]:
    """hermes pre_tool_call hook — 拦 memory_update 误写.

    返:
      - None → 放行
      - {"action": "block", "message": ...} → block + LLM 拿到 message 自动重试

    callable from hermes pre_tool_call dispatcher. fail-silent: 出错放行不阻 LLM.
    """
    try:
        if tool_name != "memory":
            return None
        if not isinstance(args, dict):
            return None
        action = args.get("action", "")
        if action not in ("add", "replace"):
            return None
        target = args.get("target", "")
        if target not in ("memory", "user"):
            return None
        content = args.get("content", "")
        if not isinstance(content, str) or not content.strip():
            return None

        # 调 LLM 二次校验
        model = get_verifier_model()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        # 军规 (P3.5.79+ 7/22 鸿波): model 未解出 → fail-closed block, 不再硬编
        # catfish-private-main 兜底. 员工必须去 Companion 里选 chat_model, 或让 IT
        # 配 roles.yaml chat_default. 硬编兜底掩盖真错, 让人查半天.
        if not model:
            _audit_log({
                "ts": now_iso,
                "tool": tool_name,
                "target": target,
                "action": action,
                "content_preview": content[:100],
                "model": None,
                "decision": "block_no_model",
                "reason": "picker_state.json 空 + roles.yaml chat_default 空",
            })
            return {
                "action": "block",
                "message": (
                    "memory write 挂 · verify model 未设:\n"
                    "  ① ~/.catfish/picker_state.json 空 — 请在 Companion 里选 chat_model\n"
                    "  ② gateway roles.yaml chat_default 空 — 请让 IT 补配\n"
                    "军规: 不硬编 model 兜底. 二选一设完再试."
                ),
            }

        classification = _classify_memory_route(content, model)

        if classification is None:
            # fail-silent: classify 挂 → 放行 + audit log error
            _audit_log({
                "ts": now_iso,
                "tool": tool_name,
                "target": target,
                "action": action,
                "content_preview": content[:100],
                "model": model,
                "decision": "allow_fallback",
                "reason": "classify 失败 (LLM 调挂 / 模型未装 / JSON parse 错), 放行不阻塞",
            })
            return None

        route = classification["route"]
        reason = classification["reason"]
        confidence = classification["confidence"]

        # route 跟 target 匹配 → 放行
        if route == target:
            _audit_log({
                "ts": now_iso,
                "tool": tool_name,
                "target": target,
                "action": action,
                "content_preview": content[:100],
                "model": model,
                "decision": "allow",
                "llm_route": route,
                "llm_reason": reason,
                "confidence": confidence,
            })
            return None

        # route in (memory, user) 但 != target → block + 建议改 target
        if route in ("memory", "user"):
            _audit_log({
                "ts": now_iso,
                "tool": tool_name,
                "target": target,
                "action": action,
                "content_preview": content[:100],
                "model": model,
                "decision": "block_target_mismatch",
                "llm_route": route,
                "llm_reason": reason,
                "confidence": confidence,
            })
            return {
                "action": "block",
                "message": (
                    f"⚠️ catfish memory_enforce: 这条该写 {route.upper()}.md, 你传了 target={target}. "
                    f"reason: {reason}. 改 target={route} 重试."
                ),
            }

        # route 是 journal / todo / skill → block + 引导改调对应 tool
        _audit_log({
            "ts": now_iso,
            "tool": tool_name,
            "target": target,
            "action": action,
            "content_preview": content[:100],
            "model": model,
            "decision": "block_wrong_kind",
            "llm_route": route,
            "llm_reason": reason,
            "confidence": confidence,
        })
        kind_to_tool = {
            "journal": "直接写 ~/.catfish/employee_journal.md (append `## [YYYY-MM-DD HH:MM] kind | title`)",
            "todo": "调 catfish_create_task tool (写入本机任务库)",
            "skill": "调 catfish_propose_skill tool (多步流程 / SKILL.md)",
        }
        suggested = kind_to_tool.get(route, route)
        return {
            "action": "block",
            "message": (
                f"⚠️ catfish memory_enforce: 这条疑似 {route}, 不是 project_fact. "
                f"reason: {reason}. 改调对应 tool: {suggested}."
            ),
        }
    except Exception as e:  # noqa: BLE001
        # 兜底 fail-silent — hook 异常绝不阻 LLM
        logger.warning("memory_enforce_hook 异常 (放行兜底): %s", e, exc_info=True)
        return None
