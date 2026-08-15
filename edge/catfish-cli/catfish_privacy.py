"""员工隐私自查 (BL-EMPLOYEE-PRIVACY-VERIFICATION #76, 5/25)。

回答员工一个问题: **"这东西到底在我机器上存了啥, 又往中央传了我啥?"**

8/15 从 catfish.py 搬出来。

# 为什么这一组值得单独一个文件

它跟 CLI 的其他命令**方向相反**: 别的命令是"帮员工干活", 这个是"让员工查我们"。
它读的是本机 `.catfish/` 下的文件清单和本地 audit jsonl, 拉的是中央
`/api/audit/me` —— 也就是说, 它的正确性标准不是"跑通", 而是**报出来的东西
必须是全的**。少报一项, 员工就在一个他以为看全了的清单上做了判断。

所以扫描目标写成 `_PRIVACY_SCAN_TARGETS` 这张**显式表**, 而不是遍历目录:
加了新的落盘位置就得来这里加一行, 是个必须有人动手的动作。
schema 见 docs/EMPLOYEE-PRIVACY-VERIFICATION.md。

# ⚠ 中央那一半只能看"我自己"

`_fetch_audit_me` 打的是 `/api/audit/me`, 带员工自己的 token。中央端**严禁**
看到别的员工的数据, 这个端点也不该被改成能传 user 参数。

# 依赖方向

config / token 两层往下依赖, 不反向 import catfish.py。
`_do_refresh` 从 catfish_token 拿 —— 注意这意味着
`monkeypatch.setattr(catfish, "_do_refresh", ...)` 对本模块**无效**。
现在没有测试这么干 (cmd_privacy_audit 一条测试都没有, 见下), 但以后要加的话
得打 catfish_privacy。

# 现状: 这个模块没有测试

8/15 拆分时查过, test_catfish.py 57 条里没有一条碰 cmd_privacy_audit。
180 行、直接决定员工对"数据到底出没出端"的判断, 却零覆盖 —— 这件事本身
比拆分更值得排期。没在这次改, 只是记下来。
"""
from __future__ import annotations

import json
import time
import urllib.error   # ← 见 catfish_token.py 顶部关于这一行的说明
import urllib.parse
import urllib.request
from pathlib import Path

from catfish_config import _gateway_url, logger
from catfish_token import _do_refresh, load_token, save_token

#
# `catfish privacy-audit`: 员工自己跑一发, 输出 "我本机存了啥 + 中央存了我啥"
# 报告. 目的不是给运维诊断, 是给员工"我能验证, 不需要信任公司说辞".
#
# 报告分 3 段:
#   1. 本机数据 (员工电脑上的): catfish 目录 / hermes 目录 / token / 配置.
#      列文件路径 + 大小 + 权限 + 内容类别 (token / 对话 / 日志 / 缓存).
#      员工看完知道 "哦, 对话历史在我电脑这, 不在中央" / "中央拿不到我 prompt".
#   2. 中央存了我啥 (调 /api/audit/me): 全是 metadata (count / token / model / 时间戳).
#      schema_note 让员工知道"中央只看 metadata, 不看 prompt/response 文本".
#   3. 上传记录 (本机 audit jsonl): 我电脑往中央发了啥 (按模型分布 / 今日量).
#
# 防误判设计:
#   - 调中央失败不 fail-hard (本机段照样输出, 中央段标"未连通", 让离线员工也能审)
#   - --json 给机器 (CI / 软著合规检查脚本接), 默认人类可读 (员工平常用)
#   - 路径全用 expanduser, 写绝对 (~/.catfish/... → /Users/xxx/.catfish/...), 复制粘贴可验
#   - 权限位单独列, 600 / 644 / 755 一眼看 "token 文件是 600 ✓ 私有 / 还是 644 ✗ 漏权限"
#
# 不做:
#   - 不读对话文件内容打印 (那是 privacy nightmare, 员工想看自己开 ~/.catfish 看)
#   - 不删任何东西 (审计 ≠ 清理. 清理用 catfish logout / 手动 rm)
#   - 不联网下载任何"判定规则" (全本地逻辑, 防中央偷偷改判定)


