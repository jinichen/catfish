"""按**站点**找登录凭据 —— 教学流程不再需要人搬 secret_ref (8/18)。

# 为什么有这个模块

老流程里 `secret_ref` 是一个人工搬运的字符串:

    员工在 Companion 存密码  →  keychain://catfish-teaching:<自己起的名字>
                               ↓  ✗ 断了, 没有任何机制把它带过去
    教学时模型调              browser_fill(secret_ref='keychain://eis_password')
                               ↑ 这串从 memory 里那句话来, 靠人告诉它
                               ↓
    trace 记下它  →  _infer_params 焊成 password_ref 默认值  →  冻结进 script.py

人在中间搬一次, 然后**焊死**。8/17 鸿波改了 EIS 密码, UI 存进
`catfish-teaching:http://eis.ffcs.cn`, 而冻结的 eis-login 读的是
`eis_password` (4/28 建的那条) —— 登录报"账号或密码错误", 两边谁也不知道谁。

站点是天然的标识: 密码本来就是**某个网站的**密码。所以改成按当前页 URL 查,
中间那次人工搬运就不存在了, 也就没有东西可以焊死、可以过期。

# 匹配规则 (只认 hostname)

    sites: ["neis.ffcs.cn"]        ← 新数据, 显式列
    label: "http://eis.ffcs.cn"    ← 老数据, 当 URL 解析出 hostname
    label: "EIS"                   ← 老数据, 不是 URL → 不参与站点匹配

**不做任何模糊放宽**:

  · 不剥 `www.` —— 不同 host 就是不同 host, 要一起用就在 UI 里显式加
  · 不退到注册域 (`ffcs.cn`) —— 公司所有系统都在这个域下, 退一步就串号,
    拿 A 系统的密码去登 B 系统

  代价是 `eis.ffcs.cn → neis.ffcs.cn` 这种跳转要手工把两个都加上。这是有意的:
  这层关系只有人知道, 猜错的后果 (用错密码 / 撞锁定策略) 比多点一下严重。

# 边界

**这里只返 reference, 永远不碰密码本身。** 真正取值仍然走
secret_resolver.resolve_secret(), 而写入只走 Companion 的 Tauri IPC。
索引文件里也只有标签 / 引用 / 站点, 没有密码 (见 Companion 的
teaching_credentials.rs 文件头)。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("catfish.tool_bridge.credential_sites")


def _index_path() -> Path:
    """跟 advisor_io.py:25 / a2a_notifications.py:53 同一套: CATFISH_HOME 优先。

    ⚠ 写这个文件的是 Companion (teaching_credentials/index.rs:208), 而**那边只认
      $HOME, 不认 CATFISH_HOME**。所以设了 CATFISH_HOME 的机器上两边会错开:
      Companion 写 ~/.catfish/, 这里读 $CATFISH_HOME/ —— 表现成"存了但教学说没
      存过"。目前没人设它, 先记在这儿; 真要统一得改 Rust 那侧, 顺带影响别的模块,
      不在这次改动范围内。
    """
    env = os.environ.get("CATFISH_HOME", "").strip()
    base = Path(env).expanduser() if env else Path.home() / ".catfish"
    return base / "teaching_credentials.json"


def host_of(url_or_host: str) -> str:
    """从 URL 或裸 host 取 hostname, 小写。取不出来返空串。

    'http://eis.ffcs.cn/cas/login?x=1' → 'eis.ffcs.cn'
    'eis.ffcs.cn'                      → 'eis.ffcs.cn'
    'EIS'                              → ''            (不是站点)
    """
    s = (url_or_host or "").strip()
    if not s:
        return ""
    if "://" in s:
        # ⚠ 必须**限定 scheme**, 不能只看 "有没有 ://"。
        #
        #   urlparse("keychain://catfish-teaching:EIS").hostname == "catfish-teaching"
        #
        # 它把 keychain 当 scheme、catfish-teaching 当 host、EIS 当端口, 全程不报错。
        # 而 8/17 那条坏数据的 label 正是这个形状 —— 宽判据会给它凭空配一个叫
        # "catfish-teaching" 的站点, 于是任何 host 是这个的页面都会拿到别人的密码。
        #
        # 浏览器地址栏只会是 http/https (page.url 也只可能是这两个), 收窄没有代价。
        parts = urlparse(s)
        if parts.scheme not in ("http", "https"):
            return ""
        return (parts.hostname or "").lower()
    # 裸串: 当 host 试一次。要求含 '.' 且没有空格 / 路径 —— 否则 "EIS" 这种
    # 人起的名字会被当成 hostname, 匹配上一个根本不存在的站点。
    if "." in s and " " not in s and "/" not in s:
        return s.lower()
    return ""


def _load() -> list[dict[str, Any]]:
    """读索引。读不出来一律当空 —— 查不到只是"要存一次", 不该炸。"""
    p = _index_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("teaching_credentials.json 解析失败, 当空: %s", e)
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def sites_of(entry: dict[str, Any]) -> list[str]:
    """一条凭据覆盖哪些 hostname。

    新数据看 `sites`; 老数据退回把 `label` 当 URL 解析 —— 8/17 那条 label 正好
    是 `http://eis.ffcs.cn`, 所以老数据不用迁移也能立刻匹配上。
    """
    raw = entry.get("sites")
    if isinstance(raw, list) and raw:
        return [h for h in (host_of(str(x)) for x in raw) if h]
    h = host_of(str(entry.get("label") or ""))
    return [h] if h else []


def ref_for_url(url: str) -> str | None:
    """当前页该用哪条凭据 → 返 secret_ref。没有就返 None。

    多条命中同一个 host 时取 **createdAt 最新**的 —— 那多半是员工刚改过密码
    存的新条目。老的留着不动 (删除是员工的动作, 不该由查找顺手做掉)。
    """
    host = host_of(url)
    if not host:
        return None
    hits = [
        e for e in _load()
        if host in sites_of(e) and str(e.get("reference") or "").strip()
    ]
    if not hits:
        return None
    if len(hits) > 1:
        hits.sort(key=lambda e: str(e.get("createdAt") or e.get("created_at") or ""),
                  reverse=True)
        logger.info(
            "站点 %s 命中 %d 条凭据, 取最新的那条 (%s)",
            host, len(hits), hits[0].get("label"),
        )
    return str(hits[0]["reference"]).strip()


def known_sites() -> list[str]:
    """本机存过密码的所有 hostname —— 给"这个站点没存过"的提示用。"""
    out: list[str] = []
    for e in _load():
        for h in sites_of(e):
            if h not in out:
                out.append(h)
    return out
