"""expertise — BL-FED2.1 (5/12 鸿波拍板): 员工专长自动从 journal 抽取.

# 为啥

BL-FED2 (Plan D Federation v2) 真卖点 = 员工自愿出来同事互助.
但员工**自己写专长 tag** 太累 + 容易遗漏 / 主观偏差.

5/12 鸿波拍板: **从 employee_journal 自动抽**, 员工 review (👍/👎/✏️) 即可.
journal 里"鸿波本周做了 X / Y / Z" 累积 ~30 天 → LLM 一眼看出"这人擅长资质 / 合规".

# 设计

  ~/.catfish/expertise.yaml  ← 员工 mac 本机, 跨机迁移走 cp
  ───────────────────────────────────────────
  extracted_at: 2026-05-12T20:00:00+08:00
  source: employee_journal.md  # 或 + session_history
  auto_review_pending: true    # 员工没 review 时 true → 黄页不展示
  tags:
    - tag: 资质管理
      confidence: 0.92
      evidence_count: 27         # journal 里相关段落 (粗估)
      aliases: [资质, 证书申报]
      status: pending            # pending → confirmed / rejected
      last_reviewed_at: null
    - tag: 合规审核
      ...

# 隐私边界 (跟 BL-FED2 灵魂校准对齐)

  - journal **全程在员工 mac**, 抽取走员工本机 hermes 端的 LLM (catfish-private-main)
  - expertise.yaml **写在员工 mac**, 不上传中央
  - export_for_registry() 上行**只 tag 字符串列表** (不传 confidence / evidence /
    aliases / source) — 中央 identity-server 只知道"这人公开了 X / Y / Z 三个 tag",
    不知道为啥
  - 员工随时改 status='rejected' / 删 tag → 黄页立即失踪 (BL-FED2.2 黄页 endpoint
    每次查 expertise.yaml 不缓存)
  - 员工跳槽 cp ~/.catfish/expertise.yaml → 新 mac 直接接管 (跨雇主可携带)

# 何时跑

  - 员工 explicit "鲶鱼, 抽一下我的专长" → catfish_extract_expertise tool
  - 周期 (BL-FED2.x 后续): journal 长度 +20% 触发自动重抽

# LLM 调用

  走 catfish-private-main (内部模型, 不计员工 quota), 通过 tool-bridge 内部
  通常通过 catfish_run_skill 的 dispatch 机制不行 — 需要 caller 提供
  llm_call_fn (ABI 解耦, 测试 mock 容易).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("catfish.tool_bridge.expertise")

#: 默认 expertise.yaml 路径
EXPERTISE_PATH = Path.home() / ".catfish" / "expertise.yaml"

#: tag 名长度上限 (短 = 易搜)
MAX_TAG_LEN = 20
#: 单次抽出 tag 数上限 (防 LLM 幻觉一堆)
MAX_TAGS_PER_EXTRACT = 20
#: tag 名长度下限
MIN_TAG_LEN = 2

#: status 枚举
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
_VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_CONFIRMED, STATUS_REJECTED})


# ─── yaml IO (避免 PyYAML 强依赖, 自己 dump 手写格式) ────────────────


def _yaml_dump(data: dict[str, Any]) -> str:
    """简化 yaml dump — 只支持本模块用到的 schema, 不上 PyYAML 依赖."""
    lines: list[str] = []
    lines.append(f"extracted_at: {data.get('extracted_at', '')}")
    lines.append(f"source: {data.get('source', '')}")
    lines.append(f"auto_review_pending: {str(bool(data.get('auto_review_pending', True))).lower()}")
    lines.append("tags:")
    for t in data.get("tags") or []:
        lines.append(f"  - tag: {_yaml_str(t.get('tag', ''))}")
        if "confidence" in t:
            lines.append(f"    confidence: {float(t['confidence']):.2f}")
        if "evidence_count" in t:
            lines.append(f"    evidence_count: {int(t['evidence_count'])}")
        aliases = t.get("aliases") or []
        if aliases:
            quoted = ", ".join(_yaml_str(a) for a in aliases)
            lines.append(f"    aliases: [{quoted}]")
        lines.append(f"    status: {t.get('status', STATUS_PENDING)}")
        lr = t.get("last_reviewed_at")
        lines.append(f"    last_reviewed_at: {lr if lr else 'null'}")
    return "\n".join(lines) + "\n"


def _yaml_str(s: str) -> str:
    """yaml 单引号 quote (含特殊字符的 tag/alias)."""
    if not s:
        return "''"
    # 简单情况无 quote
    if re.match(r"^[A-Za-z0-9一-鿿_\-]+$", s):
        return s
    # 含特殊字符 → quote
    return "'" + s.replace("'", "''") + "'"


def _yaml_load(text: str) -> dict[str, Any]:
    """简化 yaml load — 只解析本模块写出的 schema. 不抛."""
    out: dict[str, Any] = {
        "extracted_at": "",
        "source": "",
        "auto_review_pending": True,
        "tags": [],
    }
    if not text or not text.strip():
        return out
    lines = text.splitlines()
    cur_tag: dict[str, Any] | None = None
    for raw in lines:
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        # 顶层字段
        if not line.startswith(" "):
            if line.startswith("tags:"):
                continue
            m = re.match(r"^([a-z_][a-z0-9_]*):\s*(.*)$", line)
            if m:
                k, v = m.group(1), m.group(2).strip()
                if k == "auto_review_pending":
                    out[k] = v.lower() in ("true", "yes", "1")
                else:
                    out[k] = v
            continue
        # tag 列表项 (- tag: xxx)
        if line.startswith("  - tag:"):
            if cur_tag:
                out["tags"].append(cur_tag)
            cur_tag = {
                "tag": _strip_yaml_str(line[len("  - tag:"):].strip()),
                "status": STATUS_PENDING,
            }
            continue
        # tag 内字段 (    confidence: 0.92)
        if line.startswith("    ") and cur_tag is not None:
            m = re.match(r"^    ([a-z_][a-z0-9_]*):\s*(.*)$", line)
            if not m:
                continue
            k, v = m.group(1), m.group(2).strip()
            if k == "confidence":
                try:
                    cur_tag[k] = float(v)
                except ValueError:
                    pass
            elif k == "evidence_count":
                try:
                    cur_tag[k] = int(v)
                except ValueError:
                    pass
            elif k == "aliases":
                # [a, b, c] 或 ['a', 'b']
                inner = v.strip("[]").strip()
                items = [_strip_yaml_str(x.strip()) for x in inner.split(",") if x.strip()]
                cur_tag[k] = items
            elif k == "last_reviewed_at":
                cur_tag[k] = None if v in ("null", "~", "") else v
            else:
                cur_tag[k] = v
    if cur_tag:
        out["tags"].append(cur_tag)
    return out


def _strip_yaml_str(s: str) -> str:
    s = s.strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1].replace("''", "'")
    return s


# ─── 核心 API ─────────────────────────────────────────────────────────


def load_expertise(path: Path | None = None) -> dict[str, Any]:
    """读 ~/.catfish/expertise.yaml. 不存在返默认空."""
    p = path or EXPERTISE_PATH
    if not p.exists():
        return {
            "extracted_at": "",
            "source": "",
            "auto_review_pending": True,
            "tags": [],
        }
    try:
        return _yaml_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        logger.warning("读 expertise.yaml 失败: %s", e)
        return {"extracted_at": "", "source": "", "auto_review_pending": True, "tags": []}


def save_expertise(data: dict[str, Any], path: Path | None = None) -> bool:
    """写 ~/.catfish/expertise.yaml. 失败返 False, 永不抛."""
    p = path or EXPERTISE_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_yaml_dump(data), encoding="utf-8")
        return True
    except OSError as e:
        logger.warning("写 expertise.yaml 失败: %s", e)
        return False


# ─── LLM 调用 (ABI: 解耦, 测试 mock) ─────────────────────────────────


_EXTRACT_PROMPT = """你是员工专长抽取助手. 读员工的工作日记 (employee_journal.md), 抽出 5-15 个专长方向.