# 本机数据扫描的目录清单 (按"员工最该知道的"排序).
#
# (相对 home, 是路径, 描述, 是否敏感) — 敏感=含 prompt 内容 / token.
_PRIVACY_SCAN_TARGETS = [
    (".catfish/auth/token.json",
     "我的 OAuth token (中央认证用, 不含对话)",
     True),
    (".catfish/gateway_audit.jsonl",
     "本机 audit log 历史归档 (PG 化前的镜像, 当前 PG-only 模式不再写)",
     False),
    (".catfish/",
     "catfish 数据目录 (token / 配置 / 边缘缓存)",
     False),
    (".hermes/sessions/",
     "hermes 对话会话 (含 prompt + response 全文 — 本机, 不上传)",
     True),
    (".hermes/memories/",
     "hermes 长期记忆 (USER.md / MEMORY.md, LLM 学的事实 — 本机, 不上传)",
     True),
    (".hermes/config.yaml",
     "hermes 配置 (含 service token, 不含对话)",
     True),
    (".hermes/.env",
     "hermes 第三方 API key (Tavily / 等, 本机调外网用)",
     True),
]


def _stat_path(p: Path) -> dict:
    """收 path 的 size / mode / 文件数 / 最新 mtime. 不存在返 exists=False."""
    if not p.exists():
        return {"exists": False, "path": str(p)}

    out: dict = {"exists": True, "path": str(p)}
    if p.is_file():
        st = p.stat()
        out.update({
            "kind": "file",
            "size_bytes": st.st_size,
            "mode_oct": oct(st.st_mode & 0o777),
            "mtime": int(st.st_mtime),
        })
    elif p.is_dir():
        # 目录: 算总大小 + 文件数, 不递归打印每个
        total = 0
        count = 0
        latest_mtime = 0
        try:
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        s = f.stat()
                        total += s.st_size
                        latest_mtime = max(latest_mtime, int(s.st_mtime))
                        count += 1
                    except OSError:
                        # symlink 断 / 权限不够 跳
                        continue
        except OSError as e:
            out["scan_error"] = str(e)
        st = p.stat()
        out.update({
            "kind": "dir",
            "file_count": count,
            "total_bytes": total,
            "mode_oct": oct(st.st_mode & 0o777),
            "mtime": int(latest_mtime or st.st_mtime),
        })
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f}TB"


def _fetch_audit_me(token: str, gateway: str) -> dict:
    """调 GET /api/audit/me. 任何失败抛 RuntimeError (让 caller 决定 fail-soft)."""
    url = f"{gateway}/api/audit/me"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连不上 gateway ({url}): {e.reason}") from e


def _scan_local_audit_jsonl(p: Path, since_ts: int) -> dict:
    """扫本机 ~/.catfish/gateway_audit.jsonl, 算今天往中央发了啥.

    返:
      - request_count: 今天总请求数
      - total_tokens:  今天 token 总数
      - by_model:      [{model, count}]
      - earliest_ts / latest_ts: 文件内最早 / 最新一条 (反映"我电脑上 audit 留多久")
    """
    empty = {
        "exists": False,
        "request_count": 0,
        "total_tokens": 0,
        "by_model": [],
        "earliest_ts": None,
        "latest_ts": None,
    }
    if not p.exists():
        return empty

    count_today = 0
    tokens_today = 0
    by_model_today: dict = {}
    earliest = None
    latest = None
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = int(r.get("ts") or 0)
                if not ts:
                    continue
                earliest = ts if earliest is None else min(earliest, ts)
                latest = ts if latest is None else max(latest, ts)
                if ts < since_ts:
                    continue
                count_today += 1
                tokens_today += int(r.get("prompt_tokens", 0) or 0) + int(r.get("completion_tokens", 0) or 0)
                m = r.get("model", "")
                by_model_today[m] = by_model_today.get(m, 0) + 1
    except OSError as e:
        logger.warning("scan audit jsonl: %s", e)
        return empty

    return {
        "exists": True,
        "path": str(p),
        "request_count": count_today,
        "total_tokens": tokens_today,
        "by_model": [{"model": m, "count": c} for m, c in sorted(by_model_today.items(), key=lambda x: -x[1])],
        "earliest_ts": earliest,
        "latest_ts": latest,
    }

