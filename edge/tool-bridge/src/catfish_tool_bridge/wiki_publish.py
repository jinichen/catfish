"""catfish_wiki_publish 工具实现 (P3.3.18 Phase 2, 6/10).

把员工本机一条 wiki 笔记 publish 到部门 wiki-hub.

跟 catfish_skill_publish 关键差异:
1. **输入**: 单 markdown 文件路径 (~/.catfish/wiki/entities/老李.md), 不是 skill 目录
2. **endpoint**: POST gateway `/v1/wiki/documents/{namespace}` (JSON body), 不是 multipart
3. **扫描行为**:
   - **凭据扫**: 命中**拒** (跟 skill 一致, wiki 写密码是 mistake)
   - **PII / 内网 URL / 敏感词**: 命中**只警告** (默认 ack_warnings=false), 员工
     看了 LLM 转告的 warning 决定 ack 后, LLM 加 acknowledge_warnings=true retry.
   - 跟 skill_publish 默认 "PII / intranet 拒" 行为不同 — wiki 是知识笔记,
     客户名 / 项目细节是它的本质内容, 不能自动拒, 也不能自动 redact (脱敏
     后 wiki 就没意义了). 让员工自己拍.
4. **敏感词扫 (新)**: 从 ~/.catfish/wiki/sensitive_terms.txt 读员工自配 list,
   客户名 / 项目代号 / 关键人姓名等命中给警告.

namespace 约定: 按部门分 (dept/finance / dept/sales / dept/it / dept/hr).
caller 用 catfish_today_summary 拿员工 department 字段拼.

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
SENSITIVE_TERMS_PATH = Path.home() / ".catfish" / "wiki" / "sensitive_terms.txt"


def _read_id_token() -> str | None:
    return skill_publish._read_id_token()


def _load_sensitive_terms() -> list[str]:
    """从 ~/.catfish/wiki/sensitive_terms.txt 读员工自配敏感词. 不存在返空 list.

    文件格式: 一行一个词, # 开头注释忽略, 空行跳过.
    """
    if not SENSITIVE_TERMS_PATH.exists():
        return []
    terms: list[str] = []
    try:
        for line in SENSITIVE_TERMS_PATH.read_text(encoding="utf-8").splitlines():
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


def wiki_publish(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 读 + 扫描 + POST JSON 到 gateway /v1/wiki/documents/{ns}.

    参数:
      wiki_rel_path (必): 员工本机 wiki rel_path, 必须以 'wiki/' 开头
        (e.g. 'wiki/entities/老李.md', 'wiki/concepts/CSMM-4.md')
      namespace (必): 部门 namespace ('dept/finance', 'dept/sales', ...)
      acknowledge_warnings (可选, 默认 false): true 时跳 PII / 内网 / 敏感词警告
        强 publish. 第一次调若拿到 warnings, LLM 必须**转告员工**, 员工
        confirm 后才能加这个参数 retry.
      file_id (可选): 不传则服务器分配 UUID. 重发同一 wiki 时传上次拿到的 file_id
        让中央 update 同 row.

    成功返:
      {ok:true, file_id, namespace, hub_url, published_at, warnings?}
    警告 (默认拒):
      {ok:false, scan_phase, warnings:[...], acknowledge_warnings_available:true}
    凭据撞 (永拒):
      {ok:false, scan_phase:"credentials", leaks:[...]}
    """
    wiki_rel_path = (args.get("wiki_rel_path") or "").strip()
    namespace = (args.get("namespace") or "").strip()
    ack_warnings = bool(args.get("acknowledge_warnings", False))
    file_id = (args.get("file_id") or "").strip()

    # 校验
    if not wiki_rel_path:
        return {"ok": False, "error": "wiki_rel_path 必填 (例: wiki/entities/老李.md)"}
    if not namespace:
        return {"ok": False, "error": "namespace 必填 (例: dept/finance)"}
    if ".." in wiki_rel_path or wiki_rel_path.startswith("/"):
        return {"ok": False, "error": f"wiki_rel_path 不合法: {wiki_rel_path}"}
    if not wiki_rel_path.startswith("wiki/"):
        return {
            "ok": False,
            "error": f"wiki_rel_path 必须以 'wiki/' 开头 (拿到 {wiki_rel_path})",
        }
    # namespace 必须 dept/<part>, dept-only 不允许公司级广播
    if not re.fullmatch(r"dept/[a-z][a-z0-9_-]{0,40}", namespace):
        return {
            "ok": False,
            "error": (
                f"namespace 只允许 'dept/<部门>' 格式, 小写字母数字/-/_  "
                f"(拿到 {namespace!r}). 公司级广播未开放."
            ),
        }

    # 读 wiki 文件
    catfish_home = Path.home() / ".catfish"
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
    title = _extract_field(fm_yaml, "title")
    if not title:
        # title fallback: 用文件 stem
        title = abs_path.stem
    kind = _extract_field(fm_yaml, "type") or _infer_kind_from_path(wiki_rel_path)
    if kind not in ("entity", "concept", "query"):
        kind = "entity"

    # 用 file_map 接口跑共享扫描
    file_map = {abs_path.name: text.encode("utf-8")}

    # (a) 凭据扫 — 永拒
    leaks = skill_publish._scan_sensitive(file_map)
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

    # 收集 warnings — 非凭据的 3 类
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

    # (d) 敏感词扫 — 员工自配 list
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

    if warnings and not ack_warnings:
        return {
            "ok": False,
            "scan_phase": "warnings",
            "warnings": warnings,
            "acknowledge_warnings_available": True,
            "error": (
                f"wiki publish 前扫描有 {len(warnings)} 类警告. "
                "把 warnings 转告员工, 员工确认要继续后, LLM 加 "
                "acknowledge_warnings=true retry. (跟 skill auto_scrub 不同 — "
                "wiki 内容不能自动脱敏, 员工要自己拍.)"
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
    if file_id:
        payload["file_id"] = file_id

    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/wiki/documents/{namespace}"
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

    result = {
        "ok": True,
        "namespace": data.get("namespace", namespace),
        "file_id": data.get("file_id"),
        "published_at": data.get("published_at"),
        "size_bytes": data.get("size_bytes"),
        "hub_url": f"{GATEWAY_URL}/v1/wiki/documents/{namespace}/{data.get('file_id')}",
    }
    # 显式说明发出去的是什么 (帮 LLM 转告员工)
    result["summary"] = (
        f"已 publish wiki '{title}' 到 {namespace}. "
        f"已 pull 副本不受未来 unpublish 影响 (manifesto 公理 4)."
    )
    if warnings:
        result["acknowledged_warnings"] = [w["category"] for w in warnings]
    return result
