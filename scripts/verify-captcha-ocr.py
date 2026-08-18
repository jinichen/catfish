#!/usr/bin/env python3
"""量一下验证码识别到底准不准 —— **靠你的眼睛, 不靠 confidence**。

─────────────────────────────────────────────────────────────────────────
# 为什么要人来看

`recognize_captcha` 返回的 confidence 是个启发式, 不是真概率。看它的实现
(recognize_captcha.py:114 `_estimate_confidence`):

    if text == "?":                       return 0.0
    if 含 "验证码"/"我看到" 等解释词:      return 0.2
    if hint 说 4 位 and len(text) != 4:   return 0.55
    return 0.85          ← 长度和字符集对上就 0.85

**它只看格式, 不看认得对不对。** 图里是 `2fW2`, 模型答 `8bN5` —— 四位字母
数字, 格式全对, confidence 0.85, 直接放行。

而 skill 里的重试逻辑 (skill_freeze_template.py 的 _CAPTCHA_RETRY_BLOCK)
判的就是 `confidence >= 0.6`:

    · 模型不肯答 (返 '?' 或带解释话)  → 0.0/0.2 → 重试 ✓ 拦得住
    · 模型答错但格式对                → 0.85    → 放行 ✗ 拦不住

验证码识别最常见的失败恰恰是后者。所以"跑 3 次都失败"和"一次通过但密码填错"
是两种完全不同的病, confidence 区分不了 —— 只有人对着图看才分得清。

# 这个脚本做什么

每一轮:
  1. goto 登录页 (第一轮) / 点验证码图刷新 (后续轮)
  2. 截**验证码那个元素**存成 PNG 到 ~/.catfish/captcha-test/
  3. 调 recognize_captcha, 打印它认出来的字符 + confidence
  4. 停下来让你看图, 你敲 y/n 告诉它对不对

跑完给三个数:
  · 识别率   —— 模型愿意给答案的比例 (confidence >= 0.6)
  · 真准确率 —— 你判定认对了的比例        ← 这个才是要的
  · 假高分   —— confidence >= 0.6 但你说错了的  ← 这个是最危险的一栏

# 不做什么

不填密码、不点登录、不碰凭据库。只截图 + 识别。

用法:
    python3 scripts/verify-captcha-ocr.py            # 默认 10 轮
    python3 scripts/verify-captcha-ocr.py -n 20
    python3 scripts/verify-captcha-ocr.py --url http://eis.ffcs.cn --selector '#captchaImg'
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path

B = "\033[1m"; R = "\033[31m"; G = "\033[32m"; Y = "\033[33m"; D = "\033[2m"; N = "\033[0m"

DEFAULT_SOCK = Path.home() / ".catfish" / "tool-bridge.sock"
OUT_DIR = Path.home() / ".catfish" / "captcha-test"


class Bridge:
    """tool-bridge 的行分隔 JSON-RPC 客户端 (server.py:_handle_request)。"""

    def __init__(self, p: Path):
        self.path = p
        self.sock: socket.socket | None = None
        self._id = 0

    def connect(self) -> str:
        raw = self.path.read_bytes() if self.path.is_file() else b""
        if raw.strip().isdigit():                      # Windows: 端口号文件
            s = socket.create_connection(("127.0.0.1", int(raw.strip())), timeout=60)
            self.sock = s
            return f"tcp://127.0.0.1:{int(raw.strip())}"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(90)                               # vision 模型慢, 给足
        s.connect(str(self.path))
        self.sock = s
        return f"unix://{self.path}"

    def dispatch(self, name: str, args: dict):
        """两层 ok 都要看 (adapter.py:393) —— 只看外层会把工具自己的失败当成功。"""
        assert self.sock
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": "tools/dispatch",
               "params": {"name": name, "args": args}}
        self.sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                return False, "连接被对端关闭"
            buf += chunk
        resp = json.loads(buf.decode())
        if "error" in resp:                                   # ① JSON-RPC 层
            return False, resp["error"].get("message", resp["error"])
        res = resp.get("result")
        if isinstance(res, dict):
            if res.get("ok") is False or res.get("type") == "error":   # ② adapter 层
                return False, res.get("error") or "(工具返错但没给 error)"
            inner = res.get("result", res)
            # ③ **工具自己那层**。
            #
            #   8/18 第一版漏了这层, 于是 recognize_captcha 返
            #   {"ok": false, "error": "..."} 时, 我 .get("text","") 拿到空串,
            #   打印成「模型认成 ''  confidence 0.00」还问员工"这几个字符对吗" ——
            #   错误原文一直在, 被我自己扔了。
            #
            #   讽刺的是同一个坑我在 verify-browser-family.py 里专门写了注释防过,
            #   换个脚本又踩一次。
            if isinstance(inner, dict) and (
                inner.get("ok") is False or inner.get("type") == "error"
            ):
                return False, inner.get("error") or "(工具返错但没给 error)"
            return True, inner
        return True, res


def _unarchive(br: "Bridge", shot) -> str:
    """把截图结果里的 base64 取出来, 必要时从归档里读回。

    tool-bridge 的 adapter 对**任何**超阈值的 tool result 做归档
    (adapter.py:234 `_maybe_archive_oversized_result`), 把 result 换成:

        [已归档: archive_ref=486be5e2a96c7307, tool=catfish_browser_screenshot,
         16.8KB / 1 行] 摘要 + 头尾预览

    一张验证码 PNG 的 base64 十几 KB, 必然超阈值 —— 所以**正常路径上就会**
    拿到这个占位串, 不是异常。

    8/18 第一版把它当成了"selector 匹配不到", 直接报错退出。实际截图是成功的,
    `#captchaImg` 也确实存在 (selector 匹配不到的话上一层就返 type=error 了)。
    判据比真事宽: 用"拿不到 base64"去判断"元素不存在"。
    """
    raw = shot if isinstance(shot, str) else ""
    if isinstance(shot, dict):
        raw = shot.get("image") or shot.get("image_b64") or shot.get("data") or ""
        if not raw:
            raw = json.dumps(shot, ensure_ascii=False)
    if "archive_ref=" not in raw:
        return raw
    ref = raw.split("archive_ref=", 1)[1].split(",", 1)[0].strip().strip("]")
    ok, full = br.dispatch("catfish_read_tool_archive", {"ref": ref})
    if not ok:
        return ""
    if isinstance(full, dict):
        return (full.get("content") or full.get("result")
                or full.get("image") or json.dumps(full, ensure_ascii=False))
    return str(full)


def _save_png(b64: str, path: Path) -> bool:
    """把 data:image/png;base64,xxx 或裸 base64 落盘。"""
    if not b64:
        return False
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        path.write_bytes(base64.b64decode(b64))
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", type=Path, default=DEFAULT_SOCK)
    ap.add_argument("--url", default="http://eis.ffcs.cn", help="登录页 (eis-login skill 用的就是它)")
    ap.add_argument("--selector", default="#captchaImg")
    ap.add_argument("--hint", default="alphanumeric_4")
    ap.add_argument("-n", "--rounds", type=int, default=10)
    a = ap.parse_args()

    if not a.socket.exists():
        print(f"   {R}❌ 找不到 {a.socket}{N} —— tool-bridge 没起来, 先开 Companion")
        return 2
    br = Bridge(a.socket)
    try:
        ep = br.connect()
    except Exception as e:
        print(f"   {R}❌ 连不上: {e}{N}")
        return 2
    print(f"{B}══ 已连接{N} {D}{ep}{N}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    ok, res = br.dispatch("catfish_browser_goto", {"url": a.url, "timeout_seconds": 45})
    if not ok:
        print(f"   {R}✗ 打不开 {a.url}: {res}{N}")
        print("     · 连不上/超时 → Chrome CDP 没 attach, 跑 catfish-browser-attach.sh")
        print("     · 404/拒接    → 不在公司网里")
        return 1
    actual = (res or {}).get("actual_url", "") if isinstance(res, dict) else ""
    title = (res or {}).get("actual_title", "") if isinstance(res, dict) else ""
    print(f"   {G}✓ 已打开{N} {D}{title} {actual[:70]}{N}")
    if actual and not actual.startswith(a.url):
        # 8/18 实测: eis.ffcs.cn 会跳到 neis.ffcs.cn/cas/login —— DOM 是另一套。
        print(f"   {Y}⚠ 被重定向了 (从 {a.url} 到上面那个)。DOM 很可能跟"
              f"冻结 skill 时不是一套, selector 要重新确认。{N}")

    # selector 先探一下再开跑 —— 不然十轮全是空结果, 还问员工"对不对"
    ok, snap = br.dispatch("catfish_browser_snapshot", {"max_elements": 500})
    if ok:
        blob = json.dumps(snap, ensure_ascii=False) if not isinstance(snap, str) else snap
        if a.selector.lstrip("#.") not in blob:
            print(f"   {Y}⚠ 快照里没找到 {a.selector!r}。页面上像验证码的元素:{N}")
            import re as _re
            cands = sorted(set(
                _re.findall(r'"selector"\s*:\s*"([^"]*(?:captcha|code|verify|vcode)[^"]*)"',
                            blob, _re.I)
            ))
            for c in cands[:8]:
                print(f"        {c}")
            if not cands:
                print("        (一个都没匹配到 —— 用浏览器右键→检查 手工看)")
            print(f"   {D}用 --selector 传进来重跑。{N}")
    print()

    rows = []
    for i in range(1, a.rounds + 1):
        if i > 1:
            # 点验证码图 = 换一张 (eis-login skill 刷新验证码就是这么干的)
            br.dispatch("catfish_browser_click", {"selector": a.selector})
            time.sleep(0.8)

        png = OUT_DIR / f"{stamp}-{i:02d}.png"
        ok, shot = br.dispatch("catfish_browser_screenshot",
                               {"selector": a.selector, "compress": "none"})
        saved = False
        if not ok:
            # selector 匹配不到时 locator.wait_for 会抛, 这里才是真的"截不到"
            # (catfish_tools_browser.py:478-495 —— **不会**退回整屏)。
            print(f"  {i:>2}. {R}✗ 截图失败{N}: {str(shot)[:150]}")
            print(f"      {Y}没有样本, 识别准不准无从谈起。先确认 selector:{N}")
            print("        浏览器右键验证码图 → 检查, 拿到真实 id/class,")
            print("        再 --selector '<那个>' 重跑。")
            print(f"      {Y}⚠ 同一个 selector 写死在 skills/department/eis-login/script.py:98{N}")
            return 1
        saved = _save_png(_unarchive(br, shot), png)

        t0 = time.time()
        ok, out = br.dispatch("catfish_recognize_captcha",
                              {"selector": a.selector, "hint": a.hint})
        dt = time.time() - t0

        if not ok:
            print(f"  {i:>2}. {R}调用失败{N}: {str(out)[:130]}")
            rows.append((i, None, 0.0, None, dt))
            continue

        text = (out or {}).get("text", "") if isinstance(out, dict) else str(out)
        conf = (out or {}).get("confidence", 0.0) if isinstance(out, dict) else 0.0

        # 空串跟"认错"是两回事, 不能混进准确率统计里让人判断
        if not str(text).strip():
            print(f"  {i:>2}. {R}✗ 识别返回空串{N} (confidence {conf:.2f}) {dt:.1f}s")
            print(f"      图在 {png} —— 打开看看是不是截到了别的东西")
            rows.append((i, None, conf, None, dt))
            continue

        passes = conf >= 0.6

        print(f"  {i:>2}. 模型认成 {B}{text!r}{N}  confidence {conf:.2f} "
              f"{'(过闸门)' if passes else R + '(被重试拦下)' + N}  {dt:.1f}s")
        if saved:
            print(f"      图: {png}")
        else:
            print(f"      {Y}图没落盘 (归档读回失败) —— 直接看浏览器里那张{N}")

        ans = input("      这张图里真的是这几个字符吗? [y/n/s=跳过] ").strip().lower()
        correct = True if ans == "y" else (False if ans == "n" else None)
        rows.append((i, text, conf, correct, dt))

    # ── 汇总 ──────────────────────────────────────────────────
    judged = [r for r in rows if r[3] is not None]
    print(f"\n{B}══ 结果 ({len(rows)} 轮, 你判定了 {len(judged)} 轮){N}")
    if not judged:
        print(f"   {Y}一轮都没判定, 什么结论都得不出来。{N}")
        return 0

    gate = [r for r in judged if r[2] >= 0.6]
    right = [r for r in judged if r[3]]
    false_hi = [r for r in judged if r[2] >= 0.6 and r[3] is False]

    print(f"   过闸门 (confidence≥0.6)  {len(gate)}/{len(judged)}  = {len(gate)/len(judged):.0%}")
    print(f"   {B}真认对{N}                  {len(right)}/{len(judged)}  = {len(right)/len(judged):.0%}")
    print(f"   {R}假高分 (过闸门但认错){N}    {len(false_hi)}/{len(judged)}"
          f"  = {len(false_hi)/len(judged):.0%}")
    print(f"   平均耗时 {sum(r[4] for r in rows)/len(rows):.1f}s")

    print()
    if false_hi:
        print(f"   {R}★ 假高分就是最要命的那一栏。{N}")
        print("     confidence 只看长度和字符集, 认错但格式对照样 0.85 放行 ——")
        print("     skill 的重试 (confidence>=0.6) 拦不住, 结果是拿错验证码去提交,")
        print("     表现成'登录失败'而不是'识别失败'。要修得让它验真结果, 不是验格式。")
    elif len(right) == len(judged):
        print(f"   {G}这一批全对 —— 识别本身没问题。{N}")
        print("     那『卡死』就在别处: 看看是不是 vision 模型被限流 (429),")
        print("     或者根本没走到这一步。跑 scripts/hermes-log-triage.sh 看错误栏。")
    else:
        print("   认错的那几张被闸门拦下了 —— 重试机制在起作用, 只是次数不够。")
        print("   eis-login 的 max_captcha_retry 默认 3, 按上面的准确率算够不够。")

    print(f"\n   图都在 {OUT_DIR}/{stamp}-*.png, 可以回头对。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
