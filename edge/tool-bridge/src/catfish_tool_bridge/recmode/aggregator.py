"""BL-LEARN-RECMODE / aggregator (5/14 v0 骨架, 5/25 搬到 edge/tool-bridge).

# 5/25 鸿波拍板 BL-RECMODE-MIGRATE-TO-EDGE: 本模块从 central/llm-gateway/.../recmode/
# 整体搬这里. 中央代码 (`central/`) 不再读 / 写 ~/.catfish/recordings/ ~/.catfish/skills/.
# 详见 ./__init__.py 顶部注释 + docs/CENTRAL-EDGE-DATA-BOUNDARY.md E.1.
# gateway /api/learn/analyze 现在是 thin proxy, 通过 Unix socket 转过来.

读一个 RecMode session 的输入 (events JSONL + 语音转写 JSONL + keyframe 截图) →
构造 multipart messages → 调 catfish-private-main → 解析 JSON 输出 → 落 SKILL.md
+ main.py 到 `~/.catfish/skills/<namespace>/<skill_name>/`.

V1 假设 (5/14 鸿波拍板): catfish-private-main (Qwen3.5 122B MoE A10B) `supports_vision: true`,
5/8 鸿波实测 EIS 截图识别准确, 中文 UI 识别能力够. 不再做 V1 mock 验证, 直接走主路径.

设计文档: docs/LEARN-RECMODE-DESIGN.md §5 (main 模型综合 prompt 模板)

Pipeline:
1. 读 session_dir/events.jsonl → list[event]
2. 读 session_dir/transcripts.jsonl → list[transcript with ts]  (whisper.cpp 输出)
3. 读 session_dir/screenshots/*.png → list[base64 PNG]
4. 构造 messages = system (skill-author 角色 + JSON schema) + user (含 events 表 + 语音表 + 截图 multipart)
5. POST /v1/chat/completions catfish-private-main (走自己 gateway, 复用 RBAC + quota)
6. 解析 response 为 SKILL JSON
7. 渲染 SKILL.md 人话版 + main.py 可跑代码
8. 落档 ~/.catfish/skills/<namespace>/<name>/

5/14 v0: 骨架 + prompt 模板, 不真调 LLM. 5/26 sprint 真做时:
- 真发 HTTP 到 gateway /v1/chat/completions
- 错误处理 (token 超限 / JSON 解析挂 / selector_hint 校验)
- preview UI 集成 (Companion 显 SKILL.md + "跑一次试" 按钮)
"""
from __future__ import annotations

import base64
import json
import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("catfish.recmode.aggregator")


# ─── 系统 prompt 模板 (跟 docs/LEARN-RECMODE-DESIGN.md §5 对齐) ────────


