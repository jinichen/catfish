"""P3.5.43 (鸿波 6/20 拍 'SKILL 三路径彻底统一收口').

# 干啥

集中 SKILL.md + script.py 渲染逻辑, RecMode 和 propose_skill 都调它, 保证
**生成的 skill 真能被 hermes 加载 + catfish_run_skill 调用**.

# 解决的 3 个 BLOCKER (P3.5.43 audit 发现, 不是猜)

1. **frontmatter 缺失** (老 `aggregator.render_skill_md:426-476` 第一行直接 `# {name}`):
   hermes plugin loader 加载 SKILL.md 时 parse YAML frontmatter 拿
   `name/version/kind/triggers/description/deprecated`. 缺 frontmatter →
   silent skip / parse 异常, skill 不进 hermes 视野.
   ✓ 修: 输出标准 `---\n<yaml>\n---\n` 头.

2. **文件名 + 函数名错** (老 `aggregator.write_skill_files:543` 写 `main.py` +
   `render_main_py:494` 生成 `def main(params)`):
   `catfish_tools_skill_ops.py:297` 找 `script.py`, `_find_render_function:180-185`
   找 `attr.startswith("render_")`. 文件名 + 函数名都对不上, catfish_run_skill
   永远报 "script.py 不存在" 或 "没 render_* 函数".
   ✓ 修: 文件名 `script.py`, 函数名 `render_<skill_slug>(params)`.

3. **无 triggers + description 无 budget** (老 `aggregator.SYSTEM_PROMPT:48-103`
   不要求 LLM 出 triggers, description 也无长度约束):
   hermes 加载 skill 时 system prompt 注入 description, 超 ~158 tokens (P3.5.41.1
   安全线, `audit-skills.py:30-33` 算法) 会撞 SYSTEM_PROMPT 超限 (P3.5.32.5 实证).
   ✓ 修: SkillManifest 加 triggers 字段; description token 计数 + 校验 + warn.

# 参考

- hermes SKILL.md 真实样本: `~/person_task/catfish/skills/department/weekly-report/SKILL.md:1-16`
- install_huashu_ppt_skill.sh:82-163 (装机时补 frontmatter 的范本)
- audit-skills.py:30-33, 186 (token 算法)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


# ─── 常量 ──────────────────────────────────────────────────────────

#: description token 预算安全线 (P3.5.41.1 huashu 压缩到 158 是实测可用).
#: 超出会撞 system prompt 总预算 (P3.5.32.5 advisor 超时实证).
DESCRIPTION_TOKEN_BUDGET = 158

#: triggers 数量范围 (太少 LLM 触发漏, 太多 system prompt 占用大).
TRIGGERS_MIN = 3
TRIGGERS_MAX = 20

#: kind enum (hermes SKILL 规范).
VALID_KINDS = ("procedural", "instructional")

#: namespace enum (catfish 内部约定 — 跟 install scripts 对齐).
VALID_NAMESPACES = ("personal", "department", "public", "creative")


# ─── data class ────────────────────────────────────────────────────


@dataclass
class SkillManifest:
    """统一 skill 数据载体 — RecMode SkillOutput / propose_skill 入参都转成它.

    frontmatter 字段 (必填, hermes plugin loader 强制):
      - name: kebab-case slug (kebab 跟 hermes 仓库现有 SKILL 对齐, e.g. weekly-report)
      - version: SemVer (e.g. "1.0.0")
      - kind: procedural / instructional
      - description: 给 system prompt 注入的描述 (≤158 tokens)
      - triggers: 触发关键词 list (3-20 个, 让 LLM 看到关键词时调 skill)
      - deprecated: bool 默认 False

    catfish 扩展字段 (frontmatter 也写, 不影响 hermes 加载):
      - namespace: personal / department / public / creative — 装机目录区分

    body 字段 (markdown 段落, hermes 不解析, catfish_run_skill 也不读, 给员工看):
      - intent_summary / params_schema / steps / execute_code_segment / etc
    """

    # ── frontmatter 必填 ─────────────────────────────────────────
    name: str  # kebab-case
    namespace: str  # personal / department / public / creative
    kind: str  # procedural / instructional
    description: str  # ≤158 tokens
    triggers: list[str]  # 3-20 个

    # ── frontmatter 可选 ─────────────────────────────────────────
    version: str = "1.0.0"
    deprecated: bool = False
    author: str = "鲶鱼 RecMode"  # 自动生成的 skill 标 RecMode, propose 改成 propose_skill

    # ── body 段落 ────────────────────────────────────────────────
    intent_summary: str = ""
    params_schema: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    execute_code_segment: str = ""
    output_schema: dict = field(default_factory=dict)
    questions_for_user: list[str] = field(default_factory=list)


# ─── 校验 ──────────────────────────────────────────────────────────


def estimate_description_tokens(desc: str) -> int:
    """ceil(utf8_bytes / 4) — 跟 audit-skills.py:30-33 算法对齐.

    跟 OpenAI / Codex tokenizer 接近 (中文每字符 ~3 bytes → ~0.75 token).
    """
    return math.ceil(len(desc.encode("utf-8")) / 4)


_KEBAB_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")


def validate_manifest(manifest: SkillManifest) -> list[str]:
    """返一组 validation errors. 空 = OK. 调用方 raise / warn 自决."""
    errors: list[str] = []

    # name
    if not manifest.name:
        errors.append("name 不能为空")
    elif not _KEBAB_RE.match(manifest.name):
        errors.append(
            f"name {manifest.name!r} 必须 kebab-case (小写 + 连字符, e.g. weekly-report)"
        )

    # namespace
    if manifest.namespace not in VALID_NAMESPACES:
        errors.append(
            f"namespace {manifest.namespace!r} 不合法, 必须是 {VALID_NAMESPACES}"
        )

    # kind
    if manifest.kind not in VALID_KINDS:
        errors.append(
            f"kind {manifest.kind!r} 不合法, 必须是 {VALID_KINDS}"
        )

    # description
    if not manifest.description.strip():
        errors.append("description 不能为空")
    else:
        tokens = estimate_description_tokens(manifest.description)
        if tokens > DESCRIPTION_TOKEN_BUDGET:
            errors.append(
                f"description {tokens} tokens 超预算 {DESCRIPTION_TOKEN_BUDGET} "
                f"(P3.5.41.1 安全线). 删 body 细节, 留触发词 + 一句话定位."
            )

    # triggers
    if len(manifest.triggers) < TRIGGERS_MIN:
        errors.append(
            f"triggers 至少 {TRIGGERS_MIN} 个, 现有 {len(manifest.triggers)} 个 "
            f"(LLM 看到关键词才会调, 太少漏触发)"
        )
    elif len(manifest.triggers) > TRIGGERS_MAX:
        errors.append(
            f"triggers 至多 {TRIGGERS_MAX} 个, 现有 {len(manifest.triggers)} 个 "
            f"(占 system prompt 预算)"
        )

    return errors


# ─── 渲染 ──────────────────────────────────────────────────────────


def _yaml_str(s: str) -> str:
    """YAML 字符串安全转义 — 含特殊字符 / 多行就用 `|-` 块标量."""
    if "\n" in s:
        indented = "\n".join("  " + line for line in s.split("\n"))
        return "|-\n" + indented
    # 含 YAML 特殊字符 → 双引号
    if any(c in s for c in (":", "#", "[", "]", "{", "}", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`")):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml_list(items: list[str]) -> str:
    """YAML list 缩进 block 形态.

    输出:
        triggers:
          - item1
          - item2
    """
    if not items:
        return "[]"
    lines = []
    for item in items:
        lines.append(f"  - {_yaml_str(item)}")
    return "\n" + "\n".join(lines)


def render_frontmatter(manifest: SkillManifest, today: Optional[str] = None) -> str:
    """渲染 YAML frontmatter (不含外层 `---` 标记).

    跟 hermes weekly-report SKILL 同 schema. created/updated 加 catfish 扩展.
    """
    today = today or date.today().isoformat()
    lines = [
        f"name: {manifest.name}",
        f'version: "{manifest.version}"',
        f"kind: {manifest.kind}",
        f"deprecated: {'true' if manifest.deprecated else 'false'}",
        f"namespace: {manifest.namespace}",
        f"author: {_yaml_str(manifest.author)}",
        f"created: {today}",
        f"updated: {today}",
        f"description: {_yaml_str(manifest.description)}",
        f"triggers:{_yaml_list(manifest.triggers)}",
    ]
    return "\n".join(lines)


def render_skill_md(
    manifest: SkillManifest,
    recording_meta: Optional[dict] = None,
    today: Optional[str] = None,
) -> str:
    """渲染完整 SKILL.md (含 hermes-兼容 frontmatter + body 段).

    recording_meta: RecMode 自动生成时塞录制元数据 (session_id / 时长 / events 数).
                    propose_skill / 手写 skill 不传.
    """
    parts: list[str] = []

    # ── 1. frontmatter ──────────────────────────────
    parts.append("---")
    parts.append(render_frontmatter(manifest, today=today))
    parts.append("---")
    parts.append("")

    # ── 2. 标题 ─────────────────────────────────────
    parts.append(f"# {manifest.name}")
    parts.append("")

    # ── 3. 意图说明 ─────────────────────────────────
    if manifest.intent_summary:
        parts.append("## 用户意图")
        parts.append("")
        parts.append(manifest.intent_summary)
        parts.append("")

    # ── 4. 参数 schema ──────────────────────────────
    if manifest.params_schema:
        parts.append("## 参数")
        parts.append("")
        for p in manifest.params_schema:
            default = p.get("default")
            default_str = f" (默认 `{default}`)" if default is not None else ""
            parts.append(
                f"- **{p['name']}** (`{p.get('type', 'string')}`){default_str} — "
                f"{p.get('description', '')}"
            )
        parts.append("")

    # ── 5. 步骤 ─────────────────────────────────────
    if manifest.steps:
        parts.append("## 步骤")
        parts.append("")
        for s in manifest.steps:
            parts.append(f"### {s.get('step_no', '?')}. {s.get('intent', '')}")
            parts.append("")
            if s.get("tool"):
                parts.append(f"- tool: `{s['tool']}`")
            if s.get("selector_hint"):
                import json as _json
                parts.append(
                    f"- selector_hint: `{_json.dumps(s['selector_hint'], ensure_ascii=False)}`"
                )
            if s.get("expected_after"):
                parts.append(f"- 预期: {s['expected_after']}")
            parts.append("")

    # ── 6. 待 confirm (可选) ────────────────────────
    if manifest.questions_for_user:
        parts.append("## ⚠ 待 confirm")
        parts.append("")
        for q in manifest.questions_for_user:
            parts.append(f"- {q}")
        parts.append("")

    # ── 7. 录制元数据 (RecMode 自动生成时塞) ───────
    if recording_meta:
        parts.append("---")
        parts.append("")
        parts.append("## 自动生成元数据 (不要手改, 重录或 propose 再生)")
        parts.append("")
        parts.append(f"- session_id: `{recording_meta.get('session_id', '?')}`")
        parts.append(f"- 录制时长: {recording_meta.get('duration_s', '?')}s")
        parts.append(f"- events: {recording_meta.get('events_count', '?')}")
        parts.append(f"- keyframes: {recording_meta.get('keyframes_count', '?')}")
        parts.append("")

    return "\n".join(parts)


def _name_to_render_function(name: str) -> str:
    """kebab-case → render_<snake_case>.

    e.g. "weekly-report" → "render_weekly_report"
    catfish_tools_skill_ops._find_render_function 找 attr.startswith("render_"),
    所以前缀必须是 render_.
    """
    snake = name.replace("-", "_")
    return f"render_{snake}"


def render_script_py(manifest: SkillManifest) -> str:
    """渲染 script.py — 必含 `def render_<slug>(params)` 函数.

    catfish_tools_skill_ops.py:180-185 _find_render_function 找
    `attr.startswith("render_")` 的函数, 找不到 catfish_run_skill 直接 fail.
    所以函数名必须是 render_ 前缀.

    body: step 串联 + execute_code_segment (LLM 输出的数据提取/输出代码).
    """
    import json as _json
    fn_name = _name_to_render_function(manifest.name)

    parts = [
        '"""P3.5.43 自动生成 skill script. 不要手改 — propose 或重录后重生.',
        "",
        f"skill: {manifest.name} ({manifest.namespace})",
        f"description: {manifest.description[:200]}",
        '"""',
        "",
        f'SKILL_NAME = "{manifest.name}"',
        f'NAMESPACE = "{manifest.namespace}"',
        '',
        '',
        f"def {fn_name}(params):",
        '    """skill 入口 — catfish_run_skill 调这个函数.',
        '',
        '    Args:',
        '      params: dict — 跟 SKILL.md `## 参数` 段对齐.',
        '',
        '    Returns:',
        '      dict — 至少含 {ok: bool, summary: str}. catfish_run_skill 透传给 LLM.',
        '    """',
    ]

    # 步骤注释 (skill 真跑接 catfish runtime, 这里 emit 模板)
    for s in manifest.steps:
        parts.append(f"    # Step {s.get('step_no', '?')}: {s.get('intent', '')}")
        if s.get("tool"):
            parts.append(f"    # tool: {s['tool']}")
        if s.get("args_template"):
            parts.append(
                f"    # args: {_json.dumps(s['args_template'], ensure_ascii=False)}"
            )
        parts.append("")

    # 数据提取段
    if manifest.execute_code_segment:
        parts.append("    # ── 数据提取 (LLM 综合生成) ──")
        for line in manifest.execute_code_segment.splitlines():
            parts.append(f"    {line}")
        parts.append("")

    # ── 返回 ok=False, 不是 ok=True (8/20 修) ──────────────────────
    #
    # 这里原来生成的是 `"ok": True, "summary": "skill X 跑完"` —— 而上面那些
    # 步骤**只是注释**, 一行都没实现 (见上面那句 "skill 真跑接 catfish runtime,
    # 这里 emit 模板" —— 那个 runtime 并不存在)。
    #
    # 于是每个 propose/install 出来的 skill 都是: 什么都不做, 然后报告成功。
    # 8/19 凝固的 eis-zizhi-shenpi 就是这样, 32 行, 整个函数体只有一个
    # 无条件 return。它骑在「资质申请审批」这条政企流程上。
    #
    # 配上 catfish_tool_schemas_skill.py 里那条铁律 (返 ok=false 才允许手工
    # 接管), ok=True 意味着模型会告诉员工"已办理", 而实际什么都没提交。
    #
    # 改成 ok=False + 说清楚为什么。代价是这类 skill 调用会明确失败 ——
    # 这正是我们要的: 失败要看得见, 而不是伪装成成功。
    parts.extend([
        '    return {',
        '        "ok": False,',
        f'        "error": "skill {manifest.name} 的步骤只凝固成了注释, 函数体没有实现 — '
        '请按上面的步骤补实现, 或者把 SKILL.md 当文档用 (让模型照着步骤自己执行)。",',
        f'        "summary": "skill {manifest.name} 未实现 (只有步骤注释)",',
        '        "files": [],',
        '    }',
        '',
        '',
        'if __name__ == "__main__":',
        '    # 直接跑 — 给 RecMode preview / 手动调试用',
        '    import json, sys',
        '    p = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}',
        f'    result = {fn_name}(p)',
        '    print(json.dumps(result, ensure_ascii=False, indent=2))',
        '',
    ])
    return "\n".join(parts)


__all__ = [
    "SkillManifest",
    "DESCRIPTION_TOKEN_BUDGET",
    "TRIGGERS_MIN",
    "TRIGGERS_MAX",
    "VALID_KINDS",
    "VALID_NAMESPACES",
    "estimate_description_tokens",
    "validate_manifest",
    "render_frontmatter",
    "render_skill_md",
    "render_script_py",
]
