"""catfish_wiki_publish 工具实现 (P3.3.18 Phase 2, 6/10).

把员工本机一条 wiki 笔记 publish 到部门 wiki-hub.

跟 catfish_skill_publish 关键差异:
1. **输入**: 单 markdown 文件路径 (~/.catfish/wiki/entities/老李.md), 不是 skill 目录
2. **endpoint**: POST gateway `/v1/wiki/documents` (JSON body), 不是 multipart
3. **扫描行为**:
   - **凭据扫**: 命中**拒** (跟 skill 一致, wiki 写密码是 mistake)
   - **PII / 内网 URL / 敏感词**: 命中**只警告** (默认 ack_warnings=false), 员工
     看了 LLM 转告的 warning 决定 ack 后, LLM 加 acknowledge_warnings=true retry.
   - 跟 skill_publish 默认 "PII / intranet 拒" 行为不同 — wiki 是知识笔记,
     客户名 / 项目细节是它的本质内容, 不能自动拒, 也不能自动 redact (脱敏
     后 wiki 就没意义了). 让员工自己拍.
4. **敏感词扫 (新)**: 从 ~/.catfish/wiki/sensitive_terms.txt 读员工自配 list,
   客户名 / 项目代号 / 关键人姓名等命中给警告.

namespace (9/30 起): 不再由调用方传. hub 按发布者身份里的 department 定成
`dept/<部门>`, 员工只能发到自己部门. 原来手填 `dept/finance` 那条路从来没通过
(路由只接一个路径段, 含 `/` 的 namespace 一律 405), 而且跟身份里的部门对不上,
没法做部门隔离.

重发 (9/30): 每条本机 wiki 第一次发出去拿到的 file_id 记在
~/.catfish/wiki_publish_state.json, 之后再发同一个文件自动带上, hub 更新同一条,
已安装的同事那边会看到"有更新". 之前每发一次都是新 file_id, 部门里就多一条重复的.

Manifesto 公理 2 例外条款明示允许 (员工主动 push), 公理 4 (无反向触及员工本机).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from . import skill_publish  # 复用 _scan_sensitive / _scan_pii / _scan_intranet

logger = logging.getLogger("catfish.tool_bridge.wiki_publish")


#: gateway URL — 跟 skill_publish 同 (复用 GATEWAY_URL 常量)
GATEWAY_URL = skill_publish.GATEWAY_URL

#: OAuth id_token 路径 — 跟 skill_publish 同
OAUTH_ID_TOKEN_PATH = skill_publish.OAUTH_ID_TOKEN_PATH

#: 员工自配敏感词 list (一行一个, # 开头注释)
def _catfish_home() -> Path:
    from .wiki_files import _catfish_home as h  # noqa: PLC0415  (11/08: 跟搜索 / 读取同一个根, CATFISH_HOME 设了就用它)
    return h()


def _sensitive_terms_path() -> Path:
    return _catfish_home() / "wiki" / "sensitive_terms.txt"

#: 本机 wiki rel_path → 上次发布拿到的 {file_id, namespace, updated_at}
def _publish_state_path() -> Path:
    return _catfish_home() / "wiki_publish_state.json"


def _load_publish_state() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_publish_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("读 %s 失败 (当作没发过): %s", _publish_state_path(), e)
        return {}
    return data if isinstance(data, dict) else {}


def _save_publish_state(rel_path: str, entry: dict[str, Any]) -> None:
    state = _load_publish_state()
    state[rel_path] = entry
    try:
        _publish_state_path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _publish_state_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_publish_state_path())
    except Exception as e:
        # 发布本身已成功; 记不下来的后果是下次重发变成新条目, 不值得让这次报错
        logger.warning("写 %s 失败: %s", _publish_state_path(), e)


def _read_id_token() -> str | None:
    return skill_publish._read_id_token()


def wiki_doc_url(namespace: str, file_id: str, suffix: str = "") -> str:
    """`dept/<部门>` + file_id → gateway 上这条的 URL. 部门名可能是中文, 逐段编码."""
    from urllib.parse import quote  # noqa: PLC0415

    segs = [quote(seg, safe="") for seg in namespace.split("/")]
    return f"{GATEWAY_URL}/v1/wiki/documents/{'/'.join(segs)}/{quote(file_id, safe='')}{suffix}"


def _load_sensitive_terms() -> list[str]:
    """从 ~/.catfish/wiki/sensitive_terms.txt 读员工自配敏感词. 不存在返空 list.

    文件格式: 一行一个词, # 开头注释忽略, 空行跳过.
    """
    if not _sensitive_terms_path().exists():
        return []
    terms: list[str] = []
    try:
        for line in _sensitive_terms_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            terms.append(line)
    except Exception as e:
        logger.warning("读 sensitive_terms.txt 失败: %s", e)
        return []
    return terms


def _scan_sensitive_terms(text: str, terms: list[str]) -> list[dict[str, str]]:
    """扫敏感词 — 大小写不敏感, 子串匹配. 返命中 list: [{term, context}]."""
    if not terms or not text:
        return []
    hits: list[dict[str, str]] = []
    text_lower = text.lower()
    for term in terms:
        if not term:
            continue
        idx = text_lower.find(term.lower())
        if idx < 0:
            continue
        # context 取前后各 20 字
        start = max(0, idx - 20)
        end = min(len(text), idx + len(term) + 20)
        ctx = text[start:end].replace("\n", " ")
        hits.append({"term": term, "context": ctx})
    return hits


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """split frontmatter + body. 返 (frontmatter_yaml, body_md). 没 frontmatter 返 ("", text)."""
    if not text.startswith("---"):
        return ("", text)
    end_idx = text.find("\n---", 3)
    if end_idx < 0:
        return ("", text)
    fm = text[3:end_idx].strip()
    body = text[end_idx + 4:].lstrip("\n")
    return (fm, body)


def _extract_field(fm: str, key: str) -> str:
    """从 frontmatter 抽单行 `key: value` 字段. 没找到返空."""
    pat = re.compile(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    m = pat.search(fm)
    if not m:
        return ""
    return m.group(1).strip().strip("\"'")


def _infer_kind_from_path(rel_path: str) -> str:
    """从 wiki rel_path 推 kind. wiki/entities/X.md → entity, etc."""
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        d = parts[-2].lower()
        if d == "entities":
            return "entity"
        if d == "concepts":
            return "concept"
        if d == "queries":
            return "query"
    return "entity"  # 默认


def scan_text(name: str, text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """发布前扫描 (11/09 抽出来, 分享对话框的预检 wiki_share.plan 也用): (凭据命中 → 拒发, 其余警告)。"""
    file_map = {name: text.encode("utf-8")}
    leaks = skill_publish._scan_sensitive(file_map)
    warnings: list[dict[str, Any]] = []
    pii_hits = skill_publish._scan_pii(file_map)
    if pii_hits:
        warnings.append({
            "category": "pii",
            "hits": pii_hits,
            "advice": "wiki 含身份证 / 手机 / 工号 / 银行卡. 员工你确认要 share 给部门吗?",
        })
    intranet_hits = skill_publish._scan_intranet(file_map)
    if intranet_hits:
        warnings.append({
            "category": "intranet",
            "hits": intranet_hits,
            "advice": "wiki 含内网 IP / hostname. 员工你确认要 share 给部门吗?",
        })
    terms = _load_sensitive_terms()
    if terms:
        term_hits = _scan_sensitive_terms(text, terms)
        if term_hits:
            warnings.append({
                "category": "sensitive_terms",
                "hits": term_hits,
                "advice": (
                    "wiki 命中员工自配敏感词 (~/.catfish/wiki/sensitive_terms.txt). "
                    "员工你确认要 share 客户/项目信息给部门吗?"
                ),
            })
    return leaks, warnings


def _read_source_doc(wiki_rel_path: str) -> tuple[dict[str, Any] | None, str | None]:
    """11/09: 原件 (raw/sources/…) 当单独一页发 —— 知识页的来源同事那边点得开。
    返 ({title, kind, fm, body}, None) 或 (None, 错误)。标题固定「原件 · 名称」, 知识页里的来源改写成同名链接。"""
    from . import wiki_share  # noqa: PLC0415
    p = wiki_share.source_path(wiki_rel_path)
    if p is None:
        return None, f"原件不存在或不是文本: {wiki_rel_path}"
    if p.stat().st_size > wiki_share.MAX_SOURCE_BYTES:
        return None, "原件超过 1MB, 不能当一页分享"
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        return None, "原件是空的"
    _, raw_body = _parse_frontmatter(raw)   # 原件自己带的头部 (导入时的元数据) 不发
    title = wiki_share.source_doc_title(wiki_rel_path)
    fm = "\n".join([
        "type: concept",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        "concept_type: source_original",
        "tags: [原件]",
    ])
    return {"title": title, "kind": "concept", "fm": fm, "body": raw_body}, None


def wiki_publish(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 读 + 扫描 + POST JSON 到 gateway /v1/wiki/documents.

    参数:
      wiki_rel_path (必): 员工本机 wiki rel_path, 必须以 'wiki/' 开头
        (e.g. 'wiki/entities/老李.md', 'wiki/concepts/CSMM-4.md')
      acknowledge_warnings (可选, 默认 false): true 时跳 PII / 内网 / 敏感词警告
        强 publish. 第一次调若拿到 warnings, LLM 必须**转告员工**, 员工
        confirm 后才能加这个参数 retry.
      file_id (可选): 一般不用传 —— 同一个文件上次发布的 file_id 会自动带上.
      namespace: 已废弃, 传了也忽略 (hub 按身份里的部门定).
      scope (可选, 11/09): department (本部门) | company (全公司: 本部门马上可见, 管理员审过后全公司可见).
        不传 = 沿用这个文件上次的范围, 从没发过就是 department.

    成功返:
      {ok:true, file_id, namespace, hub_url, published_at, warnings?}
    警告 (默认拒):
      {ok:false, scan_phase, warnings:[...], acknowledge_warnings_available:true}
    凭据撞 (永拒):
      {ok:false, scan_phase:"credentials", leaks:[...]}
    """
    wiki_rel_path = (args.get("wiki_rel_path") or "").strip()
    # 11/08: 警告只认员工在界面上点的确认 (Companion 走 tools/dispatch 会带 _from_ui); 小鲶在对话里自己加
    # acknowledge_warnings=true 不算 —— 以前这一步由小鲶转述, 它说员工同意了就能发出去, 代码上没人拦。
    from_ui = bool(args.get("_from_ui"))
    ack_warnings = bool(args.get("acknowledge_warnings", False)) and from_ui
    llm_acked = bool(args.get("acknowledge_warnings", False)) and not from_ui
    file_id = (args.get("file_id") or "").strip()
    scope = str(args.get("scope") or "").strip()
    if scope and scope not in ("department", "company"):
        return {"ok": False, "error": "scope 只能是 department (本部门) 或 company (全公司)"}

    # 校验
    if not wiki_rel_path:
        return {"ok": False, "error": "wiki_rel_path 必填 (例: wiki/entities/老李.md)"}
    if ".." in wiki_rel_path or wiki_rel_path.startswith("/"):
        return {"ok": False, "error": f"wiki_rel_path 不合法: {wiki_rel_path}"}
    source_doc: dict[str, Any] | None = None
    if wiki_rel_path.startswith("raw/sources/"):
        source_doc, err = _read_source_doc(wiki_rel_path)
        if err:
            return {"ok": False, "error": err}
    elif not wiki_rel_path.startswith("wiki/"):
        return {
            "ok": False,
            "error": f"wiki_rel_path 必须以 'wiki/' 开头 (拿到 {wiki_rel_path})",
        }
    if source_doc is not None:
        title, kind = source_doc["title"], source_doc["kind"]
        fm_yaml, body_md = source_doc["fm"], source_doc["body"]
        scan_name = wiki_rel_path.rsplit("/", 1)[-1]
    else:
        # 读 wiki 文件
        catfish_home = _catfish_home()
        abs_path = (catfish_home / wiki_rel_path).resolve()
        # 安全: resolve 后必须仍在 catfish_home 下
        try:
            abs_path.relative_to(catfish_home.resolve())
        except ValueError:
            return {"ok": False, "error": f"wiki_rel_path 路径逃逸: {wiki_rel_path}"}
        if not abs_path.exists() or not abs_path.is_file():
            return {"ok": False, "error": f"wiki 文件不存在: {abs_path}"}

        try:
            text = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            return {"ok": False, "error": f"读 wiki 文件失败: {e}"}

        if not text.strip():
            return {"ok": False, "error": "wiki 文件为空"}

        if len(text.encode("utf-8")) > 1024 * 1024:
            return {"ok": False, "error": f"wiki 文件超 1MB 上限 ({len(text)} bytes)"}

        # 解 frontmatter
        fm_yaml, body_md = _parse_frontmatter(text)
        # 10/12: 客户页 (时间线里是客户原话) 只在本机; 给主管看走「发布给主管」的结构化摘要 (sales_publish.py)
        if _extract_field(fm_yaml, "entity_type") == "customer":
            return {"ok": False, "error": "客户档案只存在你本机, 不能整页分享到部门 (里面有客户原话)。"
                                          "要让主管看到进展, 用 设置 →「🤝 销售跟进」→「发布给主管」, 只发阶段 / 金额 / 下一步。"}
        title = _extract_field(fm_yaml, "title")
        if not title:
            # title fallback: 用文件 stem
            title = abs_path.stem
        kind = _extract_field(fm_yaml, "type") or _infer_kind_from_path(wiki_rel_path)
        if kind not in ("entity", "concept", "query"):
            kind = "entity"
        # 11/09: 发出去那份的来源换成人话 (本机路径 / 对话编号同事那边没有); 一起分享了的原件换成 [[原件 · 名称]]。本机文件不动。
        from . import wiki_share  # noqa: PLC0415
        attached = {wiki_share.source_key(r) for r in (args.get("attached_sources") or []) if isinstance(r, str)}
        fm_yaml, body_md = wiki_share.rewrite_for_share(fm_yaml, body_md, attached)
        scan_name = abs_path.name

    # 扫的是改写后真正发出去的那份。凭据永拒; 个人信息 / 内网地址 / 自配敏感词警告
    leaks, warnings = scan_text(scan_name, f"---\n{fm_yaml}\n---\n\n{body_md}")
    if leaks:
        return {
            "ok": False,
            "error": (
                "wiki 内含疑似凭据 (api_key / password / 私钥), 拒绝 publish:\n  "
                + "\n  ".join(leaks)
                + "\n凭据永不能上 hub. 本机改成 keychain:// ref 或环境变量后再发."
            ),
            "scan_phase": "credentials",
        }

    if warnings and not ack_warnings:
        return {
            "ok": False,
            "scan_phase": "warnings",
            "warnings": warnings,
            "acknowledge_warnings_available": False,
            # 对话里会出现「我看过警告, 确认发布」按钮 (ChatWikiPublish), 员工点了才真发
            "confirm_in_ui": {"wiki_rel_path": wiki_rel_path, "warnings": warnings},
            "error": (
                f"发布前扫描有 {len(warnings)} 类警告 (内容不会自动脱敏). "
                + ("你不能替员工确认: " if llm_acked else "")
                + "把 warnings 用大白话转告员工, 请他在这条消息下面点「确认发布」(或在知识体系页里分享), "
                "点了才会发出去; 不要再带 acknowledge_warnings 重试."
            ),
        }

    # 拿 OAuth id_token
    token = _read_id_token()
    if not token:
        return {
            "ok": False,
            "error": (
                f"OAuth id_token 不存在 ({OAUTH_ID_TOKEN_PATH}). 在 Companion "
                "里点登录走 OIDC 流, 之后 publish 才能用真员工身份."
            ),
        }

    # 拼 payload + POST
    payload = {
        "filename": wiki_rel_path,
        "title": title,
        "kind": kind,
        "frontmatter_yaml": fm_yaml,
        "body_md": body_md,
    }
    previous = _load_publish_state().get(wiki_rel_path) or {}
    scope = scope or str(previous.get("scope") or "department")
    payload["scope"] = scope
    if not file_id:
        file_id = str(previous.get("file_id") or "")
    if file_id:
        payload["file_id"] = file_id

    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/wiki/documents"
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text[:500]
        return {"ok": False, "error": f"gateway {resp.status_code}: {detail}"}

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": f"gateway 返非 json: {resp.text[:200]}"}

    namespace = str(data.get("namespace") or "")
    new_file_id = str(data.get("file_id") or "")
    if namespace and new_file_id:
        _save_publish_state(wiki_rel_path, {
            "file_id": new_file_id,
            "namespace": namespace,
            "updated_at": data.get("updated_at"),
            "scope": scope,
        })
    republished = bool(previous) and previous.get("file_id") == new_file_id \
        and previous.get("namespace") == namespace
    result = {
        "ok": True,
        "namespace": namespace,
        "file_id": new_file_id,
        "republished": republished,
        "published_at": data.get("published_at"),
        "updated_at": data.get("updated_at"),
        "size_bytes": data.get("size_bytes"),
        "hub_url": wiki_doc_url(namespace, new_file_id),
        "scope": scope,
        "company_status": data.get("company_status"),
    }
    # 显式说明发出去的是什么 (帮 LLM 转告员工)
    result["summary"] = (
        f"已{'更新' if republished else '发布'} wiki '{title}' 到 {namespace}. "
        + ("本部门同事现在就能看到; 全公司可见要等管理员审过, 之前其他部门看不到. " if scope == "company" else "")
        + "已安装的同事会看到有更新. "
        f"已 pull 副本不受未来 unpublish 影响 (manifesto 公理 4)."
    )
    if warnings:
        result["acknowledged_warnings"] = [w["category"] for w in warnings]
    return result