SYSTEM_PROMPT = """你是 catfish-skill-author. 用户刚录了一段教学过程
(events + 语音 + 截图). 你的任务: 综合理解用户在教什么流程,
输出 hermes 兼容的 SKILL.md + script.py 代码.

# 输出 schema (严格 JSON, 不要任何前后文 prose)

{
  "skill_name": "<kebab-case 名字, e.g. weekly-report / catfish-email-digest>",
  "namespace": "<department / personal / public / creative 选一>",
  "kind": "<procedural / instructional 选一; 多步操作流程选 procedural, 解释/教学/参考选 instructional>",
  "description": "<一句话定位 + 触发场景, ≤158 tokens (utf8 bytes/4). 给 LLM 系统 prompt 注入用>",
  "triggers": ["<触发关键词 1>", "<触发关键词 2>", "<…>"],
  "intent_summary": "<3-5 句话用户意图描述, 放 SKILL.md body>",
  "params_schema": [
    {"name": "...", "type": "string|integer|number|boolean", "default": ..., "description": "..."}
  ],
  "steps": [
    {
      "step_no": 1,
      "intent": "<人话: 这一步在干嘛>",
      "tool": "<catfish_browser_navigate / find_by_text / click / execute_code>",
      "args_template": {<tool 参数, 用 {param} 引用 params_schema>},
      "selector_hint": {
        "text": "<click 元素的可见文本>",
        "near_text": "<旁边文字, disambiguation>",
        "role": "<button/tab/menuitem/link/...>"
      },
      "expected_after": "<这步跑完应该看到啥, 给截图对比>"
    }
  ],
  "execute_code_segment": "<最后数据提取 + 输出: Python 代码字符串, 含 BeautifulSoup 解析表 + 翻页 + 数据 diff>",
  "output_schema": {<skill 跑完返给 LLM 的字段>},
  "confidence": <0-1 你对 skill 自动跑成功的把握>,
  "questions_for_user": ["<不确定 / 需用户 confirm 的点>"]
}

# 关键纪律 (违反这些 skill 会跑废)

1. **selector 不要硬编码** — 用 selector_hint.text + near_text + role,
   skill 跑时调 catfish_browser_find_by_text 实时找 (DOM 改了能跟上).
2. **数据提取走 catfish_execute_code + BeautifulSoup** — 不要硬编码
   ".table tr td:nth-child(3)" 这种 brittle CSS selector.
3. **每步加 expected_after** — skill 跑时截图对比, 视觉差异大就报错.
4. **params_schema 要通用化** — 不要硬编码用户名 / 数值阈值, 让 skill 通用.
5. **steps 必含至少一步 LLM-only "返结果"** — 不绑死下游动作 (e.g. 不在 skill 里
   循环创建日历事件, 留给 LLM 看结果决定 dedup).
6. **看不全就直接写 questions_for_user** — 别瞎猜, 不确定列出来给用户 confirm.

7. **skill_name kebab-case** (P3.5.43 新): `weekly-report` 对, `weekly_report` /
   `WeeklyReport` 错. 跟 hermes 现有 SKILL 仓库一致 (e.g. weekly-report,
   guizang-ppt-magazine, huashu-design).

8. **triggers 3-20 个** (P3.5.43 新): hermes 加载 SKILL 时把 triggers 注入 system
   prompt, LLM 看到员工说这些词就调 skill. 真实场景例:
   - 周报类 → ["周报", "本周工作", "本周总结", "一周工作", "写周报", "weekly report"]
   - 邮件类 → ["邮件", "回复邮件", "邮件总结", "email"]
   太少 (<3) 漏触发, 太多 (>20) 占预算 — 严守 3-20.

9. **description ≤158 tokens** (P3.5.43 新, P3.5.41.1 实测安全线):
   utf8_bytes/4 ≤158. 中文约 200 字, 英文约 600 字符. 超 budget 会撞 system
   prompt 总预算 (P3.5.32.5 advisor 超时实证). description 只放"何时调 + 一句话
   定位", 详细 spec / 例子放 intent_summary / steps body 段.

10. **🇨🇳 所有自然语言字符串必须用中文** (BL-RECMODE-ZH-OUTPUT, 5/27 鸿波):
   - `description` / `intent_summary` / `steps[].intent` / `steps[].expected_after` /
     `questions_for_user` 一律用简体中文写
   - **不要英文**, 也**不要中英混杂** ("用户 demonstrate ..." / "Browse the homepage to ..."
     全错). 用户是中文母语员工, skill 给非技术同事看
   - 例: `description: "浏览智慧学习平台首页, 查看推荐课程和热门专区"`
     而**不是**: `description: "Browse the AI learning platform homepage to view recommended courses and hot zones"`
   - 唯一允许英文的字段: `skill_name` (snake_case), `namespace`, `tool` (catfish_browser_*),
     `args_template` 里的 selector / key 名, `output_schema` 里的 JSON key.
     **value 仍然中文** — 比如 `output_schema: {"课程标题": "string"}` 才对, `{"title": "string"}` 不对.
"""


# ─── data class ────────────────────────────────────────────


@dataclass
class RecordingInputs:
    """一次 RecMode session 的输入数据 (从 session_dir 读出来)."""
    session_id: str
    session_dir: Path
    events: list[dict]
    transcripts: list[dict]
    screenshots: list[tuple[str, bytes]]  # [(keyframe_id, png_bytes), ...]


