"""事实补丁 pipeline — BL-Q3-FACT P0 MVP (5/10).

三步 LLM 流程, 跟 facts_router.py 解耦:

  run_extract(meta) → 读变更文件 → LLM 提"事实点" → dict
  run_find_impact(meta, facts, user) → 拉 skills-hub 全 skill → LLM 标受影响 → list[impact]
  run_generate_patches(meta, facts, impacts, user) → 对每个 impact 调 BL-MM13 → list[patch]

LLM 调用走 internal_models.pick_internal_model("fact_analyzer") (BL-F14 同款),
跟 summarizer / proactive / a2a 一脉相承 (优先 private 模型, 数据不出公司).

# 简化点 (P0 demo MVP)

- find_impact 走 LLM 直接扫前 N 个 skill (catfish 现阶段 skill 总数 ~50, 够用),
  不走 BM25 / 不做向量检索 (Q3 P1 加)
- generate_patches 直接 LLM 写 unified diff, 不走 catfish_propose_skill_revision
  tool (那个在 tool-bridge 端, 调用链长. P0 直接 inline LLM, Q3 P1 接 tool-bridge)
- 都不并行, 严格顺序跑 (P0 简单 + debug 好看. 真生产并发跑会快很多)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

from .auth import User
from .config import load_config
from .internal_models import pick_internal_model

logger = logging.getLogger("catfish.gateway.facts_pipeline")

# ── LLM prompts ─────────────────────────────────

EXTRACT_PROMPT_SYSTEM = """你是合规分析助手. 任务: 读公司/政府发的变更通知/标准/规章, \
提取出"事实点"列表 (每个事实点 = 一条可执行的规则变更).

返回 JSON, schema:
{
  "summary": "整篇文件 1-2 句话总结",
  "effective_date": "ISO 日期, 若文件没说返 null",
  "facts": [
    {
      "id": "fact-001",          // 序号
      "title": "短标题 (10-20 字)",
      "summary": "1 句话事实, 主语+谓语+宾语 (e.g. '采购阈值从 5 万改为 3 万')",
      "category": "采购/合规/财务/安全/人事/其他",
      "keywords": ["3 个以内核心关键词"],
      "raw_quote": "原文摘录 (50-200 字, 完整保留数字和条款编号)",
      "impact_scope": "员工/部门/全公司 (e.g. '采购部 manager 以上' / '全员')"
    }
  ]
}

规则:
1. 只提"规则变更" — "X 改成 Y" 这种, 不提背景/解释/标点性介绍
2. 关键数字 (金额/天数/百分比) 必须原样保留
3. raw_quote 必须从原文逐字摘录, 不要 paraphrase
4. 一份文件可能有多个事实点, 也可能只有 1 个
5. 返**严格 JSON**, 不要 markdown 代码块包裹, 不要任何解释文字"""


FIND_IMPACT_PROMPT_SYSTEM = """你是 catfish skill 影响分析助手. 给你一条事实变更 + 一批 skill, \
你判断哪些 skill 可能受这个变更影响.

返回 JSON, schema:
{
  "impacts": [
    {
      "skill_namespace": "...",
      "skill_name": "...",
      "confidence": 0.0-1.0,
      "reason": "1 句话说明为什么受影响"
    }
  ]
}

规则:
1. 只列**confidence >= 0.5** 的 skill, 低的别报
2. 不确定的不要瞎猜, 宁可漏掉也不误报
3. reason 必须具体到"skill 的哪段内容跟事实变更冲突", 不要泛泛说"可能相关"
4. 一个事实变更可能影响 0 个 skill (返空数组), 也可能影响多个
5. 返严格 JSON, 不要 markdown 包裹"""


GENERATE_PATCH_PROMPT_SYSTEM = """你是 catfish skill 改写助手. 给你一个 skill 当前内容 + 一条 \
事实变更, 你写出"改进 patch" — 改后的 skill 内容应该跟新事实对齐.

返回 JSON, schema:
{
  "rationale": "改这个 skill 的理由 (1-2 句, 引用 raw_quote)",
  "changes": [
    {
      "description": "这一处改动的简短说明",
      "old_snippet": "原 skill 中的相关片段 (~50 字)",
      "new_snippet": "改后的片段"
    }
  ],
  "full_new_content": "改写后的完整 skill 内容 (Markdown 格式)"
}

