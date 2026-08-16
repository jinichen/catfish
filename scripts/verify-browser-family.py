#!/usr/bin/env python3
"""周一到公司跑这个 — 验 catfish_browser_* 这一族到底通不通。

─────────────────────────────────────────────────────────────────────────
# 这个脚本要回答的那一个问题

8/15 晚查「一句话为什么烧 4-5 万输入 token」时翻出来一件事:

  网关 BL-FIX4 (5/8) 定的规矩是 —— tools 里出现 catfish_browser_* 就丢掉
  hermes 自带的 browser_*, 两族不并存。判据在 tools_sanitizer.py:309:

      has_catfish_browsers = _has_catfish_browser_tools(tools)   # 扫入参数组

  8/13 tool_search 上线后, 9 个 catfish_browser_* 全被 defer 了 (P43 的
  _PROMOTE 名单里一个浏览器工具都没有)。数组里没有 → 判据为 False →
  **这条去重从 8/13 起就再没触发过**。

  结果: hermes 那 12 个 browser_* 常驻烧 6,872+ token, catfish 那 9 个躲在
  tool_search 的 bridge 后面。跟 5/8 定的规矩正好相反, 而且没有任何报错。

  判据 (「数组里有没有」) 和真事 (「catfish 浏览器工具可不可达」) 在
  tool_search 上线那天分了家。

修法是「修 BL-FIX4 判据 + P43 提升 catfish 那 5 个」, 净省 ≥3,585 token/轮。
但那一刀会**丢掉现在正在用的 hermes 一族**, 所以动手前必须先确认要换过去的
catfish 一族真的能干活。

  → 这个脚本就是那个前置。它绿之前, 那一刀不许动。

# 为什么不能在家里跑

内网 B/S (CHANGELOG 3535-3540 实盘: 10.10.111.53:8776 登录 → 抓用户列表)
只有在公司网里够得着。而恰恰是内网系统这条链最不能断。

# 这个脚本**不做**什么

不接受任何密码 / 凭据参数。红线: 密码只走 Tauri IPC 到 OS 凭据库, 不进
shell 参数 / 配置文件 / 聊天。所以这里只验到「能导航 + 能识别表单结构 +
能定位元素」为止 —— 这三步够证明通道是活的。真正的登录留给人在 Companion
里手工做一次。

# 写法上跟 hermes-log-triage.sh 同一套纪律

每一项都带前置。前置没跑过的项**不给绿色**, 明说「查不了」。
8/15 那晚连着三次拿「某个计数是 0」当好消息, 三次都错 —— 0 样本时
「没有坏消息」不是「好消息」。

用法:
    python3 scripts/verify-browser-family.py
    python3 scripts/verify-browser-family.py --url http://10.10.111.53:8776
    python3 scripts/verify-browser-family.py --find 登录
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any

B = "\033[1m"; R = "\033[31m"; G = "\033[32m"; Y = "\033[33m"; D = "\033[2m"; N = "\033[0m"

#: server.py:__main__ 的默认值。Unix 是 unix socket, Windows 是**端口号文件**
#: (BL-WIN8 5/8) —— 里面存 ASCII 端口号, 语义一样都是「一个文件代表 RPC 端点」。
DEFAULT_SOCK = Path.home() / ".catfish" / "tool-bridge.sock"

#: 内网 B/S 实盘流程用到的 5 个 (CHANGELOG 3535-3540)。要丢 hermes 一族的话,
#: 至少这 5 个得能用 —— 少一个那条链就断在那一步。
CORE5 = [
    "catfish_browser_goto",
    "catfish_browser_snapshot",
    "catfish_browser_click",
    "catfish_browser_fill",
    "catfish_browser_find_by_text",
]


class Bridge:
    """tool-bridge 的行分隔 JSON-RPC 客户端 (server.py:_handle_request)。"""

    def __init__(self, sock_path: Path):
        self.path = sock_path
        self.sock: socket.socket | None = None
        self._id = 0

    def connect(self) -> str:
        raw = self.path.read_bytes() if self.path.is_file() else b""
        if raw.strip().isdigit():          # Windows: 端口号文件
            port = int(raw.strip())
            s = socket.create_connection(("127.0.0.1", port), timeout=30)
            self.sock = s
            return f"tcp://127.0.0.1:{port}"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(60)                   # goto 首次进内网可能 10s+ (CHANGELOG 3535)
        s.connect(str(self.path))
        self.sock = s
        return f"unix://{self.path}"

    def call(self, method: str, params: dict | None = None) -> tuple[bool, Any]:
        assert self.sock
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params:
            req["params"] = params
        self.sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(65536)
            if not chunk:
                return False, "连接被对端关闭"
            buf += chunk
        resp = json.loads(buf.decode())
        if "error" in resp:
            return False, resp["error"].get("message", resp["error"])
        return True, resp.get("result")

    def dispatch(self, name: str, args: dict) -> tuple[bool, Any]:
        """tools/dispatch 有**两层** ok: RPC 层 + adapter 层 (adapter.py:393)。

        只看外层会把工具自己的失败当成功 —— 正是这个脚本要防的那种读法。
        """
        ok, res = self.call("tools/dispatch", {"name": name, "args": args})
        if not ok:
            return False, res
        if isinstance(res, dict) and res.get("ok") is False:
            return False, res.get("error") or "(工具返 ok=False 但没给 error)"
        return True, res.get("result") if isinstance(res, dict) else res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", type=Path, default=DEFAULT_SOCK)
    ap.add_argument("--url", default="http://10.10.111.53:8776",
                    help="内网 B/S 地址 (CHANGELOG 3535 实盘那台)")
    ap.add_argument("--find", default="登录", help="snapshot 之后拿这个词做定位测试")
    a = ap.parse_args()

    print(f"{B}══ 0. 前置: tool-bridge 端点{N}")
    if not a.socket.exists():
        print(f"   {R}❌ 找不到 {a.socket}{N}")
        print("      tool-bridge 没起来。Companion 起着的话它该自动 spawn ——")
        print("      先开 Companion, 或手工: python3 -m catfish_tool_bridge")
        print(f"   {D}后面每一项都依赖它, 全部查不了。{N}")
        return 2
    br = Bridge(a.socket)
    try:
        ep = br.connect()
    except Exception as e:
        print(f"   {R}❌ 连不上 {a.socket}: {e}{N}")
        print(f"   {D}文件在但连不上 —— 多半是上次 crash 留下的死 socket。重启 Companion。{N}")
        return 2
    print(f"   {G}✓ 已连接{N} {D}{ep}{N}\n")

    print(f"{B}══ 1. health{N}")
    ok, h = br.call("health")
    if not ok:
        print(f"   {R}❌ {h}{N}\n   {D}bridge 在但不健康, 后面的结论都不成立。{N}")
        return 2
    print(f"   {G}✓{N} 工具总数 {h.get('tool_count')} (native {h.get('native_tool_count')})")
    print(f"   {D}toolsets: {', '.join(map(str, h.get('toolsets', [])))}{N}\n")

    print(f"{B}══ 2. 那 9 个 catfish_browser_* 注册了没{N}")
    ok, tools = br.call("tools/list")
    if not ok:
        print(f"   {R}❌ tools/list 失败: {tools}{N}")
        return 2
    names = set()
    for t in tools if isinstance(tools, list) else []:
        n = t.get("name") if isinstance(t, dict) else None
        if not n and isinstance(t, dict):
            n = (t.get("function") or {}).get("name")
        if n:
            names.add(n)
    browsers = sorted(n for n in names if n.startswith("catfish_browser_"))
    print(f"   注册到 {len(browsers)} 个 catfish_browser_*")
    missing = [n for n in CORE5 if n not in names]
    for n in CORE5:
        mark = f"{G}✓{N}" if n in names else f"{R}✗ 缺{N}"
        print(f"      {mark} {n}")
    if missing:
        print(f"\n   {R}❌ 内网 B/S 流程要的 {len(missing)} 个不在注册表里。{N}")
        print("      这一族替不了 hermes 那一族 —— BL-FIX4 那一刀不能动。")
        return 1
    print()

    print(f"{B}══ 3. 真跑一次: goto → snapshot → find_by_text{N}")
    print(f"   {D}目标 {a.url}  (首次进内网 10s+ 属正常, CHANGELOG 3535 记录过){N}")

    ok, res = br.dispatch("catfish_browser_goto", {"url": a.url, "timeout_seconds": 45})
    if not ok:
        print(f"   {R}✗ goto 失败: {res}{N}")
        print(f"\n   {Y}先分清是哪一头:{N}")
        print("      · 连不上 / 超时  → Chrome CDP 没 attach。跑 catfish-browser-attach.sh")
        print("        (CHANGELOG 3990: Chrome 重启后 webSocketDebuggerUrl 的 UUID 会变,")
        print("         config.yaml 里 browser.cdp_url 写死就会 404)")
        print("      · 404 / 拒接    → 不在公司网里, 或那台机器换地址了")
        return 1
    print(f"   {G}✓ goto{N} {D}{str(res)[:110]}{N}")

    # FIX9 的教训: max_elements 给小了登录按钮会被 nav/footer 挤出去, LLM 找不到
    # 就去截图, 撞 Playwright sync 卡死。这里直接给上限。
    ok, snap = br.dispatch("catfish_browser_snapshot", {"max_elements": 500})
    if not ok:
        print(f"   {R}✗ snapshot 失败: {snap}{N}")
        print(f"   {D}FIX3 给它加过 a11y → JS evaluate 的 fallback。两条都挂说明页面没真加载。{N}")
        return 1
    txt = json.dumps(snap, ensure_ascii=False) if not isinstance(snap, str) else snap
    n_el = txt.count('"role"') or txt.count("role=")
    print(f"   {G}✓ snapshot{N} {len(txt):,} 字符, 约 {n_el} 个元素")
    if n_el == 0:
        print(f"   {R}   ⚠ 0 个元素 —— 「没报错」在这里不等于「成功」。{N}")
        print("      页面可能还没渲染完, 或 a11y 和 JS 两条 fallback 都返了空。")
        return 1

    ok, found = br.dispatch("catfish_browser_find_by_text",
                            {"text": a.find, "max_results": 10})
    if not ok:
        print(f"   {R}✗ find_by_text('{a.find}') 失败: {found}{N}")
        return 1
    ftxt = json.dumps(found, ensure_ascii=False) if not isinstance(found, str) else found
    hit = ftxt.count("selector")
    print(f"   {G}✓ find_by_text('{a.find}'){N} 命中 {hit} 个")
    if hit == 0:
        print(f"   {Y}   ⚠ 0 个命中。换个 --find 词再试 —— 定位不到就没法点登录。{N}")
        return 1

    print(f"\n{B}══ 结论{N}")
    print(f"   {G}catfish_browser_* 这一族通了{N} (导航 / 识别表单 / 定位元素 三步都真出了东西)")
    print()
    print("   可以动那一刀了:")
    print("     ① tools_sanitizer.py — BL-FIX4 判据改成看**可达性**, 不看入参数组")
    print("        (现在: has_catfish_browsers = 数组里有没有 → tool_search defer 后恒为 False)")
    print("     ② plugin_core_tools.py — _PROMOTE 加上这 5 个")
    print("     ③ 两处必须同一次改完:")
    print("        只改 ① → hermes 一族被丢, catfish 一族还在 defer 后面 → 浏览器彻底不可用")
    print("        只改 ② → 两族并存, 反而更贵")
    print()
    print("   净省 ≥ 3,585 token/轮 (-6,872 hermes 12 个, +3,287 catfish 5 个)")
    print()
    print(f"   {Y}还欠一次人工:{N} 在 Companion 里手工走一遍真登录 (密码只走凭据库,")
    print("   不进这个脚本)。这里只证明了通道活着, 没证明整条业务链跑得通。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