@dataclass
class SkillOutput:
    """LLM 综合后落档的 skill (parse 自 LLM JSON).

    P3.5.43 加 triggers + kind — hermes 加载 SKILL 时 frontmatter 必填.
    skill_name 改 kebab-case (跟 hermes 现有 SKILL 仓库一致).
    """
    skill_name: str  # P3.5.43: kebab-case (旧 snake_case, 装机时 hermes 仓库 kebab)
    namespace: str
    kind: str  # P3.5.43 新: procedural / instructional
    description: str  # P3.5.43: ≤158 tokens budget (LLM prompt 已约束)
    triggers: list[str]  # P3.5.43 新: 3-20 个触发关键词
    intent_summary: str
    params_schema: list[dict]
    steps: list[dict]
    execute_code_segment: str
    output_schema: dict
    confidence: float
    questions_for_user: list[str]
    raw_json: dict  # 原始 LLM 输出 JSON, 供调试

    def to_manifest(self) -> "SkillManifest":
        """转 SkillManifest (skill_format 模块) — render_skill_md / render_script_py
        都吃 manifest."""
        from .skill_format import SkillManifest  # noqa: PLC0415 (循环依赖防护)
        return SkillManifest(
            name=self.skill_name,
            namespace=self.namespace,
            kind=self.kind,
            description=self.description,
            triggers=self.triggers,
            intent_summary=self.intent_summary,
            params_schema=self.params_schema,
            steps=self.steps,
            execute_code_segment=self.execute_code_segment,
            output_schema=self.output_schema,
            questions_for_user=self.questions_for_user,
            author="鲶鱼 RecMode",
        )


# ─── 读输入 ────────────────────────────────────────────────


def load_recording_inputs(session_dir: Path) -> RecordingInputs:
    """读 events / transcripts / keyframes — 给 LLM 喂的 3 类数据."""
    if not session_dir.exists():
        raise FileNotFoundError(f"session dir 不存在: {session_dir}")

    events = _load_jsonl(session_dir / "events.jsonl")
    transcripts = _load_jsonl(session_dir / "transcripts.jsonl")  # 5/26 真做接 BL-VOICE3 输出

    screenshots = []
    ss_dir = session_dir / "screenshots"
    if ss_dir.exists():
        for png in sorted(ss_dir.glob("kf_*.png")):
            kf_id = png.stem
            screenshots.append((kf_id, png.read_bytes()))

    session_id = session_dir.name
    return RecordingInputs(
        session_id=session_id,
        session_dir=session_dir,
        events=events,
        transcripts=transcripts,
        screenshots=screenshots,
    )