def cmd_privacy_audit(args) -> int:
    """BL-EMPLOYEE-PRIVACY-VERIFICATION (#76, 5/25): 员工自查 "本机存了啥 + 中央存了我啥".

    给员工"我能验证, 不需要纯信任公司"的工具. 跟 #77 (Dashboard 隐私 tab) /
    #79 (gateway /api/audit/me) / #78 (员工 doc) 配套.

    退码:
      0: 全段成功 (含中央段)
      1: 中央段失败 (离线 / 未登录 / 中央挂), 本机段照样输出
    """
    json_mode = getattr(args, "json", False)
    home = Path.home()
    today_start = int(time.time()) - 86400  # 本机 audit jsonl ts 用秒, 跟 audit.rs 一致

    # ── 段 1: 本机数据 ────────────────────────────
    local_items = []
    for rel, desc, sensitive in _PRIVACY_SCAN_TARGETS:
        p = home / rel
        info = _stat_path(p)
        info["description"] = desc
        info["sensitive"] = sensitive
        local_items.append(info)

    local_audit_jsonl_path = home / ".catfish" / "gateway_audit.jsonl"
    local_audit_summary = _scan_local_audit_jsonl(local_audit_jsonl_path, today_start)

    # ── 段 2: 中央 (/api/audit/me) ────────────────
    central_section: dict = {"reachable": False, "reason": "", "data": None}
    store = load_token()
    if not store:
        central_section["reason"] = "未登录 (没 token, 跑: catfish login)"
    elif store.is_expired(buffer=0) and not store.refresh_token:
        central_section["reason"] = "token 过期且无 refresh_token (跑: catfish login)"
    else:
        # 过期但有 refresh, 自动 refresh 一次
        if store.is_expired():
            try:
                store = _do_refresh(store)
                save_token(store)
            except Exception as e:
                central_section["reason"] = f"refresh 失败: {e}"
                store = None  # 不再尝试

        if store is not None:
            try:
                gw = _gateway_url()
                data = _fetch_audit_me(store.access_token, gw)
                central_section["reachable"] = True
                central_section["data"] = data
                central_section["gateway"] = gw
            except RuntimeError as e:
                central_section["reason"] = str(e)

    report = {
        "version": 1,
        "generated_at": int(time.time()),
        "user_email": (store.user_email if store else None) or "(未登录)",
        "local": {
            "scanned_paths": local_items,
            "local_audit_jsonl_summary": local_audit_summary,
        },
        "central": central_section,
        "privacy_contract": [
            "中央只存 metadata (count / tokens / model / 时间戳), 不存 prompt / response 文本.",
            "对话 / 长期记忆 / 第三方 API key 全在本机 (~/.hermes/, ~/.catfish/), 不上传.",
            "本机 audit jsonl 是边缘 gateway 自己写的副本, 跟中央存的内容一致.",
            "中央 /api/audit/me 跟本机 audit jsonl 数字对得上 → 没偷偷上传额外字段.",
        ],
    }

    if json_mode:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if central_section["reachable"] else 1

    # ── 人类可读输出 ──────────────────────────────
    print("═" * 60)
    print("  catfish 隐私自查报告")
    print(f"  员工: {report['user_email']}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    print("═" * 60)

    print("\n── 段 1 · 本机数据 (在你电脑上, 不上传) ──")
    for item in local_items:
        marker = "🔒" if item.get("sensitive") else "📄"
        if not item["exists"]:
            print(f"  {marker} (不存在) {item['path']}")
            print(f"      {item['description']}")
            continue
        if item["kind"] == "file":
            size = _fmt_bytes(item["size_bytes"])
            print(f"  {marker} {item['path']}")
            print(f"      {item['description']}")
            print(f"      大小: {size}  权限: {item['mode_oct']}  改动: {time.strftime('%Y-%m-%d %H:%M', time.localtime(item['mtime']))}")
            # token 文件权限不是 600 → 警告
            if item["path"].endswith("token.json") and item["mode_oct"] != "0o600":
                print(f"      ⚠ token 文件权限不是 600! 任何同机器其他用户可读. 建议: chmod 600 {item['path']}")
        else:  # dir
            size = _fmt_bytes(item["total_bytes"])
            print(f"  {marker} {item['path']}/  ({item['file_count']} 个文件, {size})")
            print(f"      {item['description']}")
            if item["file_count"] > 0:
                print(f"      最近改动: {time.strftime('%Y-%m-%d %H:%M', time.localtime(item['mtime']))}")

    # ── PG-only 模式检测 ──
    # 5/9 之前 gateway 写 ~/.catfish/gateway_audit.jsonl (jsonl backend).
    # 5/9 之后配 CATFISH_DB_URL → 切 PG-only, metrics.py 不再写 jsonl.
    # 判定: 本机 latest_ts (jsonl 最后一条) < 中央 first_seen (PG 最早记录) → PG-only.
    a = local_audit_summary
    pg_only_mode = False
    if (a["exists"] and a.get("latest_ts")
        and central_section["reachable"]
        and central_section["data"]
        and central_section["data"].get("first_seen_ts")):
        local_latest_s = int(a["latest_ts"])
        central_earliest_s = int(central_section["data"]["first_seen_ts"] / 1000)
        if local_latest_s < central_earliest_s:
            pg_only_mode = True

    print("\n── 段 2 · 本机 audit log (历史镜像) ──")
    if not a["exists"]:
        print(f"  (无 jsonl: {local_audit_jsonl_path} 不存在 — 当前 PG-only 模式, 此为预期)")
    else:
        print(f"  路径: {a['path']}")
        if pg_only_mode:
            print(f"  📦 PG-only 模式: gateway 5/9 后切 PG backend, 本机 jsonl 是历史归档不再更新.")
            print(f"  历史范围: {time.strftime('%Y-%m-%d', time.localtime(a['earliest_ts']))} ~ {time.strftime('%Y-%m-%d', time.localtime(a['latest_ts']))}")
            print(f"  (中央 PG 是当前唯一真相, 见段 3)")
        else:
            print(f"  今日: {a['request_count']} 请求, {a['total_tokens']} tokens")
            if a["by_model"]:
                print(f"  今日按模型:")
                for r in a["by_model"][:10]:
                    print(f"    - {r['model']}: {r['count']} 次")
            if a["earliest_ts"]:
                print(f"  全量记录: {time.strftime('%Y-%m-%d', time.localtime(a['earliest_ts']))} ~ {time.strftime('%Y-%m-%d', time.localtime(a['latest_ts']))}")

    print("\n── 段 3 · 中央存了我啥 (调 /api/audit/me 验) ──")
    if not central_section["reachable"]:
        print(f"  ⚠ 中央段未连通: {central_section['reason']}")
        print(f"  (本机段照样有效, 离线员工也能审本机数据)")
    else:
        d = central_section["data"]
        print(f"  gateway: {central_section.get('gateway', '?')}")
        print(f"  user_email: {d.get('user_email')}")
        print(f"  department: {d.get('department') or '(未配置)'}")
        print(f"  今日: {d.get('request_count', 0)} 请求, {d.get('total_tokens', 0)} tokens")
        bm = d.get("by_model") or []
        if bm:
            print(f"  今日按模型:")
            for r in bm[:10]:
                print(f"    - {r['model']}: {r['count']} 次, {r['total_tokens']} tokens")
        if d.get("first_seen_ts"):
            f_str = time.strftime('%Y-%m-%d', time.localtime(d["first_seen_ts"] / 1000))
            l_str = time.strftime('%Y-%m-%d', time.localtime((d.get("last_seen_ts") or d["first_seen_ts"]) / 1000))
            print(f"  中央对我的最早记录: {f_str}  最新: {l_str}")
        print(f"  schema_note: {d.get('schema_note', '')}")

        # 自洽性检查: 只在 jsonl backend 模式 (非 PG-only) 才对照
        # PG-only 时本机 jsonl 不更新, 跟中央对比永远差一截 — 不该报警
        if not pg_only_mode and a["exists"] and d.get("request_count", 0) > 0:
            diff = abs(d.get("request_count", 0) - a["request_count"])
            if diff > 5:
                print(f"  ⚠ 本机今日 {a['request_count']} ≠ 中央 {d['request_count']} (差 {diff}), 可能漏统计 / 边缘gateway 没刷新")

    # 契约文案按 backend 动态调整 — PG-only 跟 jsonl-mirror 时代说法不同
    print("\n── 隐私契约 (审计判定依据) ──")
    if pg_only_mode:
        contracts = [
            "中央只存 metadata (count / tokens / model / 时间戳), 不存 prompt / response 文本.",
            "对话 / 长期记忆 / 第三方 API key 全在本机 (~/.hermes/, ~/.catfish/), 不上传.",
            "中央当前走 PG-only backend (gateway metrics.py 直写 PG, 不再镜像本机 jsonl).",
            "中央 /api/audit/me 返的就是 schema_note 写的字段, 多一个少一个就是契约违反.",
        ]
    else:
        contracts = report["privacy_contract"]
    for line in contracts:
        print(f"  · {line}")

    print(f"\n报告完成. 想给机器 / CI 看: 加 --json")
    return 0 if central_section["reachable"] else 1
