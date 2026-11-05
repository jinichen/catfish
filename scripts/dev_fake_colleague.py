#!/usr/bin/env python3
"""开发用: 一个人测同事群聊 —— 在本机扮演另一位同事 (11/5, docs/GROUP-CHAT-PLAN.md)。

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
        if m.get("kind") == "system":
            print(f"    · {m['text']}")
        else:
            who = cr._label(room, m.get("from", ""))
            print(f"    {m['at'][11:16]} {who}: {m['text']}")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    email, cmd, rest = sys.argv[1].strip().lower(), sys.argv[2], sys.argv[3:]
    if "@" not in email:
        sys.exit("✗ 第一个参数要写同事的完整邮箱")
    cr = setup(email)
    me = email
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