约束:
1. **必须基于日记真实内容** (有 evidence). **不要编造**.
2. **tag 短** (2-6 字), 例 "资质管理" / "合规审核" / "项目立项" — 不要"管理项目立项报告写作" 长串.
3. **confidence 0-1** — 高频 + 多样化提及 = 高 confidence (>0.8). 偶尔提到 = 低 (<0.5, 跳过别返).
4. **evidence_count** 整数 — journal 里相关 entry 数粗估.
5. **aliases** 0-3 个同义词 / 缩写 (例 "资质管理" 加 ["资质", "证书申报"]).
6. **[a2a-help] 标签的 entry 加权** (BL-FED2.4 反馈环): 这是员工**真实被同事咨询**的事实, 比员工自己日记自夸更可靠. 每个 [a2a-help] 算 evidence_count +3 (相当于自己干 3 次). 出现 2 次同主题 [a2a-help] → 该 tag confidence 应 ≥0.85.

# 输入: 员工 journal

{journal_text}

# 输出 (strict JSON, 不要 markdown wrap)

{{
  "tags": [
    {{"tag": "资质管理", "confidence": 0.92, "evidence_count": 27, "aliases": ["资质", "证书申报"]}},
    {{"tag": "合规审核", "confidence": 0.85, "evidence_count": 18, "aliases": ["合规"]}},
    ...
  ]
}}

