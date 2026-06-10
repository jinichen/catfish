"""catfish_skill_publish 工具实现 (BL-D2 5/10).

把员工本机一个 skill 目录打包 multipart 发到 gateway /v1/hub/skills/{ns}.

走 gateway (8999) 而不是直连 hub (8997), 因为:
  - 单 origin 简化 token 路径 (跟 chat / mcp-registry 同条 OAuth id_token)
  - gateway OIDC 验签后注入 X-Catfish-User-Sub, hub 端用 sub 当 published_by
  - 不暴露 hub 端口给员工本机进程, 防绕权限

# Token 来源

通过 secret_broker 拿当前员工的 OAuth id_token? 不 — tool-bridge 没接 OAuth.
通过 gateway dev_token? BL-FIX29 关掉了.

实际方案: tool-bridge 通过 gateway loopback 用 internal token (BL-FIX37 同款).
但 internal token 解码身份是 'internal:gateway-loopback', published_by 不准.

更好: 从 ~/.catfish/oauth/id_token 文件读 (Companion 写的, BL-FIX32). tool-bridge
跟 Companion 同机, 同 ~/.catfish 目录, 直接读. published_by 就是真员工 sub.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("catfish.tool_bridge.skill_publish")

#: Companion 写 OAuth token 的位置 (BL-FIX32)
OAUTH_ID_TOKEN_PATH = Path.home() / ".catfish" / "oauth" / "id_token"

#: gateway URL — 跟 chat / mcp-registry 走同条
import os as _os
_GATEWAY_PORT = _os.environ.get("CATFISH_GATEWAY_PORT", "8999")
_GATEWAY_HOST = _os.environ.get("CATFISH_GATEWAY_HOST", "127.0.0.1")
GATEWAY_URL = _os.environ.get(
    "CATFISH_GATEWAY_URL",
    f"http://{_GATEWAY_HOST}:{_GATEWAY_PORT}",
)

#: 凭据 / 敏感字段正则 — publish 前 grep, 撞到拒绝 (BL-D2, 5/10 ship)
_SENSITIVE_PATTERNS = [
    re.compile(r"\bpassword\s*[:=]\s*['\"]?[A-Za-z0-9_\-!@#$%^&*]{4,}", re.IGNORECASE),
    re.compile(r"\bapi_?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"\bsecret_?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),  # OpenAI / DeepSeek key
    re.compile(r"\bAIza[A-Za-z0-9_\-]{35}"),  # Gemini key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # SSH / RSA key
]

#: PII 扫描 (5/21 方案 1): 教学产物 publish 前防身份证号 / 姓名 / 工号上传
#:
#: 命中位置: SKILL.md / script.py 里 hardcode 的数据 (录屏 OCR 误写 / 流程含真员工
#: 姓名/工号). 命中 → 拒上传 + 提示员工脱敏.
_PII_PATTERNS = [
    # 中国身份证号 (18 位, 含末尾 X)
    re.compile(r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b"),
    # 中国手机号 (11 位, 1 开头)
    re.compile(r"\b1[3-9]\d{9}\b"),
    # 工号 (8-12 位数字, 前后是空格 / : / = / 'employee_id' 等上下文词)
    re.compile(r"(?i)\b(employee_?id|work_?no|工号|员工号)\s*[:=]\s*['\"]?\d{6,12}\b"),
    # 银行卡号 (16-19 位)
    re.compile(r"\b\d{16,19}\b"),
]

#: 内网 URL / hostname 扫描 (5/21 方案 1): 防教学录屏把内网拓扑信息打包上 Hub.
#:
#: 客户企业内网常用网段 / 域名后缀. 撞到 → 拒上传 + 提示员工脱敏成 example.com /
#: 占位符 / 环境变量.
_INTRANET_PATTERNS = [
    # 内网 IP 段 (RFC 1918)
    re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),
    # 常见企业域名后缀
    re.compile(r"https?://[\w\-]+\.(corp|internal|intra|local)\b"),
    # FFCS 默认拿到的内网 hostname pattern (鸿波公司, 灵敏)
    re.compile(r"\beis[\.\-][\w\-]+\b"),
    re.compile(r"\boa[\.\-][\w\-]+\b"),
]

#: skill 目录里要打包的文件后缀
_SKILL_FILE_EXTS = {".md", ".py", ".json", ".yaml", ".yml", ".txt", ".sh"}


def _read_id_token() -> str | None:
    """读 OAuth id_token, 没就返 None."""
    if not OAUTH_ID_TOKEN_PATH.exists():
        return None
    try:
        return OAUTH_ID_TOKEN_PATH.read_text(encoding="utf-8").strip() or None
    except Exception as e:
        logger.warning("读 id_token 失败: %s", e)
        return None


def _scan_sensitive(file_map: dict[str, bytes]) -> list[str]:
    """扫所有文件内容找凭据 (BL-D2). 返命中文件名列表."""
    hits = []
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pat in _SENSITIVE_PATTERNS:
            if pat.search(text):
                hits.append(f"{rel} (撞 {pat.pattern[:40]})")
                break
    return hits


def _scan_pii(file_map: dict[str, bytes]) -> list[str]:
    """扫 PII (5/21 方案 1). 返命中文件名 + 撞到的字段类型."""
    hits = []
    pii_names = ["身份证号", "手机号", "工号", "银行卡号"]
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for idx, pat in enumerate(_PII_PATTERNS):
            m = pat.search(text)
            if m:
                hits.append(f"{rel} (含 {pii_names[idx]}: {m.group()[:6]}***)")
                break  # 一个文件命中一类就够, 不重复报
    return hits


def _scan_intranet(file_map: dict[str, bytes]) -> list[str]:
    """扫内网 URL/hostname (5/21 方案 1). 返命中文件名 + 撞到的 URL 片段."""
    hits = []
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for pat in _INTRANET_PATTERNS:
            m = pat.search(text)
            if m:
                hits.append(f"{rel} (含内网: {m.group()})")
                break
    return hits


# ── P3.3.17 (6/10): auto-scrub — 把命中的 PII / 内网 URL 换成占位 ──────────
#
# 凭据 (api_key / password / 私钥) 永不自动 scrub — 改了员工自己都不知道,
# 必须手动. PII 跟内网 URL 可 scrub: 替换成 {{placeholder_N}} + SKILL.md 注
# params: 区告诉装的员工/LLM "这是占位, 装上后填".
#
# 设计:
#   - 同值同占位 (dedup): 5 处 "13800138000" 全换成 {{phone_1}}, 不分 phone_1/2/3
#   - 不同值递增: 138... → phone_1, 139... → phone_2
#   - 整 match 替换 (不解 capture group). 工号 pattern 含 prefix "工号:", 整段
#     替换成 {{employee_id_1}}, 接受 prefix 被吃的副作用 (LLM 看占位仍懂语义)


_PII_LABELS = ["id_card", "phone", "employee_id", "bank_card"]
_PII_DESC = ["身份证号", "手机号", "工号 (含'工号:'前缀一起替换)", "银行卡号"]


def _scrub_pii(
    file_map: dict[str, bytes],
) -> tuple[dict[str, bytes], list[dict[str, str]], dict[str, str]]:
    """扫所有文件提取 unique PII, 替换成占位 (in-memory, 不改员工本机文件).

    返:
      new_file_map: 替换后副本
      records: [{file, original (脱敏显示), placeholder, type}] 给 LLM 显
      params: {placeholder_bare_name → 描述} 用来注入 SKILL.md frontmatter
    """
    # 1. 扫一遍 — 收集 unique 值 + 分配 placeholder
    unique_values: dict[str, str] = {}  # 原始 match → placeholder ('{{phone_1}}')
    counter = [0, 0, 0, 0]
    records: list[dict[str, str]] = []
    first_seen: dict[str, str] = {}  # 原始 match → 第一个看到它的文件

    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for idx, pat in enumerate(_PII_PATTERNS):
            for m in pat.finditer(text):
                val = m.group()
                if val in unique_values:
                    continue  # 同值 dedup, 已分配过 placeholder
                counter[idx] += 1
                placeholder = f"{{{{{_PII_LABELS[idx]}_{counter[idx]}}}}}"
                unique_values[val] = placeholder
                first_seen[val] = rel
                # 显示用脱敏 — 前 4 + *** + 后 2 (太短直接全 ***)
                masked = (val[:4] + "***" + val[-2:]) if len(val) >= 8 else "***"
                records.append({
                    "file": rel,
                    "original_masked": masked,
                    "placeholder": placeholder,
                    "type": _PII_DESC[idx],
                })

    if not unique_values:
        return dict(file_map), [], {}

    # 2. 替换 — 整 match 替换
    new_file_map: dict[str, bytes] = {}
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            new_file_map[rel] = content
            continue
        for val, placeholder in unique_values.items():
            text = text.replace(val, placeholder)
        new_file_map[rel] = text.encode("utf-8")

    # 3. 整 params
    params: dict[str, str] = {}
    for val, placeholder in unique_values.items():
        bare = placeholder.strip("{}").strip()  # 'phone_1'
        # 拆出 label 来查 desc (e.g. 'phone_1' → 'phone')
        label = bare.rsplit("_", 1)[0]
        try:
            desc = _PII_DESC[_PII_LABELS.index(label)]
        except ValueError:
            desc = "员工 PII (装上 skill 后填)"
        params[bare] = f"装上 skill 后必填 — {desc} (源文件: {first_seen[val]})"

    return new_file_map, records, params


# 内网 pattern idx → placeholder label (跟 _INTRANET_PATTERNS 顺序对齐)
_INTRANET_LABELS = [
    "INTRANET_IP",      # 10.x.x.x
    "INTRANET_IP",      # 192.168.x.x (同 IP 类, 共享 counter)
    "INTRANET_IP",      # 172.16-31.x.x
    "INTRANET_URL",     # https://*.corp / *.internal / *.intra / *.local
    "INTRANET_EIS",     # eis.*
    "INTRANET_OA",      # oa.*
]


def _scrub_intranet(
    file_map: dict[str, bytes],
) -> tuple[dict[str, bytes], list[dict[str, str]], dict[str, str]]:
    """扫所有文件提取 unique 内网 URL/hostname, 替换成占位."""
    unique_values: dict[str, str] = {}
    label_counter: dict[str, int] = {}
    records: list[dict[str, str]] = []
    first_seen: dict[str, str] = {}

    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            continue
        for idx, pat in enumerate(_INTRANET_PATTERNS):
            for m in pat.finditer(text):
                val = m.group()
                if val in unique_values:
                    continue
                label = _INTRANET_LABELS[idx]
                label_counter[label] = label_counter.get(label, 0) + 1
                placeholder = f"{{{{{label}_{label_counter[label]}}}}}"
                unique_values[val] = placeholder
                first_seen[val] = rel
                records.append({
                    "file": rel,
                    "original": val,
                    "placeholder": placeholder,
                    "type": "内网地址",
                })

    if not unique_values:
        return dict(file_map), [], {}

    new_file_map: dict[str, bytes] = {}
    for rel, content in file_map.items():
        try:
            text = content.decode("utf-8", errors="ignore")
        except Exception:
            new_file_map[rel] = content
            continue
        for val, placeholder in unique_values.items():
            text = text.replace(val, placeholder)
        new_file_map[rel] = text.encode("utf-8")

    params: dict[str, str] = {}
    for val, placeholder in unique_values.items():
        bare = placeholder.strip("{}").strip()
        params[bare] = f"装上 skill 后必填 — 你公司内网的对应地址 (源: {first_seen[val]})"

    return new_file_map, records, params


def _inject_params_to_skill_md(file_map: dict[str, bytes], new_params: dict[str, str]) -> dict[str, bytes]:
    """给 SKILL.md frontmatter 末尾加 params: 段. 已有 params: 跳过 (不动员工已写的).

    frontmatter parsing: 找 '---\\n' ... '\\n---\\n' 段, 在 closing '---' 前插入.
    不引 yaml 依赖, 用字符串拼接.
    """
    if not new_params:
        return file_map
    skill_md_bytes = file_map.get("SKILL.md")
    if not skill_md_bytes:
        return file_map  # 缺 SKILL.md 主流程已挡, 这里防御性

    text = skill_md_bytes.decode("utf-8", errors="ignore")
    if not text.startswith("---"):
        return file_map  # 没 frontmatter — 跳, 注入会破 SKILL.md 解析

    # 找 closing ---
    end_idx = text.find("\n---", 3)
    if end_idx < 0:
        return file_map

    # 看现有 frontmatter 里是不是已有 params: 一级 key
    frontmatter = text[:end_idx]
    has_params = False
    for line in frontmatter.split("\n"):
        if line.startswith("params:"):
            has_params = True
            break

    if has_params:
        # 不动员工已写的, P3.3.17 v1 简单实现. 后续可 merge
        return file_map

    # 在 closing --- 前插入 params section
    params_block = ["", "# P3.3.17 auto-scrub 注入 — 装上后必填占位, LLM 看到 {{xxx}} 应主动问员工要值", "params:"]
    for bare_name, desc in new_params.items():
        # 简单 yaml: bare_name: 'desc'
        desc_safe = desc.replace("'", "\\'")
        params_block.append(f"  {bare_name}: '{desc_safe}'")
    new_frontmatter = frontmatter + "\n" + "\n".join(params_block)
    new_text = new_frontmatter + text[end_idx:]

    new_file_map = dict(file_map)
    new_file_map["SKILL.md"] = new_text.encode("utf-8")
    return new_file_map


def _collect_files(skill_dir: Path) -> dict[str, bytes]:
    """递归扫 skill 目录, 只收 _SKILL_FILE_EXTS 后缀的小文件 (单文件 < 1MB)."""
    if not skill_dir.exists() or not skill_dir.is_dir():
        return {}
    file_map: dict[str, bytes] = {}
    for p in skill_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SKILL_FILE_EXTS:
            continue
        if p.stat().st_size > 1024 * 1024:  # 1MB 上限
            logger.warning("跳过大文件 %s (%d bytes)", p, p.stat().st_size)
            continue
        rel = str(p.relative_to(skill_dir))
        file_map[rel] = p.read_bytes()
    return file_map


def skill_publish(args: dict[str, Any]) -> dict[str, Any]:
    """tool entry — 检查 + 打包 + multipart upload 到 gateway.

    参数:
      skill_path (必): 本机 skill 目录 (含 SKILL.md)
      namespace (必): hub 上 namespace
      auto_scrub_pii (可选, 默认 false, P3.3.17): 命中 PII 时自动替换成
        {{phone_1}} / {{employee_id_1}} 等占位 (in-memory, 不改员工本机文件).
        SKILL.md frontmatter 加 params: 段告诉装的员工/LLM 哪些占位需要填.
      auto_scrub_intranet (可选, 默认 false, P3.3.17): 命中内网 URL 时同款.
      凭据 (api_key / password / 私钥): **永不**自动 scrub, 太敏感, 改了员工
        自己都不知道. 命中永远拒, 让员工本机手动改后重试.
    """
    skill_path = (args.get("skill_path") or "").strip()
    namespace = (args.get("namespace") or "").strip()
    auto_scrub_pii = bool(args.get("auto_scrub_pii", False))
    auto_scrub_intranet = bool(args.get("auto_scrub_intranet", False))

    if not skill_path:
        return {"ok": False, "error": "skill_path 必填"}
    if not namespace:
        return {"ok": False, "error": "namespace 必填"}
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", namespace):
        return {
            "ok": False,
            "error": f"namespace 只允许小写字母/数字/-/_ (拿到 {namespace!r})",
        }

    skill_dir = Path(skill_path).expanduser().resolve()
    if not skill_dir.exists():
        return {"ok": False, "error": f"skill 目录不存在: {skill_dir}"}
    if not skill_dir.is_dir():
        return {"ok": False, "error": f"不是目录: {skill_dir}"}

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {
            "ok": False,
            "error": f"缺 SKILL.md ({skill_md}), 不是合法 skill 目录",
        }

    # 扫文件
    file_map = _collect_files(skill_dir)
    if not file_map:
        return {"ok": False, "error": "skill 目录里没扫到任何可打包文件"}
    if "SKILL.md" not in file_map:
        return {"ok": False, "error": "SKILL.md 没收到 (可能是软链或权限问题)"}

    # 5/21 方案 1: 3 道扫描串联跑, 任一命中就拒
    # (a) 凭据 (BL-D2) — publish 不能上传含密码 / api key 的文件
    leaks = _scan_sensitive(file_map)
    if leaks:
        return {
            "ok": False,
            "error": (
                "publish 前安全扫描命中疑似凭据, 拒绝上传:\n  "
                + "\n  ".join(leaks)
                + "\n请把凭据改成 keychain:// ref 后重试."
            ),
            "scan_phase": "credentials",
        }

    # (b) PII (5/21 方案 1) — 防身份证号 / 手机号 / 工号 / 银行卡号上传
    # P3.3.17 (6/10): auto_scrub_pii=true 时自动替换占位 + 注入 frontmatter params, 不拒
    pii_hits = _scan_pii(file_map)
    pii_scrub_records: list[dict[str, str]] = []
    pii_params_added: dict[str, str] = {}
    if pii_hits:
        if auto_scrub_pii:
            file_map, pii_scrub_records, pii_params_added = _scrub_pii(file_map)
            # 注入 frontmatter
            file_map = _inject_params_to_skill_md(file_map, pii_params_added)
            # re-scan verify — 确认 scrub 没漏 (防 pattern bug)
            still_pii = _scan_pii(file_map)
            if still_pii:
                return {
                    "ok": False,
                    "error": (
                        "auto_scrub_pii 后再扫仍命中 PII (scrub helper bug?), 拒上传:\n  "
                        + "\n  ".join(still_pii)
                    ),
                    "scan_phase": "pii_postscrub",
                }
            logger.info(
                "P3.3.17 auto_scrub_pii: 替换 %d 个 PII 占位, 注入 frontmatter %d 个 param",
                len(pii_scrub_records), len(pii_params_added),
            )
        else:
            return {
                "ok": False,
                "error": (
                    "publish 前 PII 扫描命中, 拒绝上传:\n  "
                    + "\n  ".join(pii_hits)
                    + "\n两个选择:\n"
                    + "  1) 员工本机手动改成占位符 / 参数后 retry\n"
                    + "  2) 让 LLM 加 auto_scrub_pii=true 重调本工具, 自动替换\n"
                    + "     占位 (e.g. 13800138000 → {{phone_1}}) + SKILL.md\n"
                    + "     frontmatter 加 params: 段告诉装的员工填什么"
                ),
                "scan_phase": "pii",
                "auto_scrub_available": True,
            }

    # (c) 内网 URL (5/21 方案 1) — 防内网拓扑信息泄漏
    # P3.3.17: auto_scrub_intranet=true 同款 scrub
    intranet_hits = _scan_intranet(file_map)
    intranet_scrub_records: list[dict[str, str]] = []
    intranet_params_added: dict[str, str] = {}
    if intranet_hits:
        if auto_scrub_intranet:
            file_map, intranet_scrub_records, intranet_params_added = _scrub_intranet(file_map)
            file_map = _inject_params_to_skill_md(file_map, intranet_params_added)
            still_intranet = _scan_intranet(file_map)
            if still_intranet:
                return {
                    "ok": False,
                    "error": (
                        "auto_scrub_intranet 后再扫仍命中, 拒上传:\n  "
                        + "\n  ".join(still_intranet)
                    ),
                    "scan_phase": "intranet_postscrub",
                }
            logger.info(
                "P3.3.17 auto_scrub_intranet: 替换 %d 个内网占位, 注入 frontmatter %d param",
                len(intranet_scrub_records), len(intranet_params_added),
            )
        else:
            return {
                "ok": False,
                "error": (
                    "publish 前内网 URL 扫描命中, 拒绝上传:\n  "
                    + "\n  ".join(intranet_hits)
                    + "\n两个选择:\n"
                    + "  1) 员工本机手动改成 example.com / 占位符后 retry\n"
                    + "  2) 让 LLM 加 auto_scrub_intranet=true 重调, 自动替换\n"
                    + "     占位 (e.g. http://eis.corp.local → {{INTRANET_EIS_1}})"
                ),
                "scan_phase": "intranet",
                "auto_scrub_available": True,
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

    # POST multipart 到 gateway
    import httpx  # 懒 import

    url = f"{GATEWAY_URL}/v1/hub/skills/{namespace}"
    files_payload = [
        ("files", (rel, content, "application/octet-stream"))
        for rel, content in file_map.items()
    ]
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                files=files_payload,
            )
    except httpx.RequestError as e:
        return {"ok": False, "error": f"调 gateway 失败: {e!r}"}

    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text[:500]
        return {
            "ok": False,
            "error": f"gateway {resp.status_code}: {detail}",
        }

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": f"gateway 返非 json: {resp.text[:200]}"}

    result = {
        "ok": True,
        "namespace": data.get("namespace", namespace),
        "name": data.get("name"),
        "version": data.get("version"),
        "published_at": data.get("published_at"),
        "files_count": len(file_map),
        "hub_url": f"{GATEWAY_URL}/v1/hub/skills/{namespace}/{data.get('name')}",
    }
    # P3.3.17: scrub 跑过就告诉 LLM 替换了什么, LLM 应转告员工
    if pii_scrub_records or intranet_scrub_records:
        result["scrub_summary"] = {
            "pii": pii_scrub_records,
            "intranet": intranet_scrub_records,
            "frontmatter_params_added": list(pii_params_added.keys()) + list(intranet_params_added.keys()),
            "note": (
                "scrub 只改 hub 上传副本, 员工本机文件没动. "
                "装上 skill 的员工看 SKILL.md frontmatter params: 段了解要填什么."
            ),
        }
    return result