def _load_jsonl(path: Path) -> list[dict]:
    """通用 jsonl 读取, 跳坏行不致命."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("aggregator: 跳坏 jsonl 行 in %s: %r", path.name, line[:80])
    return out


# ─── 构造 messages ──────────────────────────────────────────


def build_messages(inputs: RecordingInputs) -> list[dict]:
    """把 events + transcripts + keyframes 组装成 OpenAI multipart messages.

    System: SYSTEM_PROMPT (含 schema + 纪律)
    User: 含 3 段文字 (events 表 / 语音表 / intent 总结) + N 张截图 multipart
    """
    events_table = _format_events_table(inputs.events)
    transcripts_table = _format_transcripts_table(inputs.transcripts)

    user_parts: list[dict] = []
    user_parts.append({
        "type": "text",
        "text": textwrap.dedent(f"""\
            # 录屏数据 (session_id: {inputs.session_id})

            ## events 序列 (按 ts 升序, 共 {len(inputs.events)} 条)
            {events_table}

            ## 语音转写 (按 ts 对齐 events, 共 {len(inputs.transcripts)} 段)
            {transcripts_table}

            ## 截图 (按 keyframe_id 升序, 共 {len(inputs.screenshots)} 张)

            (下面附 multipart image, 按顺序对应 keyframe_001 / kf_002 / ...)

            # 任务

            综合上面 events + 语音 + 截图, 输出 SKILL.md + main.py 的 JSON.
            严格按 system prompt 里的 schema, 不要 prose 不要 markdown 围栏.

            **重要**: 所有自然语言描述字段 (description / intent_summary /
            steps[].intent / steps[].expected_after / questions_for_user) 一律
            **用简体中文**. 不要英文, 不要中英混杂. 例:
              ✓ `"description": "浏览学习平台首页, 查看推荐课程"`
              ✗ `"description": "Browse the homepage to view courses"`
              ✗ `"description": "Browse 首页 to view 课程"`
        """)
    })
    for kf_id, png_bytes in inputs.screenshots:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        user_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_parts},
    ]


def _format_events_table(events: list[dict]) -> str:
    """events JSONL → markdown 表格 (LLM 看得懂)."""
    if not events:
        return "(空)"
    rows = ["| ts | kind | content | screenshot |", "|---|---|---|---|"]
    for e in events:
        ts = e.get("ts", "?")
        kind = e.get("kind", "?")
        # 摘要 content (排除已经在列里的 ts/kind/screenshot_id)
        content = {k: v for k, v in e.items() if k not in {"ts", "kind", "screenshot_id"}}
        ss = e.get("screenshot_id", "")
        content_str = json.dumps(content, ensure_ascii=False)[:100]
        rows.append(f"| {ts} | {kind} | {content_str} | {ss} |")
    return "\n".join(rows)


def _format_transcripts_table(transcripts: list[dict]) -> str:
    """语音转写 JSONL → markdown 表格."""
    if not transcripts:
        return "(空 — 用户没说话)"
    rows = ["| ts | text |", "|---|---|"]
    for t in transcripts:
        ts = t.get("ts", "?")
        text = t.get("text", "").replace("|", "/")
        rows.append(f"| {ts} | {text} |")
    return "\n".join(rows)


# ─── 调 LLM (5/14 v0 占位) ─────────────────────────────────


async def call_llm(
    messages: list[dict],
    model: str | None = None,
    *,
    gateway_url: str | None = None,
    auth_token: str | None = None,
    effective_user: str | None = None,
    timeout_s: float = 300.0,
    temperature: float = 0.3,  # RecMode 综合要稳, 不要太创造
    max_tokens: int = 8000,
) -> str:
    """调主力 chat 综合录屏 → 输出 JSON 字符串 (caller 自己 parse_llm_output).

    走自己 gateway /v1/chat/completions (复用 RBAC + quota + fallback cap),
    不直连 LiteLLM. gateway_url 默认 http://localhost:8999, auth_token 默认走
    CATFISH_DEV_TOKEN env (跟 internal_models.py 的 a2a 调用同模式).

    P3.5.29 Phase 5 (6/17 鸿波): ``model`` None default —
    body 真 ``role_resolver.resolve("chat_default")`` 拿真当前部署 model name.
    caller 显式传 ``model=...`` 仍优先 (selector_repair / 测试可 override).
    fail-silent: gateway 没起 → hardcoded fallback ``catfish-private-main``.

    Args:
        messages: build_messages(inputs) 输出
        model: 模型 id (None default → role_resolver "chat_default"; 显式传 override)
        gateway_url: 默认 http://localhost:8999 (env CATFISH_GATEWAY_URL 覆盖)
        auth_token: 默认 env CATFISH_DEV_TOKEN
        timeout_s: 长 multipart messages 大约 60-180s, 给 300s
        temperature: 0.3 (综合理解要稳, 不要太创造)
        max_tokens: 8000 (SKILL.md JSON 一般 2-4K, 给 2x 余量)

    Returns:
        LLM 输出文本 (含可能 markdown 围栏的 JSON), caller 用 parse_llm_output 抽

    Raises:
        RuntimeError: gateway 不可达 / token 无 / LLM 错误
    """
    import os

    import httpx  # 5/14 装上 (pip install httpx)

    gw = gateway_url or os.environ.get("CATFISH_GATEWAY_URL", "http://localhost:8999")
    tok = auth_token or os.environ.get("CATFISH_DEV_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "RecMode aggregator.call_llm: 没 auth token. "
            "设 CATFISH_DEV_TOKEN env 或 caller 显式传 auth_token."
        )

    # P3.5.29 Phase 5 (6/17 鸿波): model None → role_resolver chat_default.
    # caller 显式传 override 优先 (selector_repair / tests 用).
    # P3.5.29 Phase 8 (6/17 鸿波): 加 picker_state 真员工临时切 picker 录屏综合也走真选.
    if model is None:
        from .. import picker_state, role_resolver  # noqa: PLC0415
        model = (
            picker_state.read_picker_model()
            or role_resolver.resolve("chat_default")
            or "catfish-private-main"
        )

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,  # RecMode 不需要 stream, 一次拿完整 JSON
    }
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
    }
    # BL-RECMODE-AUTH-FORWARD (5/27 鸿波): 5/19 BL-AUTH-DECOUPLE-A1 后 catfish-gateway
    # 对 service token (sub=client:hermes-cli) 强制要求 X-Catfish-User. Companion
    # 走 hermes 模式时 Authorization 是 API_SERVER_KEY 这类 service token, 不带
    # X-Catfish-User 会被 gateway 400. caller (gateway endpoint) 把 user.email
    # 透下来, 这里附到 header.
    if effective_user:
        headers["X-Catfish-User"] = effective_user.strip()

    logger.info(
        "RecMode aggregator: 发 main %d messages (含 multipart 截图) → %s",
        len(messages), gw,
    )
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            resp = await client.post(f"{gw}/v1/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise RuntimeError(
                f"RecMode aggregator: gateway {gw} 不可达 ({e}). "
                f"看 catfish-gateway 起没."
            ) from e

        if resp.status_code != 200:
            # 把 gateway 友好错误透传 (e.g. 503 LargePromptFallbackBlocked / 429 quota)
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise RuntimeError(
                f"RecMode aggregator: gateway 返 {resp.status_code}: {detail}"
            )

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"RecMode aggregator: LLM 没返 choices: {data}")
        msg = choices[0].get("message") or {}
        content = msg.get("content", "")
        if not content:
            raise RuntimeError(f"RecMode aggregator: LLM 返空 content (可能被 max_tokens 截): {data}")
        usage = data.get("usage") or {}
        logger.info(
            "RecMode aggregator: LLM done. prompt=%d completion=%d total=%d",
            usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("total_tokens", 0),
        )
        return content


# ─── 解析 LLM 输出 ──────────────────────────────────────────


def parse_llm_output(raw: str) -> SkillOutput:
    """从 LLM 文本输出抽 JSON 块 → SkillOutput dataclass.

    宽容处理: LLM 可能加 markdown 围栏 (```json ... ```), 也可能 prose 前后包,
    用 regex 抽出第一段大括号内容尝试 parse. 失败抛 ValueError 含 raw 前 200 字.
    """
    # 优先抓 ```json ... ``` 围栏
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        json_str = m.group(1)
    else:
        # 退化: 抓第一个 { 到最后一个 } (贪婪, 兼容嵌套)
        first = raw.find("{")
        last = raw.rfind("}")
        if first < 0 or last < 0 or last < first:
            raise ValueError(f"LLM 输出找不到 JSON: {raw[:200]!r}")
        json_str = raw[first : last + 1]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM JSON parse 失败 ({e}): {json_str[:200]!r}") from e

    # P3.5.43: skill_name 兼容 snake_case 输入 → 自动转 kebab-case
    # (hermes 仓库现有 SKILL 都 kebab, LLM 偶尔输出 snake 兼容下).
    raw_name = data["skill_name"].strip().lower()
    skill_name = raw_name.replace("_", "-")

    # P3.5.43: triggers 必填. LLM 漏给的话 fallback 用 description 前几个词
    # (safe degrade, 不让 SkillOutput 构造失败).
    triggers = data.get("triggers") or []
    if not isinstance(triggers, list):
        triggers = []
    triggers = [t.strip() for t in triggers if isinstance(t, str) and t.strip()]

    # P3.5.43: kind 必填. LLM 漏给默认 procedural (录屏 99% 是流程类).
    kind = (data.get("kind") or "procedural").strip().lower()
    if kind not in ("procedural", "instructional"):
        kind = "procedural"

    return SkillOutput(
        skill_name=skill_name,
        namespace=data["namespace"],
        kind=kind,
        description=data["description"],
        triggers=triggers,
        intent_summary=data.get("intent_summary", ""),
        params_schema=data.get("params_schema", []),
        steps=data.get("steps", []),
        execute_code_segment=data.get("execute_code_segment", ""),
        output_schema=data.get("output_schema", {}),
        confidence=float(data.get("confidence", 0.0)),
        questions_for_user=data.get("questions_for_user", []),
        raw_json=data,
    )


# ─── 渲染 SKILL.md + script.py (P3.5.43 走 skill_format 公用模块) ──


def render_skill_md(skill: SkillOutput, recording_meta: dict | None = None) -> str:
    """渲染 hermes 兼容 SKILL.md.

    P3.5.43: 委托 skill_format.render_skill_md, 加 YAML frontmatter (老版直接 # 标题
    导致 hermes 加载失败, 跟 P3.5.43 audit 4 个 BLOCKER 之一对齐).
    """
    from .skill_format import render_skill_md as _render  # noqa: PLC0415
    return _render(skill.to_manifest(), recording_meta=recording_meta)


def render_script_py(skill: SkillOutput) -> str:
    """渲染 script.py (函数 def render_<name>(params)).

    P3.5.43: 文件名 script.py (不是 main.py), 函数名 render_<slug> 前缀 —
    跟 catfish_tools_skill_ops._find_render_function:180-185 对齐, 老版生成
    main.py + def main() catfish_run_skill 永远报错.
    """
    from .skill_format import render_script_py as _render  # noqa: PLC0415
    return _render(skill.to_manifest())


# 老 API 兼容 alias — 老调用者还有用 render_main_py 的, 加一个 deprecation wrapper
def render_main_py(skill: SkillOutput) -> str:
    """DEPRECATED (P3.5.43): 用 render_script_py 代替. 文件名也该改 script.py."""
    import warnings  # noqa: PLC0415
    warnings.warn(
        "render_main_py 已 deprecated (P3.5.43): 改用 render_script_py, "
        "文件名落 script.py 不是 main.py",
        DeprecationWarning,
        stacklevel=2,
    )
    return render_script_py(skill)


# ─── 落档 ──────────────────────────────────────────────────


def write_skill_files(
    skill: SkillOutput,
    skills_root: Path | None = None,
    recording_meta: dict | None = None,
    sync_to_hermes: bool = False,
) -> Path:
    """写 SKILL.md + script.py + recmode_meta.json 到 skills_root/<namespace>/<name>/.

    P3.5.43:
      - 文件名 script.py (不再是 main.py), 跟 catfish_tools_skill_ops:297 对齐
      - sync_to_hermes=True 落档后自动 rsync 到 ~/.hermes/skills/<name>/ (装机即用).
        默认 False 给 draft / 自动化 / 测试场景 (caller 决定何时装).
      - 落档前先 validate manifest, 不合规 warn 但不阻塞 (LLM 输出质量逐步收敛)

    返写出的目录路径.
    """
    from .skill_format import validate_manifest  # noqa: PLC0415

    if skills_root is None:
        skills_root = Path.home() / ".catfish" / "skills"

    skill_dir = skills_root / skill.namespace / skill.skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # P3.5.43: 写前 validate, 不合规 warn (e.g. description 超 budget / triggers 太少)
    manifest = skill.to_manifest()
    errors = validate_manifest(manifest)
    if errors:
        logger.warning(
            "RecMode skill %s validate 不合规, 仍落档 (LLM 输出质量逐步收敛): %s",
            skill.skill_name, "; ".join(errors),
        )

    (skill_dir / "SKILL.md").write_text(
        render_skill_md(skill, recording_meta), encoding="utf-8",
    )
    (skill_dir / "script.py").write_text(
        render_script_py(skill), encoding="utf-8",
    )

    if recording_meta:
        (skill_dir / "recmode_meta.json").write_text(
            json.dumps(
                {**recording_meta, "raw_llm_json": skill.raw_json,
                 "validate_errors": errors},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    logger.info(
        "RecMode skill 落档: %s (steps=%d, confidence=%.2f, validate_errors=%d)",
        skill_dir, len(skill.steps), skill.confidence, len(errors),
    )

    # P3.5.43: sync_to_hermes=True → 自动同步到 ~/.hermes/skills/<name>/
    # caller (aggregate_session draft_only=False / save_skill endpoint) 控.
    if sync_to_hermes:
        try:
            from . import skill_sync  # noqa: PLC0415
            synced = skill_sync.sync_to_hermes(skill_dir, slug=skill.skill_name)
            logger.info("RecMode skill 同步到 hermes: %s", synced)
        except Exception as e:  # noqa: BLE001
            # fail-silent: 同步挂不阻塞落档 (员工本机仍有, 手动 install 也行)
            logger.warning(
                "RecMode skill sync_to_hermes 失败 (跳过, 不阻塞落档): %s", e,
            )

    return skill_dir


# ─── 主入口 (gateway endpoint /api/learn/analyze 调) ───────


async def aggregate_session(
    session_dir: Path,
    skills_root: Path | None = None,
    *,
    gateway_url: str | None = None,
    auth_token: str | None = None,
    effective_user: str | None = None,
    draft_only: bool = True,  # 5/14 RecMode C: 默认落 draft, 用户 review 后 /api/learn/save_skill 才正式
) -> dict:
    """端到端: 读 session → 调 LLM → parse → 落 skill 文件.

    Caller (gateway endpoint /api/learn/analyze) 拿 auth_token 从当前 user
    的 dev_token / OIDC token, 传给 call_llm 走自己 gateway. 5/27 BL-RECMODE-
    AUTH-FORWARD: 同时拿 user.email 当 effective_user, call_llm 用它当
    X-Catfish-User (service token 模式必需).

    draft_only=True (默认): 落到 session_dir/skill_draft/, 用户 preview 看完
        点'保存'调 /api/learn/save_skill 才 mv 到正式 ~/.catfish/skills/.
        重录时直接覆盖 draft, 不污染正式 skills 目录.
    draft_only=False: 直接落正式 skills (老行为, test 用 / 自动化场景用)
    """
    inputs = load_recording_inputs(session_dir)
    messages = build_messages(inputs)
    raw_text = await call_llm(
        messages,
        gateway_url=gateway_url,
        auth_token=auth_token,
        effective_user=effective_user,
    )
    skill = parse_llm_output(raw_text)
    meta_path = session_dir / "meta.json"
    recording_meta = json.loads(meta_path.read_text()) if meta_path.exists() else None

    if draft_only:
        # 落 session_dir/skill_draft/ — 重录时覆盖. 用户点保存才 mv.
        # P3.5.43: draft 不 sync 到 hermes (review 期不暴露).
        draft_root = session_dir / "skill_draft"
        skill_dir = write_skill_files(
            skill, skills_root=draft_root, recording_meta=recording_meta,
            sync_to_hermes=False,
        )
    else:
        # P3.5.43: 正式落 + 自动 sync 到 ~/.hermes/skills/ (装机即用).
        skill_dir = write_skill_files(
            skill, skills_root=skills_root, recording_meta=recording_meta,
            sync_to_hermes=True,
        )

    return {
        "skill_name": skill.skill_name,
        "namespace": skill.namespace,
        "skill_dir": str(skill_dir),
        "steps_count": len(skill.steps),
        "confidence": skill.confidence,
        "questions_for_user": skill.questions_for_user,
        "is_draft": draft_only,
    }


__all__ = [
    "RecordingInputs",
    "SkillOutput",
    "load_recording_inputs",
    "build_messages",
    "parse_llm_output",
    "render_skill_md",
    "render_main_py",
    "write_skill_files",
    "aggregate_session",
    "SYSTEM_PROMPT",
]