只输出 JSON, 不要任何解释."""


def extract_from_journal(
    journal_text: str,
    llm_call_fn: Callable[[str], str],
    *,
    max_tags: int = MAX_TAGS_PER_EXTRACT,
) -> list[dict[str, Any]]:
    """调 LLM 抽 tag. llm_call_fn 接 prompt 返 raw response (caller 负责走哪个模型 / token).

    永不抛 — LLM 失败 / JSON 解析失败 → 返空列表.
    """
    if not journal_text or not journal_text.strip():
        return []
    prompt = _EXTRACT_PROMPT.format(journal_text=journal_text[:30000])  # cap 30K
    try:
        raw = llm_call_fn(prompt)
    except Exception as e:
        logger.warning("LLM 抽 expertise 调用失败: %s", e)
        return []
    if not raw:
        return []

    # 抽 JSON (LLM 偶尔会带 markdown ```json ... ```)
    json_text = raw.strip()
    if json_text.startswith("```"):
        # 去掉 fence
        json_text = re.sub(r"^```(json)?\n", "", json_text)
        json_text = re.sub(r"\n```\s*$", "", json_text)
    # 取第一个 { 到对应 }
    start = json_text.find("{")
    end = json_text.rfind("}")
    if start < 0 or end <= start:
        logger.warning("LLM 返非 JSON: %s", raw[:200])
        return []
    try:
        parsed = json.loads(json_text[start:end + 1])
    except json.JSONDecodeError as e:
        logger.warning("expertise JSON 解析失败: %s. raw=%s", e, raw[:200])
        return []

    raw_tags = parsed.get("tags") or []
    if not isinstance(raw_tags, list):
        return []

    # 校验 + normalize
    out: list[dict[str, Any]] = []
    seen_tags: set[str] = set()
    for t in raw_tags[:max_tags]:
        if not isinstance(t, dict):
            continue
        tag = (t.get("tag") or "").strip()
        if not tag or len(tag) < MIN_TAG_LEN or len(tag) > MAX_TAG_LEN:
            continue
        # 去重
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        try:
            confidence = float(t.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        if confidence < 0.4:
            # confidence 太低跳过 (LLM 自己也不确定)
            continue
        try:
            evidence_count = int(t.get("evidence_count", 1))
        except (TypeError, ValueError):
            evidence_count = 1
        evidence_count = max(0, evidence_count)
        aliases_raw = t.get("aliases") or []
        aliases = [str(a).strip() for a in aliases_raw if isinstance(a, (str, int))][:5]
        out.append({
            "tag": tag,
            "confidence": confidence,
            "evidence_count": evidence_count,
            "aliases": aliases,
            "status": STATUS_PENDING,
            "last_reviewed_at": None,
        })
    return out


def merge_with_existing(
    new_tags: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并新抽 tag 跟员工已 review 过的 tag (status != pending 的保留).

    规则:
      - 已 confirmed / rejected 的 tag → status 保留 (员工决策不被覆盖)
      - 已 confirmed 的 tag 用新 confidence / evidence_count 更新 (实时最新)
      - 已 rejected 的 tag 即使新抽到也维持 rejected (员工说不要 = 不要)
      - pending 的 tag 直接被新数据替换
      - 全新 tag 加进来 status=pending
    """
    # 按 tag 名建索引 (大小写不敏感)
    existing_idx: dict[str, dict[str, Any]] = {
        (t.get("tag") or "").lower(): t for t in existing
    }
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for new_t in new_tags:
        key = (new_t.get("tag") or "").lower()
        if not key:
            continue
        seen.add(key)
        old_t = existing_idx.get(key)
        if old_t and old_t.get("status") in (STATUS_CONFIRMED, STATUS_REJECTED):
            # 员工 explicit 决策保留, 但更新数据
            merged_t = {
                **new_t,
                "status": old_t["status"],
                "last_reviewed_at": old_t.get("last_reviewed_at"),
            }
        else:
            merged_t = new_t  # pending or 新加
        merged.append(merged_t)

    # 已 rejected 的 tag, 新抽没抽到也保留 (员工说过不要, 别再问)
    for old_t in existing:
        key = (old_t.get("tag") or "").lower()
        if key and key not in seen and old_t.get("status") == STATUS_REJECTED:
            merged.append(old_t)

    return merged


