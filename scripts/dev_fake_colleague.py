#!/usr/bin/env python3
"""开发用: 一个人测同事群聊 / 派任务 —— 在本机扮演另一位同事 (11/5, docs/GROUP-CHAT-PLAN.md)。

只给开发机用:
  - 只连本机中央 (127.0.0.1 / localhost), 别的地址直接拒绝;
  - 登录凭证用本机身份服务器的签名密钥 (~/.catfish/identity-server/keys/private.pem) 现签, 1 小时过期,
    只有本机中央认; 不需要、也不碰任何人的密码;
  - 扮演的同事有自己的独立目录 ~/.catfish-dev-colleagues/<邮箱>/, 不碰你真实的 ~/.catfish。
走的是跟真同事完全一样的路 (tool-bridge colleague_rooms → 中央邮筒), 你在 Companion 里看到的就是真效果。

用法 (仓库根目录):
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> rooms            # 收邮筒 + 列出他的群
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> join [群名]      # 点「加入」(不写群名 = 第一个等加入的)
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> decline [群名]
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> say "收到 @chenhongbo" [群名]
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> show [群名]      # 看他那边的聊天记录
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> watch [群名]     # 一直收, 有新消息就打出来 (Ctrl+C 停)
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> leave [群名]
  edge/tool-bridge/venv/bin/python scripts/dev_fake_colleague.py <同事邮箱> create "群名" 邮箱1 邮箱2 ...  # 他来建群拉你

派任务 (docs/COLLEAGUE-TASK-PLAN.md; [任务名] 不写 = 最近收到的那个):
  ... <同事邮箱> tasks                          # 收邮筒 + 列出他收到的 / 派出的任务
  ... <同事邮箱> task-show [任务名]              # 看任务说明 (派出的看每个人的进度和结果)
  ... <同事邮箱> task-accept [任务名]            # 点「接下」
  ... <同事邮箱> task-done "做完了, 结果是…" [任务名]   # 点「交回」(可以再交一次覆盖)
  ... <同事邮箱> task-decline "这周排不开" [任务名]     # 点「不接」
  ... <同事邮箱> task-assign "要做什么" 邮箱1 [邮箱2 ...] [--deadline 2026-11-07] [--room 群名]   # 他派任务给你 (--room = 在群里派)
接下 / 交回 / 不接走的是界面按钮同一条路: 群里派的任务, 群里会出现状态。
定时 / 周期任务要在他电脑上建 hermes 定时任务, 这个工具不扮演 (会提示), 只测一次性任务和多阶段里的单段。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
KEY = Path.home() / ".catfish" / "identity-server" / "keys" / "private.pem"
ISSUER = os.environ.get("CATFISH_DEV_ISSUER", "http://127.0.0.1:8998")
AUDIENCE = "catfish-companion"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mint(email: str) -> str:
    """用本机身份服务器的密钥签一个 1 小时的开发凭证 (跟 identity-server jwt_signer 同格式)。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    if not KEY.exists():
        sys.exit(f"✗ 找不到本机身份服务器的密钥 {KEY} —— 先在本机起一次 identity-server")
    key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    head = {"alg": "RS256", "typ": "JWT", "kid": hashlib.sha256(pub).hexdigest()[:16]}
    body = {"iss": ISSUER, "sub": email, "aud": AUDIENCE, "iat": now, "exp": now + 3600,
            "email": email, "name": email.split("@")[0], "dev_fake_colleague": True}
    signing = f"{_b64(json.dumps(head).encode())}.{_b64(json.dumps(body).encode())}"
    sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64(sig)}"


