"""事实补丁系统 — BL-Q3-FACT P0 MVP (5/10 鸿波 'a16z continual learning 启发, 现在就做').

# 设计

详 docs/CATFISH-FACT-PATCH-DESIGN.md (BL-Q3-FACT v0.1 设计草案).

# Pipeline (5 步)

  1. upload     员工 IT/合规上传变更文件 (multipart PDF/Word/MD/TXT/邮件)
                → 存 ~/.catfish/facts/<id>/raw.<ext> + 返 fact_id (status=uploaded)  # noqa: BOUNDARY
  2. extract    LLM 解析变更文件 → 提"事实点"列表 (title/summary/keywords/raw_quotes)
                → 落 ~/.catfish/facts/<id>/fact.json (status=extracted)  # noqa: BOUNDARY
  3. find-impact 拉 skills-hub 全部 skill, LLM 找受影响候选 (附 confidence)
                → 落 ~/.catfish/facts/<id>/impacts.jsonl (status=analyzed)  # noqa: BOUNDARY
  4. generate-patches  对每个高 confidence 受影响 skill, 调 BL-MM13
                catfish_propose_skill_revision 生成 patch
                → 落 ~/.catfish/facts/<id>/patches.jsonl (status=patches_ready)  # noqa: BOUNDARY
  5. approve    走 SkillRevisionCard 审批 (复用 BL-MM14, 不在本 router 实现)

# P0 范围

- PG 暂用 jsonl 临时落 (~/.catfish/facts/<fact_id>/), Q3 P1 迁 PG.  # noqa: BOUNDARY
- 只做单 skill 独立分析, 跨 skill 依赖图留 Q3 P1.
- 不做自动扫公司知识库, 全手动上传.
- RBAC: admin / sysadmin 才能上传/分析, employee 看不到.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from . import facts_db  # BL-Q3-FACT PG 统一 (5/10): PG 主 + jsonl 兜底
from .auth import User, get_current_user

logger = logging.getLogger("catfish.gateway.facts")

router = APIRouter(prefix="/api/facts", tags=["facts"])

# ── 落盘位置 ─────────────────────────────────────
# ~/.catfish/facts/<fact_id>/  # noqa: BOUNDARY
#   raw.<ext>           原始上传文件
#   meta.json           fact_change 元信息
#   fact.json           LLM 解析出的事实点
#   impacts.jsonl       受影响 skill 列表 (每行一个 impact 记录)
#   patches.jsonl       生成的 patch 列表 (每行一个)
#   audit.jsonl         本 fact 的所有操作审计

# BL-CENTRAL-EDGE-BOUNDARY (5/26): 生产应配 CATFISH_FACTS_DIR env 指到 PG-mount
# / 对象存储 (Q3 BL-Q3-FACT-PG-MIGRATE). dev / 单测 fallback Path.home (员工本机)
# 仅供本地手动 demo / curl 调 endpoint 测试用; 真上 SaaS 生产必须显式 set env.
FACTS_DIR = (
    Path(os.environ["CATFISH_FACTS_DIR"]).expanduser()
    if os.environ.get("CATFISH_FACTS_DIR")
    else Path.home() / ".catfish" / "facts"  # noqa: BOUNDARY (Q3 PG 迁移前 dev 兜底)
)
FACTS_DIR.mkdir(parents=True, exist_ok=True)
if not os.environ.get("CATFISH_FACTS_DIR"):
    logger.warning(
        "facts_router: CATFISH_FACTS_DIR env 未配, fallback %s (dev only). "
        "SaaS 生产必须 set env 指到 PG mount / 对象存储 (Q3 BL-Q3-FACT-PG-MIGRATE).",
        FACTS_DIR,
    )

# ── 文件大小 / 类型限制 ─────────────────────────
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB (政策文件一般几页 PDF, 远不到)
ALLOWED_EXTS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"}


# ── RBAC ────────────────────────────────────────
def _require_admin(user: User) -> None:
    """admin 或 sysadmin 才能动 facts 系统 (P2 加合规专属角色)."""
    if not user.is_admin():  # is_admin() 已含 sysadmin (BL-ARCH1 P2)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role={user.role} 不能访问 FACT 系统 (需 admin / sysadmin)",
        )


# ── 文件操作 helpers ───────────────────────────
def _fact_dir(fact_id: str) -> Path:
    """单个 fact 的存放目录, 不存在自动建."""
    # 简单防注入: fact_id 必须是 UUID 格式
    try:
        uuid.UUID(fact_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"fact_id 格式错: {e}") from e
    d = FACTS_DIR / fact_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audit(fact_id: str, action: str, by_user: str, meta: dict | None = None) -> None:
    """追加一条操作审计 — PG 主, jsonl 兜底.

    BL-Q3-FACT PG 统一 (5/10): 跟 mcp-registry / skills-hub 同模板, 双写防失败.
    PG 失败 → 仍写 jsonl (本地 debug + 离线), 不阻塞业务.
    """
    facts_db.pg_audit(fact_id, action, by_user, meta)
    # jsonl 永远写 (PG 失败兜底 + 本地可读 + dev 模式不要求 PG)
    audit_path = _fact_dir(fact_id) / "audit.jsonl"
    rec = {
        "ts_ms": int(time.time() * 1000),
        "action": action,
        "by_user": by_user,
        "meta": meta or {},
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read_meta(fact_id: str) -> dict:
    p = _fact_dir(fact_id) / "meta.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"fact {fact_id} 不存在")
    return json.loads(p.read_text(encoding="utf-8"))


def _write_meta(fact_id: str, meta: dict) -> None:
    """写 meta.json (jsonl 端) + upsert PG fact_changes 表."""
    (_fact_dir(fact_id) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # PG 同步 (失败不阻塞, log warn — facts_db 内部已 try/except)
    facts_db.pg_upsert_fact(meta)


# ── Endpoint 1: POST /api/facts/upload ─────────
@router.post("/upload")
async def upload_fact(
    file: UploadFile = File(...),
    title: str = Form(""),
    effective_date: str = Form(""),  # ISO 日期, 空表示"立即生效"
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """上传变更文件 (PDF/Word/MD/TXT). 返 fact_id, status=uploaded."""
    _require_admin(user)

    # 文件类型检查
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 {ext}. 支持: {', '.join(sorted(ALLOWED_EXTS))}",
        )

    # 读 + size 校验
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        mb = len(content) / 1024 / 1024
        raise HTTPException(
            status_code=413,
            detail=f"文件太大 ({mb:.1f} MB > {MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MB)",
        )
    if len(content) < 100:
        raise HTTPException(status_code=400, detail="文件太小, 可能是空文件")

    # 生成 fact_id + 存盘
    fact_id = str(uuid.uuid4())
    fact_dir = _fact_dir(fact_id)
    raw_path = fact_dir / f"raw{ext}"
    raw_path.write_bytes(content)

    # 写 meta.json
    now_ms = int(time.time() * 1000)
    meta = {
        "id": fact_id,
        "title": title or filename,
        "original_filename": filename,
        "raw_path": str(raw_path),
        "ext": ext,
        "size_bytes": len(content),
        "effective_date": effective_date or None,
        "uploaded_by": user.sub,
        "uploaded_at_ms": now_ms,
        "status": "uploaded",  # uploaded → extracted → analyzed → patches_ready → approved/dismissed
    }
    _write_meta(fact_id, meta)
    _audit(fact_id, "upload", user.sub, {"filename": filename, "size": len(content)})

    logger.info(
        "facts.upload: id=%s by=%s file=%s size=%d ext=%s",
        fact_id, user.sub, filename, len(content), ext,
    )
    return {"fact_id": fact_id, "status": "uploaded", "meta": meta}


# ── Endpoint 2: GET /api/facts ──────────────────
@router.get("")
async def list_facts(
    user: User = Depends(get_current_user),
    limit: int = 50,
) -> dict[str, Any]:
    """列所有已上传的 fact (admin/sysadmin 看全部).

    BL-Q3-FACT PG 统一 (5/10): PG 主, jsonl 兜底.
    PG 可用 → 用 SQL 查 (有索引快); 不可用 → 扫 jsonl 目录.
    """
    _require_admin(user)

    # 优先走 PG
    pg_items = facts_db.pg_list_facts(limit=limit)
    if pg_items is not None:
        return {"facts": pg_items, "count": len(pg_items), "source": "pg"}

    # PG 不可用 (dev / 没配 CATFISH_DB_URL / PG 挂) → jsonl 兜底
    items: list[dict] = []
    if FACTS_DIR.exists():
        # 按目录 mtime 倒序 (最新的上)
        dirs = sorted(
            (d for d in FACTS_DIR.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for d in dirs[:limit]:
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                # 加几个 derived 字段方便前端
                impacts_count = _count_jsonl(d / "impacts.jsonl")
                patches_count = _count_jsonl(d / "patches.jsonl")
                meta["impacts_count"] = impacts_count
                meta["patches_count"] = patches_count
                items.append(meta)
            except Exception as e:  # noqa: BLE001
                logger.warning("facts.list: 跳过坏目录 %s: %s", d, e)
                continue
    return {"facts": items, "count": len(items), "source": "jsonl"}


def _count_jsonl(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(1 for _ in p.open("r", encoding="utf-8"))


# ── Endpoint 3: GET /api/facts/{fact_id} ────────
@router.get("/{fact_id}")
async def get_fact(
    fact_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """获取 fact 详情. PG 主, jsonl 兜底.

    BL-Q3-FACT PG 统一 (5/10): SQL 一次拉全部 (meta + facts + impacts + patches + audit)
    比 jsonl 多次 read 快. PG 不可用 → jsonl 兜底.
    """
    _require_admin(user)

    # 优先 PG
    pg_detail = facts_db.pg_get_fact_detail(fact_id)
    if pg_detail is not None:
        return pg_detail

    # jsonl 兜底
    meta = _read_meta(fact_id)
    fact_dir = _fact_dir(fact_id)

    # 事实点 (extract 阶段产物)
    fact_json_path = fact_dir / "fact.json"
    facts = json.loads(fact_json_path.read_text(encoding="utf-8")) if fact_json_path.exists() else None

    # 受影响 skill (find-impact 阶段产物)
    impacts = _read_jsonl(fact_dir / "impacts.jsonl")

    # patches (generate-patches 阶段产物)
    patches = _read_jsonl(fact_dir / "patches.jsonl")

    # audit
    audit = _read_jsonl(fact_dir / "audit.jsonl")

    return {
        "meta": meta,
        "facts": facts,
        "impacts": impacts,
        "patches": patches,
        "audit": audit,
    }


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.open("r", encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning("jsonl 坏行 %s: %s", p, e)
    return out


# ── Endpoint 4: POST /api/facts/{fact_id}/extract ──────
# 调 LLM 解析变更文件 → 提取事实点
# 实现细节放 facts_pipeline.py (本 router 只管 HTTP, pipeline 管 LLM 逻辑)

@router.post("/{fact_id}/extract")
async def extract_fact(
    fact_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """LLM 解析变更文件 → 提事实点列表. 更新 status=extracted."""
    _require_admin(user)
    meta = _read_meta(fact_id)
    if meta["status"] not in {"uploaded", "extracted"}:
        # 已经分析过的也允许重跑 (extract 是幂等的, 后续 stage 会覆盖)
        logger.info("facts.extract: 重跑 fact %s (旧 status=%s)", fact_id, meta["status"])

    from .facts_pipeline import run_extract  # noqa: PLC0415 (避免循环依赖)
    facts = await run_extract(meta, triggered_by=user.sub)

    fact_dir = _fact_dir(fact_id)
    (fact_dir / "fact.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # BL-Q3-FACT PG 统一 (5/10): 同步 facts_json 到 PG
    facts_db.pg_write_facts_json(fact_id, facts)
    meta["status"] = "extracted"
    meta["extracted_at_ms"] = int(time.time() * 1000)
    _write_meta(fact_id, meta)
    _audit(fact_id, "extract", user.sub, {"facts_count": len(facts.get("facts", []))})

    return {"fact_id": fact_id, "status": "extracted", "facts": facts}


# ── Endpoint 5: POST /api/facts/{fact_id}/analyze ──────
# 找受影响 skill + 生成 patches (合并 P0 简化为一步, P1 拆开)

@router.post("/{fact_id}/analyze")
async def analyze_fact(
    fact_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """LLM 找受影响 skill + 生成 patches. 更新 status=patches_ready.

    前置: fact 已 extract (有 fact.json). 否则自动先跑 extract.
    """
    _require_admin(user)
    meta = _read_meta(fact_id)
    fact_dir = _fact_dir(fact_id)

    # 自动 extract (如果还没)
    if not (fact_dir / "fact.json").exists():
        from .facts_pipeline import run_extract  # noqa: PLC0415
        facts = await run_extract(meta, triggered_by=user.sub)
        (fact_dir / "fact.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        facts_db.pg_write_facts_json(fact_id, facts)  # BL-Q3-FACT PG 统一
        meta["status"] = "extracted"
        _write_meta(fact_id, meta)
        _audit(fact_id, "extract (auto)", user.sub, {"facts_count": len(facts.get("facts", []))})

    facts = json.loads((fact_dir / "fact.json").read_text(encoding="utf-8"))

    from .facts_pipeline import run_find_impact, run_generate_patches  # noqa: PLC0415

    impacts = await run_find_impact(meta, facts, user)
    with (fact_dir / "impacts.jsonl").open("w", encoding="utf-8") as f:
        for imp in impacts:
            f.write(json.dumps(imp, ensure_ascii=False) + "\n")
    facts_db.pg_replace_impacts(fact_id, impacts)  # BL-Q3-FACT PG 统一
    _audit(fact_id, "find_impact", user.sub, {"impacts_count": len(impacts)})

    patches = await run_generate_patches(meta, facts, impacts, user)
    with (fact_dir / "patches.jsonl").open("w", encoding="utf-8") as f:
        for p in patches:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    facts_db.pg_replace_patches(fact_id, patches)  # BL-Q3-FACT PG 统一
    _audit(fact_id, "generate_patches", user.sub, {"patches_count": len(patches)})

    meta["status"] = "patches_ready"
    meta["analyzed_at_ms"] = int(time.time() * 1000)
    meta["impacts_count"] = len(impacts)
    meta["patches_count"] = len(patches)
    _write_meta(fact_id, meta)

    return {
        "fact_id": fact_id,
        "status": "patches_ready",
        "impacts_count": len(impacts),
        "patches_count": len(patches),
        "impacts": impacts,
        "patches": patches,
    }


# ── Endpoint 6: DELETE /api/facts/{fact_id} ────────
@router.delete("/{fact_id}")
async def delete_fact(
    fact_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """删除一个 fact (软删, 改 status=dismissed, 不真删文件方便审计)."""
    _require_admin(user)
    meta = _read_meta(fact_id)
    meta["status"] = "dismissed"
    meta["dismissed_at_ms"] = int(time.time() * 1000)
    meta["dismissed_by"] = user.sub
    _write_meta(fact_id, meta)
    _audit(fact_id, "dismiss", user.sub)
    return {"fact_id": fact_id, "status": "dismissed"}


# ── Endpoint 7: POST /api/facts/{fact_id}/patches/{patch_idx}/approve ────────
# 真接通 SkillRevision — 把 patch 写入 SkillsHub 作为该 skill 的新版本
# version 强制加 .fact-<fact_id[:8]> 后缀避免跟现有版本撞 + 留追溯链.

import re as _re  # noqa: E402

import httpx as _httpx  # noqa: E402


@router.post("/{fact_id}/patches/{patch_idx}/approve")
async def approve_patch(
    fact_id: str,
    patch_idx: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """采纳一个 patch — 写入 SkillsHub 发布新版本 skill.

    实现:
      1. 读 patches.jsonl 第 patch_idx 条
      2. 在 full_new_content frontmatter 强制改 version (加 .fact-<fact_id[:8]> 后缀)
      3. multipart POST 到 skills-hub /skills/<namespace>
      4. 标该 patch status=approved + 改 fact meta
      5. audit
    """
    _require_admin(user)
    fact_dir = _fact_dir(fact_id)
    patches_path = fact_dir / "patches.jsonl"
    if not patches_path.exists():
        raise HTTPException(status_code=404, detail="patches.jsonl 不存在")

    patches = _read_jsonl(patches_path)
    if patch_idx < 0 or patch_idx >= len(patches):
        raise HTTPException(status_code=404, detail=f"patch_idx {patch_idx} 越界 (共 {len(patches)} 条)")
    patch = patches[patch_idx]

    if patch.get("status") == "approved":
        return {"fact_id": fact_id, "patch_idx": patch_idx, "status": "approved", "note": "已经采纳过"}

    # 强制改 frontmatter 的 version: <原 v>.fact-<fact_id[:8]>
    base_version = patch.get("skill_version_base", "1.0")
    fact_short = fact_id.replace("-", "")[:8]
    new_version = f"{base_version}.fact-{fact_short}"

    new_content = _bump_version_in_skill_md(patch["full_new_content"], new_version)
    namespace = patch["skill_namespace"]
    skill_name = patch["skill_name"]

    # multipart POST 到 skills-hub
    # 用 internal token (BL-FIX37) 调本地服务, 不需要员工 OIDC
    files_for_post = {"files": ("SKILL.md", new_content.encode("utf-8"), "text/markdown")}
    headers = {
        "X-Catfish-User-Sub": user.sub,
        "X-Catfish-User-Dept": user.department or "",
        "X-Catfish-User-Role": user.role or "employee",
    }
    # P30 (6/5 鸿波): 走 config.skills_hub.upstream_url, 不再硬编码 127.0.0.1.
    # 让 skills-hub 能跨主机部署 (e.g. k8s 不同 pod / nginx 反代不同 host).
    from .config import get_config  # noqa: PLC0415 避免循环依赖
    skills_hub_base = get_config().skills_hub.upstream_url.rstrip("/")
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{skills_hub_base}/skills/{namespace}",
                files=files_for_post,
                headers=headers,
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"skills-hub publish 失败 (status={resp.status_code}): {resp.text[:300]}",
            )
        hub_result = resp.json()
    except _httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"调 skills-hub 失败 (服务没起?): {e}") from e

    # 改 patch 状态
    now_ms = int(time.time() * 1000)
    patch["status"] = "approved"
    patch["approved_at_ms"] = now_ms
    patch["approved_by"] = user.sub
    patch["published_version"] = new_version
    patch["hub_result"] = hub_result
    patches[patch_idx] = patch
    with patches_path.open("w", encoding="utf-8") as f:
        for p in patches:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    # BL-Q3-FACT PG 统一 (5/10): 同步 patch 状态到 PG
    facts_db.pg_update_patch_status(
        fact_id, patch_idx, status="approved",
        approved_at_ms=now_ms, approved_by=user.sub,
        published_version=new_version,
    )

    # 改 fact meta — 看是否全 approve 了
    approved_count = sum(1 for p in patches if p.get("status") == "approved")
    if approved_count == len(patches):
        meta = _read_meta(fact_id)
        meta["status"] = "approved"
        meta["approved_at_ms"] = now_ms
        _write_meta(fact_id, meta)

    _audit(fact_id, "approve_patch", user.sub, {
        "patch_idx": patch_idx,
        "skill": f"{namespace}/{skill_name}",
        "new_version": new_version,
    })
    logger.info(
        "facts.approve: fact=%s patch=%d skill=%s/%s v=%s",
        fact_id, patch_idx, namespace, skill_name, new_version,
    )
    return {
        "fact_id": fact_id,
        "patch_idx": patch_idx,
        "status": "approved",
        "published_version": new_version,
        "hub_result": hub_result,
    }


# ── Endpoint 8: POST /api/facts/{fact_id}/patches/{patch_idx}/reject ────────

@router.post("/{fact_id}/patches/{patch_idx}/reject")
async def reject_patch(
    fact_id: str,
    patch_idx: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """拒绝一个 patch — 只标 status=rejected, 不发布."""
    _require_admin(user)
    fact_dir = _fact_dir(fact_id)
    patches_path = fact_dir / "patches.jsonl"
    if not patches_path.exists():
        raise HTTPException(status_code=404, detail="patches.jsonl 不存在")

    patches = _read_jsonl(patches_path)
    if patch_idx < 0 or patch_idx >= len(patches):
        raise HTTPException(status_code=404, detail=f"patch_idx {patch_idx} 越界")
    patch = patches[patch_idx]
    now_ms = int(time.time() * 1000)
    patch["status"] = "rejected"
    patch["rejected_at_ms"] = now_ms
    patch["rejected_by"] = user.sub
    patches[patch_idx] = patch
    with patches_path.open("w", encoding="utf-8") as f:
        for p in patches:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    # BL-Q3-FACT PG 统一 (5/10): 同步 reject 状态到 PG
    facts_db.pg_update_patch_status(
        fact_id, patch_idx, status="rejected",
        rejected_at_ms=now_ms, rejected_by=user.sub,
    )

    _audit(fact_id, "reject_patch", user.sub, {
        "patch_idx": patch_idx,
        "skill": f"{patch.get('skill_namespace')}/{patch.get('skill_name')}",
    })
    return {"fact_id": fact_id, "patch_idx": patch_idx, "status": "rejected"}


def _bump_version_in_skill_md(skill_md: str, new_version: str) -> str:
    """改 SKILL.md frontmatter 的 version 行为 new_version. 没 frontmatter 就在头部加.

    SKILL.md 格式约定 (跟 storage._parse_skill_md 一致):
      ---
      name: xxx
      version: 1.0
      description: ...
      ---
      正文...

    或不带 fence (顶部直接 key: value).
    """
    # 标准 frontmatter (--- 包围) 的 version: 改掉
    if skill_md.lstrip().startswith("---"):
        # 找两条 --- 之间的内容
        m = _re.match(r"^(\s*---\n)(.*?)(\n---\n)(.*)$", skill_md, _re.DOTALL)
        if m:
            head, fm, end_fence, body = m.groups()
            new_fm, n = _re.subn(
                r"^(\s*version\s*:\s*).*$",
                rf"\g<1>{new_version}",
                fm,
                count=1,
                flags=_re.MULTILINE,
            )
            if n == 0:
                # 没找到 version 行, 在 fm 末尾追加
                new_fm = fm.rstrip() + f"\nversion: {new_version}\n"
            return head + new_fm + end_fence + body

    # 无 frontmatter — 简单在最顶上加一段
    return f"---\nversion: {new_version}\n---\n\n{skill_md}"