# ─── tool entries ────────────────────────────────────────────────────


def tool_extract_expertise(
    args: dict[str, Any],
    llm_call_fn: Callable[[str], str],
    journal_text: str = "",
) -> dict[str, Any]:
    """catfish_extract_expertise: 跑一次抽取, 写 yaml.

    args:
      max_tags: int (default 20)
      include_session_history: bool (default false) — 后续扩展
    """
    max_tags = int(args.get("max_tags") or MAX_TAGS_PER_EXTRACT)
    if not journal_text or not journal_text.strip():
        return {
            "ok": False,
            "error": "journal 为空, 没东西可抽. 用一段时间 (≥ 1 周) 让鲶鱼累积 journal 再来.",
        }
    new_tags = extract_from_journal(journal_text, llm_call_fn, max_tags=max_tags)
    if not new_tags:
        return {
            "ok": False,
            "error": "LLM 没抽出有效 tag (可能 journal 太短 / LLM 返格式错). 看 log.",
        }
    existing = load_expertise().get("tags") or []
    merged = merge_with_existing(new_tags, existing)
    data = {
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "source": "employee_journal.md",
        "auto_review_pending": any(t.get("status") == STATUS_PENDING for t in merged),
        "tags": merged,
    }
    ok = save_expertise(data)
    return {
        "ok": ok,
        "extracted_count": len(new_tags),
        "merged_count": len(merged),
        "pending_review_count": sum(1 for t in merged if t.get("status") == STATUS_PENDING),
        "confirmed_count": sum(1 for t in merged if t.get("status") == STATUS_CONFIRMED),
        "path": str(EXPERTISE_PATH),
        "summary": (
            f"📚 抽出 {len(new_tags)} 个新 tag, 合并后共 {len(merged)} 个. "
            f"{sum(1 for t in merged if t.get('status') == STATUS_PENDING)} 个待你 review "
            f"(catfish_confirm_expertise tag='X' status='confirmed' 通过 / 'rejected' 拒).\n\n"
            + "\n".join(
                f"  - {t['tag']} (conf={t.get('confidence', 0):.2f}, "
                f"evidence={t.get('evidence_count', 0)}, status={t.get('status')})"
                for t in merged[:15]
            )
        ),
    }


