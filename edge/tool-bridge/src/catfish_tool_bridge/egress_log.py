"""数据外发记录 —— 在 hermes 出口 (和它拉起的 tool-bridge) 记下「带内容出本机」「发给第三方」的请求。

# 为什么 (10/24 鸿波)

Companion「数据外发记录」原来只记界面 fetchWithAuth 发的请求: 42 万条几乎全是界面每 3 秒问
本机 hermes 的轮询 (根本没出本机); 真正把对话、资料原文、截图带出去的 —— hermes 调模型、鲶鱼
插件蒸馏 / 核对、tool-bridge、网页搜索、微信 —— 一条都没记。

# 记什么 (鸿波定的标准: 看带没带内容出去、是不是给第三方, 不看走哪条路)

  · 发往本机 (localhost / 127.0.0.1, 中央服务的端口除外) —— 不记, 没出本机
  · 发往中央 (网关 / 身份 / 注册中心) —— 只记带内容的 (POST/PUT/PATCH 且有 body), 登录 / 续 token /
    健康检查不记; GET 拉目录、配额这类不带你的内容, 不记
  · 发往第三方 (网页搜索、微信 iLink、天气 …) —— 都记
  10/25 起**存请求内容** (鸿波: 不存内容, 有争议时说不清到底发了什么), 并且做成防篡改的:
    · 内容: 去掉密钥类字段、遮住用户标识后 zlib 压缩存 outbound_payload (规则见 _redact); 对话里超过 1KB 的单条消息 / 工具定义
      按内容哈希存 outbound_blob 去重 (模型调用每次都带整段历史, 不去重一天就几百 MB)
    · 指纹链: 每行 row_hash = sha256(上一行 row_hash + 本行字段 + 内容哈希), 改 / 删中间任何一行,
      后面全对不上; Companion 每天把链头指纹报给中央 (只报指纹不报内容), 截掉末尾也查得出
    · 保留期在 Companion 本机设置 (outbound_meta.retention_days), 到期由 Companion 清
  只存请求, 不存回复。

# 怎么记

hermes 进程里挂钩 httpx / requests / aiohttp 的发送 (模型 SDK、插件、网页搜索、微信都走这三个),
写 ~/.catfish/outbound_log.db (Companion transparent_log.rs 同一张表, 多了 origin / dest_kind /
dest_host / purpose 四列)。后台线程写, 不拖慢请求; 写失败只丢这一条记录, 不影响请求本身。

本文件两份: hermes 插件 catfish-xcatfish-user/egress_log.py (正本) 和 tool-bridge
catfish_tool_bridge/egress_log.py, tool-bridge 的测试钉住两份一字不差。
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import zlib

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_CONTENT_METHODS = {"POST", "PUT", "PATCH"}
#: 中央端带内容但不算「你的内容」的: 登录、续 token、健康检查
_SKIP_PATHS = ("/token", "/oauth", "/authorize", "/userinfo", "/healthz", "/health", "/.well-known/")
# 第三方的纯拉取轮询 (不带员工内容, 微信长轮询每 ~18 秒一次, 一天近 5000 条) —— 跟「拉取/心跳不记」同一条
_THIRD_PARTY_POLL_PATHS = ("/getupdates", "/sendtyping")
#: catfish_source 标签 → 页面上看到的用途 (前缀匹配, 长的优先)
_SOURCE_PURPOSE = {
    "plugin:memory-wiki-ground": "知识卡核对",
    "plugin:memory-wiki-analysis": "资料蒸馏",
    "plugin:memory-wiki-generation": "资料蒸馏",
    "plugin:memory-wiki-merge": "资料蒸馏",
    "plugin:memory-wiki": "知识卡整理",
    "plugin:memory-distill": "长期记忆整理",
    "plugin:memory-summarize": "长期记忆整理",
    "plugin:memory-reconcile": "长期记忆整理",
    "plugin:memory": "长期记忆",
    "companion-chat": "对话",
    # 10/25: hermes 自己的辅助调用 (请求头 X-Catfish-Source, 鲶鱼插件 P3 打的)
    "hermes:aux:compression": "对话 · 上下文压缩",
    "hermes:aux:vision": "对话 · 看图",
    "hermes:aux:title_generation": "对话 · 起标题",
    "hermes:aux:approval": "对话 · 审批判断",
    "hermes:aux:background_review": "后台 · 技能 / 记忆复盘",
    "hermes:aux:mcp": "MCP 工具",
    "hermes:aux": "hermes 辅助",
    "hermes:api_server": "对话",
    "hermes:webui": "对话",
    "hermes:weixin": "微信对话",
    "hermes:cron": "定时任务",
    "hermes:curator": "后台 · 技能整理",
    "hermes:subagent": "子任务",
    "companion": "Companion",
}
_PATH_PURPOSE = (
    ("/v1/chat/completions", "模型调用"),
    ("/v1/embeddings", "向量计算"),
    ("/a2a/", "专家协作"),
    ("/v1/hub/skills", "发布技能"),
    ("/v1/wiki/", "部门知识"),
    ("/api/proactive/", "主动提醒"),
)
_HOST_PURPOSE = (
    ("parallel.ai", "网页搜索"),
    ("tavily.com", "网页搜索"),
    ("firecrawl.dev", "网页抓取"),
    ("exa.ai", "网页搜索"),
    ("weixin.qq.com", "微信"),
    ("wttr.in", "天气"),
    ("pypi.org", "安装依赖"),
    ("pythonhosted.org", "安装依赖"),
    ("npmjs.org", "安装依赖"),
    ("github.com", "下载"),
)
_SECRET_KEY_RE = re.compile(r"(key|token|ticket|session|secret|password|auth|sig)", re.IGNORECASE)


def _home() -> Path:
    env = os.environ.get("CATFISH_HOME")
    return Path(env).expanduser() if env else Path.home() / ".catfish"


# ---------------------------------------------------------------- 中央服务在哪

def _host_port(url: str) -> Optional[Tuple[str, int]]:
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    if not p.hostname:
        return None
    return p.hostname.lower(), p.port or (443 if p.scheme == "https" else 80)


#: 配置里哪些键是中央服务地址 (别的 URL —— 其它模型商、MCP 地址 —— 不算中央)
_CENTRAL_KEYS = {"gateway_url", "web_url", "identity_url", "registry_url", "issuer", "base_url", "url"}


def _yaml_urls(path: Path, keys: set) -> list:
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore")) or {}
    except Exception:  # noqa: BLE001
        return []
    out: list = []

    def walk(node, parent: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, str) and parent in keys and node.startswith(("http://", "https://")):
            out.append(node)

    walk(data)
    return out


def _hermes_home() -> Path:
    """hermes 的数据目录: HERMES_HOME > Windows 的 %LOCALAPPDATA%\\hermes > ~/.hermes (同 hermes_token_renewal)。"""
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "hermes"
    return Path.home().joinpath(".hermes")


def _config_urls() -> list:
    urls = [os.environ.get(k, "") for k in (
        "CATFISH_GATEWAY_URL", "CATFISH_GATEWAY_INTERNAL_URL", "CATFISH_IDENTITY_URL",
        "CATFISH_REGISTRY_URL", "OPENAI_BASE_URL",
    )]
    hermes_home = _hermes_home()
    urls += _yaml_urls(_home() / "companion.yaml", _CENTRAL_KEYS)        # oidc.issuer / endpoints.*
    urls += _yaml_urls(_home() / "memory_plugin.yaml", {"url"})          # gateway.url
    try:  # hermes 只认 model.base_url —— 别的 base_url 可能是第三方模型商, 不算中央
        import yaml  # noqa: PLC0415
        model = (yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8")) or {}).get("model") or {}
        urls.append(str(model.get("base_url") or "") if isinstance(model, dict) else "")
    except Exception:  # noqa: BLE001
        pass
    return urls


_CENTRAL: Optional[set] = None
_CENTRAL_AT = 0.0


def _central() -> set:
    """中央服务的 (host, port): 网关 / 身份 / 注册中心 + 它们的默认端口。5 分钟重读一次配置。"""
    global _CENTRAL, _CENTRAL_AT
    if _CENTRAL is None or time.time() - _CENTRAL_AT > 300:
        found = {hp for hp in (_host_port(u) for u in _config_urls() if u) if hp}
        hosts = {h for h, _ in found} or {"127.0.0.1"}
        # 默认端口: 网关 8999 / 身份 8998 / 技能 8997 / 部门知识 8994 (中央同一台机器)
        found |= {(h, p) for h in hosts for p in (8999, 8998, 8997, 8994)}
        found -= {(h, p) for h, p in found if h in _LOOPBACK and p == 8642}  # 本机 hermes 不算中央
        _CENTRAL, _CENTRAL_AT = found, time.time()
    return _CENTRAL


def classify(method: str, url: str, body_len: int) -> Optional[str]:
    """该不该记: 'content' (带内容去中央) / 'third_party' / None (不记)。"""
    hp = _host_port(url)
    if hp is None:
        return None
    host, _ = hp
    if hp in _central():
        path = urlsplit(url).path or "/"
        if method.upper() in _CONTENT_METHODS and body_len > 0 and not any(s in path for s in _SKIP_PATHS):
            return "content"
        return None
    if host in _LOOPBACK or host.endswith(".localhost") or host.endswith(".local"):
        return None
    if any((urlsplit(url).path or "").lower().endswith(s) for s in _THIRD_PARTY_POLL_PATHS):
        return None
    return "third_party"


def purpose(url: str, source: Optional[str] = None) -> str:
    """「做什么」: 来源标签 (URL 的 catfish_source, 或请求头 X-Catfish-Source) 优先, 再按主机 / 路径。"""
    try:
        p = urlsplit(url)
    except ValueError:
        return url[:60]
    src = dict(parse_qsl(p.query)).get("catfish_source", "") or (source or "").strip()
    if src:
        for key in sorted(_SOURCE_PURPOSE, key=len, reverse=True):
            if src.startswith(key):
                return _SOURCE_PURPOSE[key]
        return src
    host = (p.hostname or "").lower()
    for suffix, label in _HOST_PURPOSE:
        if host == suffix or host.endswith("." + suffix):
            return label
    for prefix, label in _PATH_PURPOSE:
        if p.path.startswith(prefix) or prefix in p.path:
            return label
    return p.path or host


def clean_url(url: str) -> str:
    """去掉 query 里像密钥的参数值 (key= / token= …), 别把凭据记进日志。"""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    q = [(k, "***" if _SECRET_KEY_RE.search(k) else v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
    return urlunsplit((p.scheme, p.netloc.split("@")[-1], p.path, urlencode(q, safe=":/*"), ""))


# ---------------------------------------------------------------- 写库

_DDL = """
CREATE TABLE IF NOT EXISTS outbound_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_request TEXT NOT NULL,
    ts_response TEXT,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    request_bytes INTEGER NOT NULL DEFAULT 0,
    response_bytes INTEGER NOT NULL DEFAULT 0,
    status INTEGER,
    request_payload_preview TEXT,
    request_payload_path TEXT,
    response_summary TEXT,
    error TEXT,
    category TEXT
);
"""
_NEW_COLUMNS = ("origin", "dest_kind", "dest_host", "purpose", "body_sha256", "prev_hash", "row_hash")
_DDL_STORE = """
CREATE TABLE IF NOT EXISTS outbound_payload (log_id INTEGER PRIMARY KEY, body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS outbound_blob (hash TEXT PRIMARY KEY, data BLOB NOT NULL, last_ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbound_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
GENESIS = "GENESIS"
#: JSON 里这些键的字符串值存之前换成 *** (跟 Companion transparent_log_store.rs 同一份)
# 字段名里带这些就整个值打码 (10/25 第二次: 原来只认 15 个固定字段名, 微信 iLink 的 typing_ticket /
# context_token 这类会话票据原样存了下来)。原来那 15 个都含在里面。
_SECRET_KEY_PARTS = ("token", "ticket", "session", "secret", "password", "passwd", "api_key", "apikey",
                     "api-key", "access_key", "private_key", "authorization")
# 字段名以 user_id 结尾 (ilink_user_id / from_user_id / to_user_id …) 是能对应到具体某个人的标识:
# 留前后各 4 位 (审计时分得清发给了哪个联系人, 看不到完整标识), @ 后面的域名留着。
_ID_KEEP = 4
_BLOB_MIN = 1024              # 对话里超过这么大的单条消息 / 工具定义单独存、去重
_MAX_TEXT = 5 * 1024 * 1024   # 非 JSON 的文本请求体最多存这么多
_MAX_QUEUED_BODY = 64 * 1024 * 1024  # 比这还大的请求体不进队列, 只记大小和哈希


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def mask_id(v: str) -> str:
    """o9cq807yh8QQuk8jp4IEWb_900RE@im.wechat → o9cq…00RE@im.wechat。再遮一次结果不变。"""
    local, at, domain = v.partition("@")
    if "…" in local or local == "***":
        return v  # 已经遮过
    masked = f"{local[:_ID_KEEP]}…{local[-_ID_KEEP:]}" if len(local) > 3 * _ID_KEEP else "***"
    return masked + at + domain


def _redact(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = str(k).lower()
            if isinstance(v, str) and any(p in key for p in _SECRET_KEY_PARTS):
                out[k] = "***"
            elif isinstance(v, str) and key.endswith("user_id"):
                out[k] = mask_id(v)
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def stored_body(raw: Optional[bytes]) -> Tuple[str, Dict[str, str]]:
    """原始请求体 → (要存的文本, {blob 哈希: blob 文本})。没有 body 返 ("", {})。

    JSON: 去密钥 → messages 里的大消息、tools 换成 {"$blob": 哈希} → 排序键紧凑序列化;
    文本: 原样 (超 5MB 截断并注明全文哈希); 二进制 (上传的文件): 只记大小和 sha256, 拿着原件能对上。
    """
    if not raw:
        return "", {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"[二进制内容 {len(raw)} 字节, sha256={_sha(raw)}; 未保存原件]", {}
    try:
        obj = json.loads(text)
    except ValueError:
        if len(raw) > _MAX_TEXT:
            head = raw[:_MAX_TEXT].decode("utf-8", "ignore")
            return f"{head}\n[后面截掉; 全文 {len(raw)} 字节, sha256={_sha(raw)}]", {}
        return text, {}
    obj = _redact(obj)
    blobs: Dict[str, str] = {}

    def ref(v):
        s = _canon(v)
        if len(s.encode("utf-8")) <= _BLOB_MIN:
            return v
        h = _sha(s.encode("utf-8"))
        blobs[h] = s
        return {"$blob": h}

    if isinstance(obj, dict):
        if isinstance(obj.get("messages"), list):
            obj["messages"] = [ref(m) for m in obj["messages"]]
        if "tools" in obj:
            obj["tools"] = ref(obj["tools"])
    return _canon(obj), blobs


_CHAIN_FIELDS = ("ts_request", "ts_response", "method", "url", "request_bytes", "response_bytes", "status",
                 "error", "origin", "dest_kind", "dest_host", "purpose", "body_sha256")


def row_hash(prev: str, row: dict) -> str:
    """指纹链: sha256(上一行指纹 + "\\n" + 各字段用 \\x1f 连起来)。None 记成空串。Rust 那边一字不差。"""
    parts = ["" if row.get(k) is None else str(row.get(k)) for k in _CHAIN_FIELDS]
    return _sha((prev + "\n" + "\x1f".join(parts)).encode("utf-8"))


def chain_head(conn: sqlite3.Connection) -> str:
    r = conn.execute("SELECT row_hash FROM outbound_log WHERE row_hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if r:
        return r[0]
    r = conn.execute("SELECT value FROM outbound_meta WHERE key = 'gc_last_hash'").fetchone()
    return r[0] if r else GENESIS

_ORIGINALS: Dict[str, tuple] = {}
_queue: "queue.Queue[dict]" = queue.Queue(maxsize=500)
_worker: Optional[threading.Thread] = None
_ORIGIN = "hermes"


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_DDL)
    conn.executescript(_DDL_STORE)
    have = {r[1] for r in conn.execute("PRAGMA table_info(outbound_log)")}
    for col in _NEW_COLUMNS:
        if col not in have:
            try:
                conn.execute(f"ALTER TABLE outbound_log ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # 另一个进程刚加上


def _write(rows: list) -> None:
    db = _home() / "outbound_log.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    prepared = []
    for r in rows:  # 压缩 / 拆块放在锁外
        stored, blobs = stored_body(r.pop("_body", None))
        r["body_sha256"] = _sha(stored.encode("utf-8")) if stored else ""
        prepared.append((r, stored, blobs))
    conn = sqlite3.connect(str(db), timeout=15, isolation_level=None)
    try:
        _ensure(conn)
        conn.execute("BEGIN IMMEDIATE")  # 跟 Companion / 别的进程串行接链
        try:
            prev = chain_head(conn)
            for r, stored, blobs in prepared:
                for h, text in blobs.items():
                    conn.execute("INSERT OR IGNORE INTO outbound_blob (hash, data, last_ts) VALUES (?, ?, ?)",
                                 (h, zlib.compress(text.encode("utf-8")), r["ts_response"]))
                    conn.execute("UPDATE outbound_blob SET last_ts = ? WHERE hash = ? AND last_ts < ?",
                                 (r["ts_response"], h, r["ts_response"]))
                r["prev_hash"] = prev
                r["row_hash"] = prev = row_hash(prev, r)
                cur = conn.execute(
                    "INSERT INTO outbound_log (ts_request, ts_response, method, url, request_bytes, response_bytes, "
                    "status, response_summary, error, category, origin, dest_kind, dest_host, purpose, "
                    "body_sha256, prev_hash, row_hash) "
                    "VALUES (:ts_request, :ts_response, :method, :url, :request_bytes, :response_bytes, :status, "
                    ":response_summary, :error, :category, :origin, :dest_kind, :dest_host, :purpose, "
                    ":body_sha256, :prev_hash, :row_hash)",
                    r,
                )
                if stored:
                    conn.execute("INSERT INTO outbound_payload (log_id, body) VALUES (?, ?)",
                                 (cur.lastrowid, zlib.compress(stored.encode("utf-8"))))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _run() -> None:
    while True:
        rows = [_queue.get()]
        try:
            while len(rows) < 100:
                rows.append(_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            _write(rows)
        except Exception:  # noqa: BLE001 —— 记录是辅助, 写不进就丢这一批
            pass
        finally:
            for _ in rows:
                _queue.task_done()


def record(method: str, url: str, body_len: int, status: Optional[int] = None,
           response_bytes: int = 0, error: Optional[str] = None, started: Optional[float] = None,
           body: Optional[bytes] = None, source: Optional[str] = None) -> bool:
    """按规则判断, 该记就排进写队列。返回是否记了 (测试用)。"""
    kind = classify(method, url, body_len)
    if kind is None:
        return False
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_run, name="catfish-egress-log", daemon=True)
        _worker.start()
    now = datetime.now(timezone.utc).isoformat()
    t0 = datetime.fromtimestamp(started, timezone.utc).isoformat() if started else now
    hp = _host_port(url)
    try:
        _queue.put_nowait({
            "ts_request": t0, "ts_response": now, "method": method.upper(), "url": clean_url(url),
            "request_bytes": int(body_len or 0), "response_bytes": int(response_bytes or 0),
            "status": status, "response_summary": None, "error": (error or None) and str(error)[:200],
            "category": kind, "origin": _ORIGIN, "dest_kind": "central" if kind == "content" else "third_party",
            "dest_host": hp[0] if hp else "", "purpose": purpose(url, source),
            "_body": body if body is None or len(body) <= _MAX_QUEUED_BODY
            else f"[请求体 {len(body)} 字节, sha256={_sha(body)}; 太大未保存]".encode("utf-8"),
        })
    except queue.Full:
        return False
    return True


def flush(timeout: float = 5.0) -> None:
    """等排进队列的都写进库 (测试 / 退出前用)。看 unfinished_tasks 而不是队列空不空 ——
    后台线程取走一批后队列就空了, 但那一批可能还没写完。"""
    end = time.time() + timeout
    while _queue.unfinished_tasks and time.time() < end:
        time.sleep(0.02)


# ---------------------------------------------------------------- 挂钩

def _bytes_of(body) -> Optional[bytes]:
    """请求体 → bytes (存内容用); 读不出来的 (流式、表单对象) 返说明文字。"""
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (dict, list)):
        try:
            return json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError):
            pass
    return f"[{type(body).__name__} 请求体, 没法在出口读出内容]".encode("utf-8")


def _len_of(body) -> int:
    if body is None:
        return 0
    if isinstance(body, (bytes, bytearray)):
        return len(body)
    if isinstance(body, str):
        return len(body.encode("utf-8"))
    try:
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _patch_httpx() -> None:
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return
    if getattr(httpx.Client.send, "_catfish_egress", False):
        return

    def body_len(request) -> int:
        try:
            return len(request.content)
        except Exception:  # noqa: BLE001 —— 流式 body
            return int(request.headers.get("content-length") or 0)

    def body_of(request) -> Optional[bytes]:
        try:
            return bytes(request.content)
        except Exception:  # noqa: BLE001 —— 流式 body
            return "[流式请求体, 出口读不到内容]".encode("utf-8") if body_len(request) else None

    orig_sync, orig_async = httpx.Client.send, httpx.AsyncClient.send
    _ORIGINALS["httpx"] = (httpx, orig_sync, orig_async)

    # 10/28: 回复读完 (或流关掉) 才记, 下行按实际收到的字节数。
    # 原来 send() 一返回就记: 流式回复 (hermes 调模型全是 SSE) 那时正文还没开始传, 也没有 content-length,
    # 下行永远是 0、ts_response 是收到响应头的时刻 —— 跟中央配额日志对不上。
    class _CountSync(httpx.SyncByteStream):
        def __init__(self, inner, done):
            self._inner, self._done, self.n = inner, done, 0

        def __iter__(self):
            for chunk in self._inner:
                self.n += len(chunk)
                yield chunk

        def close(self):
            try:
                self._inner.close()
            finally:
                done, self._done = self._done, None
                if done:
                    done(self.n)

    class _CountAsync(httpx.AsyncByteStream):
        def __init__(self, inner, done):
            self._inner, self._done, self.n = inner, done, 0

        async def __aiter__(self):
            async for chunk in self._inner:
                self.n += len(chunk)
                yield chunk

        async def aclose(self):
            try:
                await self._inner.aclose()
            finally:
                done, self._done = self._done, None
                if done:
                    done(self.n)

    def finish(request, resp, t0, wrap) -> None:
        def done(n: int) -> None:
            record(request.method, str(request.url), body_len(request), resp.status_code, n, started=t0,
                   body=body_of(request), source=request.headers.get("x-catfish-source"))
        try:
            n = len(resp.content)  # 非流式: send() 里已经读完
        except httpx.ResponseNotRead:
            resp.stream = wrap(resp.stream, done)  # 流式: 等调用方读完 / 关掉
            return
        done(n)

    def send(self, request, *a, **kw):
        t0 = time.time()
        try:
            resp = orig_sync(self, request, *a, **kw)
        except Exception as e:
            record(request.method, str(request.url), body_len(request), error=repr(e), started=t0,
                   body=body_of(request), source=request.headers.get("x-catfish-source"))
            raise
        finish(request, resp, t0, _CountSync)
        return resp

    async def asend(self, request, *a, **kw):
        t0 = time.time()
        try:
            resp = await orig_async(self, request, *a, **kw)
        except Exception as e:
            record(request.method, str(request.url), body_len(request), error=repr(e), started=t0,
                   body=body_of(request), source=request.headers.get("x-catfish-source"))
            raise
        finish(request, resp, t0, _CountAsync)
        return resp

    send._catfish_egress = asend._catfish_egress = True  # type: ignore[attr-defined]
    httpx.Client.send, httpx.AsyncClient.send = send, asend


def _patch_requests() -> None:
    try:
        import requests  # noqa: PLC0415
    except ImportError:
        return
    if getattr(requests.Session.send, "_catfish_egress", False):
        return
    orig = requests.Session.send
    _ORIGINALS["requests"] = (requests, orig)

    def send(self, request, **kw):
        t0 = time.time()
        try:
            resp = orig(self, request, **kw)
        except Exception as e:
            record(request.method or "GET", request.url or "", _len_of(request.body), error=repr(e), started=t0,
                   body=_bytes_of(request.body),
                   source=(request.headers or {}).get("X-Catfish-Source"))
            raise
        n = int(resp.headers.get("content-length") or 0)
        if not n and not kw.get("stream"):  # chunked 回复没有 content-length; 非流式时正文已经读进来了
            n = len(resp.content or b"")
        record(request.method or "GET", request.url or "", _len_of(request.body), resp.status_code,
               n, started=t0, body=_bytes_of(request.body),
                   source=(request.headers or {}).get("X-Catfish-Source"))
        return resp

    send._catfish_egress = True  # type: ignore[attr-defined]
    requests.Session.send = send


def _patch_aiohttp() -> None:
    try:
        import aiohttp  # noqa: PLC0415
    except ImportError:
        return
    if getattr(aiohttp.ClientSession._request, "_catfish_egress", False):
        return
    orig = aiohttp.ClientSession._request
    _ORIGINALS["aiohttp"] = (aiohttp, orig)

    async def _request(self, method, str_or_url, **kw):
        t0 = time.time()
        url = str(str_or_url)
        if self._base_url is not None and not url.startswith(("http://", "https://")):
            url = str(self._base_url.join(aiohttp.client.URL(url)))
        n = _len_of(kw.get("data")) or _len_of(kw.get("json"))
        raw = _bytes_of(kw.get("data")) if kw.get("data") is not None else _bytes_of(kw.get("json"))
        src = next((str(v) for k, v in dict(kw.get("headers") or {}).items() if str(k).lower() == "x-catfish-source"), None)
        try:
            resp = await orig(self, method, str_or_url, **kw)
        except Exception as e:
            record(method, url, n, error=repr(e), started=t0, body=raw, source=src)
            raise
        if resp.content_length is not None:  # 声明了长度: 就按它记 (正文是空的也是 0, 不用等)
            record(method, url, n, resp.status, resp.content_length, started=t0, body=raw, source=src)
            return resp
        # 10/28: 没声明长度 (chunked / 流式) —— 等正文收完 (或连接被放掉) 再按实际收到的字节记, 只记一次。
        # 原来这里直接记 content_length or 0, 这类回复下行永远是 0。
        state = {"done": False}

        def done() -> None:
            if not state["done"]:
                state["done"] = True
                record(method, url, n, resp.status, resp.content.total_bytes, started=t0, body=raw, source=src)

        def hooked(fn):
            def wrapper(*a, **k):
                done()
                return fn(*a, **k)
            return wrapper

        resp.content.on_eof(done)
        resp.release, resp.close = hooked(resp.release), hooked(resp.close)
        return resp

    _request._catfish_egress = True  # type: ignore[attr-defined]
    aiohttp.ClientSession._request = _request


def install(origin: str = "hermes") -> None:
    """挂钩本进程的 httpx / requests / aiohttp。重复调用无副作用。CATFISH_EGRESS_LOG=0 关掉。"""
    global _ORIGIN
    if os.environ.get("CATFISH_EGRESS_LOG", "1") == "0":
        return
    _ORIGIN = origin
    for patch in (_patch_httpx, _patch_requests, _patch_aiohttp):
        try:
            patch()
        except Exception:  # noqa: BLE001 —— 挂不上就不记, 不能让 hermes 起不来
            pass


def uninstall() -> None:
    """撤掉挂钩 (测试用)。"""
    if "httpx" in _ORIGINALS:
        mod, a, b = _ORIGINALS.pop("httpx")
        mod.Client.send, mod.AsyncClient.send = a, b
    if "requests" in _ORIGINALS:
        mod, a = _ORIGINALS.pop("requests")
        mod.Session.send = a
    if "aiohttp" in _ORIGINALS:
        mod, a = _ORIGINALS.pop("aiohttp")
        mod.ClientSession._request = a