规则:
1. 只改跟事实变更直接相关的部分, 其他原样保留
2. 关键数字 / 阈值 / 流程节点严格按事实变更里的 raw_quote 改, 不要发挥
3. changes 数组列出所有"具体改动点", 方便审批员对照看
4. full_new_content 是改完的完整内容, 直接落盘用
5. 不要引入任何 raw_quote 之外的"补充背景", 不要扩展, 不要加例子
6. 返严格 JSON"""


# ── 公共: LLM 调用 helper ───────────────────────

async def _llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 4000,
    triggered_by: str | None = None,
) -> dict:
    """调内部 LLM, 期望返 JSON. 失败时返 {"error": ...}.

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17 鸿波): 严格用 triggered_by (触发 admin)
    最近 session 的 model. 没 triggered_by / 拿不到 model → 返 error, 不
    fallback. caller (admin /api/facts/* endpoint) 必须传 admin user.sub.
    """
    from .user_model_resolver import get_user_last_session_model, resolve_model_obj  # noqa: PLC0415

    if not triggered_by:
        return {
            "error": "BL-INTERNAL-MODEL-FOLLOW-USER: 没 triggered_by (admin sub), "
                     "facts 分析跳过 (员工同款规则). caller 必须传 admin user.sub."
        }

    config = load_config()
    admin_model = get_user_last_session_model(triggered_by)
    chosen = resolve_model_obj(admin_model, config)
    if chosen is None:
        return {
            "error": f"没拿到 admin {triggered_by} 最近 session model, facts 分析跳过. "
                     "admin 先打开 Companion 选个 model 聊一句, 创建 session 后再触发."
        }

    import litellm  # noqa: PLC0415

    try:
        response = await litellm.acompletion(
            model=chosen.upstream.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            api_base=chosen.upstream.api_base,
            api_key=chosen.upstream.api_key,
            temperature=0.2,  # 提取/分析任务, 低温度求稳
            max_tokens=max_tokens,
            timeout=chosen.upstream.timeout,
            # response_format JSON mode — 部分模型支持, 不支持的会忽略 (Anthropic 看 system prompt 即可)
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
    except Exception as e:  # noqa: BLE001
        logger.exception("facts_pipeline LLM 调用失败")
        return {"error": f"LLM 调用失败: {e}"}

    # 解 JSON. LLM 可能塞 markdown ```json ... ``` 包裹, 兜底剥
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("LLM 返非 JSON: %s\n原文: %s", e, text[:500])
        return {"error": f"LLM 返非 JSON: {e}", "raw": text}


# ── 读变更文件 → 文本 ───────────────────────────

def _read_file_text(meta: dict) -> str:
    """读上传的变更文件, 返纯文本 (PDF/Word 用 pypdfium2/python-docx, txt/md 直读)."""
    raw_path = Path(meta["raw_path"])
    ext = meta["ext"].lower()
    if ext in {".txt", ".md", ".markdown"}:
        return raw_path.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        try:
            import pypdfium2 as pdfium  # noqa: PLC0415
            pdf = pdfium.PdfDocument(str(raw_path))
            return "\n\n".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
        except Exception as e:  # noqa: BLE001
            logger.warning("pypdfium2 读 %s 失败: %s, 退 raw bytes", raw_path, e)
            return raw_path.read_text(encoding="utf-8", errors="replace")
    if ext in {".docx", ".doc"}:
        try:
            from docx import Document  # noqa: PLC0415
            doc = Document(str(raw_path))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            for t in doc.tables:
                for row in t.rows:
                    cells = [c.text.strip() for c in row.cells]
                    paras.append(" | ".join(cells))
            return "\n".join(paras)
        except Exception as e:  # noqa: BLE001
            logger.warning("python-docx 读 %s 失败: %s", raw_path, e)
            return ""
    return raw_path.read_text(encoding="utf-8", errors="replace")


# ── Step 1: extract ─────────────────────────────

async def run_extract(meta: dict, triggered_by: str | None = None) -> dict:
    """读变更文件 → LLM 提"事实点". 返 {"summary", "facts": [...], "effective_date"}.

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17): triggered_by 是 admin sub, 用 admin
    最近 session 的 model. 调用方 (facts_router) 必传.
    """
    text = _read_file_text(meta)
    if len(text) < 50:
        return {"error": "文件解析后内容太短 (< 50 字), 可能解析失败", "facts": []}

    # 截断防 prompt 爆 — 取前 12000 字, 政策文件一般够
    if len(text) > 12000:
        logger.info("extract: 文件 %d 字截断到 12000", len(text))
        text = text[:12000] + "\n[...文件后续省略]"

    user_prompt = f"文件标题: {meta.get('title', '')}\n生效日期 (若文件没说, 留 null): {meta.get('effective_date') or '未指定'}\n\n文件内容:\n\n{text}"

    result = await _llm_json(EXTRACT_PROMPT_SYSTEM, user_prompt, max_tokens=6000, triggered_by=triggered_by)
    if "error" in result:
        return {**result, "facts": []}

    # 标准化
    if "facts" not in result:
        result["facts"] = []
    return result


# ── Step 2: find_impact ─────────────────────────

SKILLS_HUB_BASE = "http://127.0.0.1:8997"  # BL-D2 默认, 跟 skills_hub_proxy 同
SKILLS_HUB_TIMEOUT = 10.0


async def _fetch_all_skills(user: User) -> list[dict]:
    """拉 skills-hub 全部 skill 列表 (附 SKILL.md 内容).

    P0 简化: 直接 GET /skills 拉元信息, 再对每个 skill 拉 SKILL.md.
    串行拉, 慢但简单 (catfish 当前 skill 数量 < 50, 可接受).
    """
    headers = {
        "X-Catfish-User-Sub": user.sub,
        "X-Catfish-User-Dept": user.department or "",
        "X-Catfish-User-Role": user.role or "employee",
    }
    async with httpx.AsyncClient(timeout=SKILLS_HUB_TIMEOUT) as client:
        try:
            resp = await client.get(f"{SKILLS_HUB_BASE}/skills", headers=headers)
            resp.raise_for_status()
            skills_meta = resp.json().get("skills", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("拉 skills-hub /skills 失败: %s", e)
            return []

        # 对每个 skill 拉 SKILL.md 内容. P0 串行 + cap 50 (再多 demo 也演不完)
        out: list[dict] = []
        for s in skills_meta[:50]:
            ns = s.get("namespace", "")
            name = s.get("name", "")
            version = s.get("version", "")
            if not (ns and name and version):
                continue
            try:
                md_resp = await client.get(
                    f"{SKILLS_HUB_BASE}/skills/{ns}/{name}/{version}/files/SKILL.md",
                    headers=headers,
                )
                md_content = md_resp.text if md_resp.status_code == 200 else ""
            except Exception as e:  # noqa: BLE001
                logger.debug("拉 skill %s/%s/%s SKILL.md 失败: %s", ns, name, version, e)
                md_content = ""
            out.append({**s, "skill_md": md_content})
    logger.info("_fetch_all_skills: 拉到 %d 个 skill", len(out))
    return out


async def run_find_impact(meta: dict, facts: dict, user: User) -> list[dict]:
    """对每个事实点, LLM 找受影响 skill. 返扁平 list (每条 = 一个 impact).

    BL-INTERNAL-MODEL-FOLLOW-USER (5/17): triggered_by 用 user.sub (admin).
    """
    skills = await _fetch_all_skills(user)
    if not skills:
        logger.warning("找受影响 skill: skills-hub 返 0 个 skill (可能没起 / 没装 skill)")
        return []

    impacts_all: list[dict] = []
    fact_list = facts.get("facts", [])
    if not fact_list:
        return []

    # 一次 LLM 调用处理 1 个 fact × N skill (太多 fact 时分批)
    # 简化: 把所有 skill 摘要 (namespace/name + SKILL.md 前 300 字) 塞 prompt
    skill_summaries = []
    for s in skills:
        md = (s.get("skill_md") or "")[:300]
        skill_summaries.append({
            "namespace": s["namespace"],
            "name": s["name"],
            "version": s.get("version"),
            "description": s.get("description", "")[:100],
            "skill_md_preview": md,
        })

    skills_block = json.dumps(skill_summaries, ensure_ascii=False)

    for fact in fact_list:
        user_prompt = (
            f"事实变更:\n{json.dumps(fact, ensure_ascii=False, indent=2)}\n\n"
            f"待筛 skill 列表 (共 {len(skills)} 个):\n{skills_block}"
        )
        result = await _llm_json(FIND_IMPACT_PROMPT_SYSTEM, user_prompt, max_tokens=2000, triggered_by=user.sub)
        if "error" in result:
            logger.warning("find_impact LLM 失败: %s (fact=%s)", result.get("error"), fact.get("id"))
            continue
        for imp in result.get("impacts", []):
            imp["fact_id"] = fact.get("id")
            imp["fact_summary"] = fact.get("summary")
            imp["detection_method"] = "llm_semantic"  # P1 加 grep/bm25 区分
            impacts_all.append(imp)

    # 按 confidence 倒序, 同 skill 多次命中保留最高 confidence 那个
    impacts_all.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    seen: dict[str, dict] = {}
    for imp in impacts_all:
        key = f"{imp['skill_namespace']}/{imp['skill_name']}"
        if key not in seen:
            seen[key] = imp
    impacts_dedup = list(seen.values())
    logger.info(
        "find_impact: %d facts × %d skills → %d 候选 (去重 %d)",
        len(fact_list), len(skills), len(impacts_all), len(impacts_dedup),
    )
    return impacts_dedup


# ── Step 3: generate_patches ────────────────────

CONFIDENCE_THRESHOLD = 0.6  # 低于这个不生成 patch (避免浪费 token)


async def run_generate_patches(
    meta: dict, facts: dict, impacts: list[dict], user: User
) -> list[dict]:
    """对每个高 confidence 受影响 skill, LLM 生成 patch."""
    if not impacts:
        return []

    # 拉受影响 skill 的完整 SKILL.md (而不是 find_impact 阶段的前 300 字 preview)
    headers = {
        "X-Catfish-User-Sub": user.sub,
        "X-Catfish-User-Dept": user.department or "",
        "X-Catfish-User-Role": user.role or "employee",
    }

    patches: list[dict] = []
    fact_by_id = {f.get("id"): f for f in facts.get("facts", [])}

    async with httpx.AsyncClient(timeout=SKILLS_HUB_TIMEOUT) as client:
        for imp in impacts:
            if imp.get("confidence", 0) < CONFIDENCE_THRESHOLD:
                logger.debug("跳过低 confidence impact: %s/%s c=%.2f",
                             imp["skill_namespace"], imp["skill_name"], imp["confidence"])
                continue

            # 拉这个 skill 的最新版本 + SKILL.md 全文
            ns = imp["skill_namespace"]
            name = imp["skill_name"]
            try:
                latest = await client.get(f"{SKILLS_HUB_BASE}/skills/{ns}/{name}", headers=headers)
                latest.raise_for_status()
                version = latest.json().get("version", "")
                if not version:
                    logger.warning("拉 skill %s/%s latest 没 version, 跳过", ns, name)
                    continue
                md_resp = await client.get(
                    f"{SKILLS_HUB_BASE}/skills/{ns}/{name}/{version}/files/SKILL.md",
                    headers=headers,
                )
                full_md = md_resp.text if md_resp.status_code == 200 else ""
            except Exception as e:  # noqa: BLE001
                logger.warning("拉 skill %s/%s 内容失败: %s", ns, name, e)
                continue

            if not full_md.strip():
                continue

            fact = fact_by_id.get(imp.get("fact_id"), {})
            user_prompt = (
                f"事实变更:\n{json.dumps(fact, ensure_ascii=False, indent=2)}\n\n"
                f"为什么这个 skill 受影响: {imp.get('reason', '')}\n\n"
                f"skill 当前内容 ({ns}/{name} v{version}):\n```\n{full_md}\n```"
            )
            patch_result = await _llm_json(
                GENERATE_PATCH_PROMPT_SYSTEM, user_prompt, max_tokens=4000, triggered_by=user.sub
            )
            if "error" in patch_result:
                logger.warning("generate_patch LLM 失败: %s", patch_result.get("error"))
                continue

            patches.append({
                "fact_id": imp.get("fact_id"),
                "skill_namespace": ns,
                "skill_name": name,
                "skill_version_base": version,  # 基于哪个版本改的
                "confidence": imp.get("confidence"),
                "rationale": patch_result.get("rationale", ""),
                "changes": patch_result.get("changes", []),
                "full_new_content": patch_result.get("full_new_content", ""),
                "status": "pending",  # pending → approved / rejected
                "generated_at_ms": int(__import__("time").time() * 1000),
            })

    logger.info("generate_patches: %d 候选 × confidence≥%.2f → %d patches",
                len(impacts), CONFIDENCE_THRESHOLD, len(patches))
    return patches