def tool_list_expertise(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_list_expertise: 看当前 yaml.

    args:
      status_filter: pending / confirmed / rejected / null=all
    """
    status_filter = (args.get("status_filter") or "").strip().lower() or None
    if status_filter and status_filter not in _VALID_STATUSES:
        return {"ok": False, "error": f"status_filter 必须是 {_VALID_STATUSES} 或 null"}
    data = load_expertise()
    tags = data.get("tags") or []
    if status_filter:
        tags = [t for t in tags if t.get("status") == status_filter]
    return {
        "ok": True,
        "count": len(tags),
        "tags": tags,
        "extracted_at": data.get("extracted_at", ""),
        "auto_review_pending": data.get("auto_review_pending", True),
        "summary": (
            f"📋 {len(tags)} 个专长 tag" + (f" (filter={status_filter})" if status_filter else "")
        ),
    }


def tool_confirm_expertise(args: dict[str, Any]) -> dict[str, Any]:
    """catfish_confirm_expertise: 员工 review 一个 tag (👍/👎/✏️).

    args:
      tag: str (必填)  — tag 名 (大小写不敏感)
      status: pending / confirmed / rejected
      new_tag: str (可选) — 改 tag 名 (例 "资质" → "资质管理")
      add_aliases: list[str] (可选) — 加同义词
    """
    tag_name = (args.get("tag") or "").strip()
    if not tag_name:
        return {"ok": False, "error": "tag 必填"}
    new_status = (args.get("status") or "").strip().lower()
    if new_status and new_status not in _VALID_STATUSES:
        return {"ok": False, "error": f"status 必须是 {_VALID_STATUSES}"}
    new_tag = (args.get("new_tag") or "").strip()
    add_aliases = args.get("add_aliases") or []
    if not isinstance(add_aliases, list):
        add_aliases = []

    data = load_expertise()
    tags: list[dict[str, Any]] = data.get("tags") or []
    target = None
    for t in tags:
        if (t.get("tag") or "").lower() == tag_name.lower():
            target = t
            break
    if target is None:
        return {
            "ok": False,
            "error": f"找不到 tag '{tag_name}'. 调 catfish_list_expertise 看可用 tag.",
        }

    if new_status:
        target["status"] = new_status
    if new_tag and MIN_TAG_LEN <= len(new_tag) <= MAX_TAG_LEN:
        target["tag"] = new_tag
    if add_aliases:
        cur = target.get("aliases") or []
        for a in add_aliases:
            a_str = str(a).strip()
            if a_str and a_str not in cur:
                cur.append(a_str)
        target["aliases"] = cur[:5]
    target["last_reviewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    data["auto_review_pending"] = any(t.get("status") == STATUS_PENDING for t in tags)
    save_expertise(data)
    return {
        "ok": True,
        "tag": target,
        "summary": (
            f"✓ '{tag_name}' → status={target.get('status')}"
            + (f", 改名 → '{new_tag}'" if new_tag else "")
            + (f", 加 alias {add_aliases}" if add_aliases else "")
        ),
    }


# ─── Federation 出口 (BL-FED2.2 黄页用) ──────────────────────────────


def export_for_registry() -> list[str]:
    """给 BL-FED2.2 黄页用. **隐私边界**:
    - 只返 status=confirmed 的 tag 字符串列表
    - 不返 confidence / evidence / aliases / source
    - 中央 identity-server 调本函数前必须验员工身份 (sub) 一致
    """
    data = load_expertise()
    return [
        t.get("tag", "")
        for t in (data.get("tags") or [])
        if t.get("status") == STATUS_CONFIRMED and t.get("tag")
    ]


__all__ = [
    "EXPERTISE_PATH",
    "STATUS_PENDING",
    "STATUS_CONFIRMED",
    "STATUS_REJECTED",
    "load_expertise",
    "save_expertise",
    "extract_from_journal",
    "merge_with_existing",
    "tool_extract_expertise",
    "tool_list_expertise",
    "tool_confirm_expertise",
    "export_for_registry",
]