def setup(email: str):
    home = Path.home() / ".catfish-dev-colleagues" / email
    home.mkdir(parents=True, exist_ok=True)
    os.environ["CATFISH_HOME"] = str(home)
    sys.path.insert(0, str(REPO / "edge" / "tool-bridge" / "src"))
    from catfish_tool_bridge import colleague_rooms as cr, skill_publish

    host = urlparse(skill_publish.GATEWAY_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(f"✗ 只能连本机中央, 现在是 {skill_publish.GATEWAY_URL}")
    token = mint(email)
    skill_publish._read_id_token = lambda: token
    return cr


def pick(cr, name: str | None, want: tuple[str, ...]):
    rooms = [r for r in cr.list_rooms()["rooms"] if r["my_state"] in want]
    if name:
        rooms = [r for r in rooms if name in (r["name"], r["room_id"])]
    if not rooms:
        sys.exit(f"✗ 没有{'叫「' + name + '」的' if name else ''}群 (状态要是 {'/'.join(want)}); 先跑 rooms 看看")
    return rooms[0]


def show_events(ev: list[dict]) -> None:
    for e in ev:
        print(f"  ← [{e['kind']}] 「{e['title']}」 {e['from']}" + (f": {e['text']}" if e.get("text") else ""))


def print_msgs(cr, room_id: str, n: int = 20) -> None:
    room = cr.load(room_id)
    for m in cr.messages(room_id)[-n:]:
        if m.get("kind") in ("system", "status"):
            print(f"    · {m['text']}")
        elif m.get("kind") == "card":
            c = m["card"]
            print(f"    {m['at'][11:16]} {cr._label(room, m.get('from', ''))} 📋 任务卡「{c['title']}」→ "
                  + "、".join(cr._label(room, e) for e in c["assignees"]) + (f" 截止 {c['deadline']}" if c.get("deadline") else ""))
        else:
            who = cr._label(room, m.get("from", ""))
            print(f"    {m['at'][11:16]} {who}: {m['text']}")


def pick_task(ct, name: str | None, *, owner: bool = False) -> dict:
    lst = ct.list_all()["outgoing" if owner else "incoming"]
    if name:
        lst = [t for t in lst if name in (t["title"], t["task_id"]) or name in t["title"]]
    if not lst:
        sys.exit(f"✗ 没有{'叫「' + name + '」的' if name else ''}{'派出的' if owner else '收到的'}任务; 先跑 tasks 看看")
    return lst[0]


def task_cmd(ct, cmd: str, rest: list[str]) -> None:
    r = ct.sync()
    for e in r["events"]:
        print(f"  ← [{e['kind']}] 「{e.get('title') or ''}」 {e.get('from') or ''}")
    if cmd == "tasks":
        a = ct.list_all()
        print("收到的:")
        for t in a["incoming"]:
            print(f"  {t['title']}  [{t['state']}]  来自 {t['owner']}" + (f"  截止 {t['deadline']}" if t["deadline"] else "")
                  + (f"  {t['schedule']}" if t.get("schedule") else "") + f"  ({t['task_id']})")
        print("派出的:")
        for t in a["outgoing"]:
            print(f"  {t['title']}  [{t['status']}]  {t['progress']}  ({t['task_id']})")
        return
    if cmd == "task-assign":
        opts = {}
        for flag in ("--deadline", "--room"):
            if flag in rest:
                i = rest.index(flag)
                opts[flag] = rest[i + 1] if i + 1 < len(rest) else None
                rest = rest[:i] + rest[i + 2:]
        deadline = opts.get("--deadline")
        room_id = None
        if opts.get("--room"):
            from catfish_tool_bridge import colleague_rooms as cr
            room_id = pick(cr, opts["--room"], ("joined",))["room_id"]
        if len(rest) < 2:
            sys.exit('✗ task-assign "要做什么" 邮箱1 [邮箱2 ...] [--deadline 2026-11-07]')
        t = ct.create({"text": rest[0], "recipients": rest[1:], "deadline": deadline, "room_id": room_id})["task"]
        out = ct.send(t["task_id"])
        print(f"✓ 派出「{t['title']}」给 {len(out['sent'])} 人" + (" (任务卡已进群)" if room_id and not out.get("room_notice") else "")
              + (f"; 没送到: {out['failed']}" if out["failed"] else "") + (f"; {out['room_notice']}" if out.get("room_notice") else ""))
        return
    if cmd == "task-show":
        name = rest[0] if rest else None
        mine = [t for t in ct.list_all()["outgoing"] if not name or name in t["title"] or name == t["task_id"]]
        theirs = [t for t in ct.list_all()["incoming"] if not name or name in t["title"] or name == t["task_id"]]
        if theirs:
            d = ct.get(theirs[0]["task_id"])
            rec = d["record"]
            print(f"「{rec['task']['title']}」 [{d['state']}] 来自 {rec['owner']}\n  {rec['task']['text']}")
            if rec.get("done"):
                print(f"  我交回的: {rec['done']['result']}")
        elif mine:
            d = ct.get(mine[0]["task_id"])
            print(f"「{d['task']['title']}」 [{d['task']['status']}]")
            for p in d["progress"]["rows"]:
                print(f"  {p.get('email')}: {p.get('state')}" + (f" — {p['result']}" if p.get("result") else ""))
        else:
            sys.exit("✗ 没有这个任务; 先跑 tasks 看看")
        return
    t = pick_task(ct, rest[1] if len(rest) > 1 else (rest[0] if cmd == "task-accept" and rest else None))
    if t.get("schedule"):
        sys.exit(f"✗ 「{t['title']}」是定时 / 周期任务 ({t['schedule']}): 要在他电脑上建 hermes 定时任务, 这个工具不扮演")
    # 走界面按钮同一条路 (pim_handlers): 群里派的任务会同时往群里发状态
    if cmd == "task-accept":
        ct.pim_handlers({"task_id": t["task_id"]})["pim/ctask_accept"]()
        print(f"✓ 接下了「{t['title']}」")
    elif cmd == "task-done":
        if not rest:
            sys.exit('✗ task-done "结果" [任务名]')
        out = ct.pim_handlers({"task_id": t["task_id"], "result": rest[0]})["pim/ctask_submit"]()
        print(f"✓ 交回了「{t['title']}」" + (" (晚了)" if out.get("late") else ""))
    elif cmd == "task-decline":
        ct.pim_handlers({"task_id": t["task_id"], "reason": rest[0] if rest else ""})["pim/ctask_decline"]()
        print(f"✓ 不接「{t['title']}」")
    else:
        sys.exit(__doc__)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    email, cmd, rest = sys.argv[1].strip().lower(), sys.argv[2], sys.argv[3:]
    if "@" not in email:
        sys.exit("✗ 第一个参数要写同事的完整邮箱")
    cr = setup(email)
    me = email
    if cmd == "tasks" or cmd.startswith("task-"):
        from catfish_tool_bridge import colleague_tasks as ct
        try:
            task_cmd(ct, cmd, rest)
        except (ValueError, LookupError) as e:
            sys.exit(f"✗ {e}")
        return
    try:
        r = cr.sync()
        show_events(r["events"])
        if cmd == "rooms":
            for room in cr.list_rooms()["rooms"]:
                print(f"  {room['name']}  [{room['my_state']}]  {room['joined']}/{room['members']} 人  未读 {room['unread']}  ({room['room_id']})")
        elif cmd in ("join", "decline"):
            room = pick(cr, rest[0] if rest else None, ("invited",))
            out = (cr.join if cmd == "join" else cr.decline)(room["room_id"])
            print(f"✓ {email} {'加入了' if cmd == 'join' else '没加入'}「{room['name']}」" +
                  (f"; 没送到: {out['failed']}" if out.get("failed") else ""))
        elif cmd == "say":
            if not rest:
                sys.exit("✗ say 后面写要说的话")
            room = pick(cr, rest[1] if len(rest) > 1 else None, ("joined",))
            members = cr.get(room["room_id"])["members"]
            ments = [m["email"] for m in members if m["email"] != me and f"@{m['name'] or m['email'].split('@')[0]}" in rest[0]]
            out = cr.send(room["room_id"], rest[0], mentions=ments)
            print(f"✓ 发到「{room['name']}」, 送到 {out['sent_to']} 人" + (f"; 没送到: {out['failed']}" if out["failed"] else ""))
        elif cmd == "show":
            room = pick(cr, rest[0] if rest else None, ("joined", "invited", "left", "removed", "disbanded", "declined"))
            print(f"「{room['name']}」 [{room['my_state']}]")
            print_msgs(cr, room["room_id"])
            cr.mark_read(room["room_id"])
        elif cmd == "watch":
            print(f"以 {email} 身份一直收 (3 秒一次), Ctrl+C 停")
            while True:
                time.sleep(3)
                show_events(cr.sync()["events"])
        elif cmd == "leave":
            room = pick(cr, rest[0] if rest else None, ("joined",))
            cr.leave(room["room_id"])
            print(f"✓ {email} 退出了「{room['name']}」")
        elif cmd == "create":
            if len(rest) < 2:
                sys.exit("✗ create \"群名\" 邮箱1 [邮箱2 ...]")
            out = cr.create({"name": rest[0], "purpose": "", "members": [{"email": e} for e in rest[1:]]})
            print(f"✓ 建了「{rest[0]}」, 邀请了 {len(rest) - 1} 人" + (f"; 没送到: {out['failed']}" if out["failed"] else ""))
        else:
            sys.exit(__doc__)
    except KeyboardInterrupt:
        pass
    except ValueError as e:
        sys.exit(f"✗ {e}")


if __name__ == "__main__":
    main()
