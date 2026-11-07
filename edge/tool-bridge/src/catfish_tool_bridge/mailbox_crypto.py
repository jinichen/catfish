"""同事协作邮筒的端到端加密 (11/6, docs/GROUP-CHAT-PLAN.md §4.1) —— 填表 / 派任务 / 群聊共用。

# 为什么

邮筒原来投的是明文 JSON: 中央程序不解析, 但投递到取走之间 (最长 7 天) 内容在中央库里, 登库的人看得到。
改成每台员工电脑一对 X25519 密钥, 私钥只在本机; 发件端按收件人的公钥加密后再投 —— 中央库里只剩密文。

# 怎么加密 (信封 v2)

    内容 = zlib(信封 JSON), 用随机内容密钥 K 做 ChaCha20-Poly1305 (附加数据 = 种类 | 发件人 | 收件人)
    K 再给收件人的**每台电脑**各包一份: X25519(我的私钥, 对方那台的公钥) → HKDF → ChaCha20-Poly1305
    payload = {"v": 2, "sd": 我的设备号, "spk": 我的公钥, "n", "c", "k": [{"d": 对方设备号, "n", "w"}]}

- 静态-静态 DH: 只有发件那台电脑的私钥能包出这份 K —— 收件人解得开, 就说明确实是那台电脑发的;
  再核对 `spk` 是不是发件人登记过的公钥 (中央目录 / 本机记下的), 中央冒充不了别人。
- 附加数据里有种类 / 发件人 / 收件人: 中央改了邮件的 from / to / kind, 解密直接失败。
- 收件人还没有公钥 (没升级 / 升级后没开过鲶鱼): **拦住不发** (11/6 定), 抛 NoKeyError 说清原因。
- 收到老版本发来的明文 (v1) 照收, 标 `_plain` (界面显示「未加密」)。

# 密钥放哪

`~/.catfish/collab_keys/device.json` (只有本人可读, 0600)。不放系统钥匙串: tool-bridge 读钥匙串在 macOS 上会弹
看不见的授权框 (见 companion_secrets.py 文件头)。11/6 做本机加密 (§4.3) 时也没挪进钥匙串: 钥匙串里的东西
只有 Companion 开着才拿得到, 而定时任务交结果、小鲶后台交回任务都要在 Companion 没开时收发邮筒。
换电脑**不随迁移包走** (catalog.rs 里 `collab_keys` 是 Secret: 包丢了也不泄露): 新电脑第一次取邮筒时生成新密钥并登记,
同事那边提示一次「密钥变了」; 换之前发给旧电脑、还没取的那几封, 新电脑解不开 (界面不会收到, 发件人那边照常显示送到)。

`pins.json`: 见过的同事公钥 (第一次见就记下)。同事的公钥变了 (换了电脑, 或者中央换了钥) → `pop_key_changes()`
交给群聊在群里提示一句, 本人可在成员栏核对指纹。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from . import wiki_files

V = 2
ALG = "x25519-hkdf-chacha20poly1305"
LOOKUP_TTL = 300          # 同事公钥查一次缓存 5 分钟
KEYS_PATH = "/api/room-link/keys"
CENTRAL_TOO_OLD_KEYS = "中央端版本太旧, 还不支持加密传输 —— 请管理员把中央端升级到最新版"

_lock = threading.RLock()
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_registered: dict[str, str] = {}   # 网关地址 → 已登记的公钥 (这个进程里)
# 登记单独一把锁: 启动后表单 / 派任务 / 群聊同时第一次取邮筒, 不加锁会各登记一次 (11/7 数据外发记录里同一秒 3 条)。
# 不用 _lock: 登记要等网络, 别让查公钥 / 读本机密钥跟着等。
_register_lock = threading.Lock()
_changes: list[dict[str, str]] = []


class NoKeyError(ValueError):
    """收件人还没有公钥: 拦住不发 (11/6 定)。"""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keys_dir() -> Path:
    d = wiki_files._catfish_home() / "collab_keys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_private(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def device() -> dict[str, str]:
    """本机的设备号 + 密钥对 (第一次用时生成)。"""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: PLC0415
    with _lock:
        p = keys_dir() / "device.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("sk") and d.get("pk") and d.get("device_id"):
                    return d
            except ValueError:
                pass
        sk = X25519PrivateKey.generate()
        raw = lambda k, enc: k.private_bytes(enc, serialization.PrivateFormat.Raw, serialization.NoEncryption())  # noqa: E731
        d = {"device_id": f"dev_{secrets.token_hex(8)}",
             "sk": _b64(raw(sk, serialization.Encoding.Raw)),
             "pk": _b64(sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
             "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        _write_private(p, d)
        return d


def fingerprint(pk: str) -> str:
    """给人核对的指纹: 公钥 SHA-256 前 8 组。"""
    import hashlib  # noqa: PLC0415
    h = hashlib.sha256(_unb64(pk)).hexdigest()[:32]
    return " ".join(h[i:i + 4] for i in range(0, 32, 4))


# ── 中央公钥目录 ─────────────────────────────────────────────────

def _call(method: str, path: str, **kw: Any) -> tuple[int, Any]:
    from . import forms_net  # noqa: PLC0415
    return forms_net._client_call(method, path, **kw)


def ensure_registered() -> None:
    """把本机公钥登记到中央 (每个进程、每个中央地址登记一次)。升级后第一次取邮筒就会走到这里。"""
    from . import skill_publish  # noqa: PLC0415
    d = device()
    gw = skill_publish.GATEWAY_URL
    if _registered.get(gw) == d["pk"]:
        return
    with _register_lock:
        if _registered.get(gw) == d["pk"]:   # 等锁时别的线程已经登记好了
            return
        code, data = _call("PUT", KEYS_PATH, body={"device_id": d["device_id"], "pubkey": d["pk"]})
        if code == 404 or code == 405:
            raise ValueError(CENTRAL_TOO_OLD_KEYS)
        if code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            raise ValueError(f"登记加密公钥失败 ({code}): {detail}")
        _registered[gw] = d["pk"]


def lookup(emails: list[str], *, fresh: bool = False) -> dict[str, list[dict[str, str]]]:
    """同事们登记的公钥: {email: [{device_id, pubkey}]}; 没登记的人不在结果里。缓存 5 分钟。"""
    want = sorted({e.strip().lower() for e in emails if e})
    now = time.time()
    out, miss = {}, []
    for e in want:
        hit = _cache.get(e)
        if hit and not fresh and now - hit[0] < LOOKUP_TTL:
            out[e] = hit[1]
        else:
            miss.append(e)
    for i in range(0, len(miss), 50):
        part = miss[i:i + 50]
        code, data = _call("GET", KEYS_PATH, params={"subs": ",".join(part)})
        if code == 404 or code == 405:
            raise ValueError(CENTRAL_TOO_OLD_KEYS)
        if code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            raise ValueError(f"查同事的加密公钥失败 ({code}): {detail}")
        got = (data or {}).get("keys") or {} if isinstance(data, dict) else {}
        for e in part:
            ks = [{"device_id": str(k.get("device_id")), "pubkey": str(k.get("pubkey"))}
                  for k in got.get(e) or [] if k.get("pubkey") and k.get("device_id")]
            if ks:   # 只缓存查到的: 同事刚升级登记了, 下一封就能发, 不用等缓存过期
                _cache[e] = (now, ks)
                out[e] = ks
                _pin(e, ks)
    return out


def _pins_path() -> Path:
    return keys_dir() / "pins.json"


def _pins() -> dict[str, dict[str, str]]:
    try:
        return json.loads(_pins_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pin(email: str, keys: list[dict[str, str]]) -> None:
    """第一次见到的公钥记下; 已经记过这个人、又冒出新公钥 → 记一条「密钥变了」给群聊提示。"""
    with _lock:
        pins = _pins()
        seen = pins.get(email) or {}
        new = [k["pubkey"] for k in keys if k["pubkey"] not in seen]
        if not new:
            return
        if seen:
            for pk in new:
                _changes.append({"email": email, "fingerprint": fingerprint(pk)})
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        pins[email] = {**seen, **{pk: now for pk in new}}
        _write_private(_pins_path(), pins)


def pop_key_changes() -> list[dict[str, str]]:
    """同事公钥变了的记录 (取走即清): [{email, fingerprint}]。"""
    with _lock:
        out = list(_changes)
        _changes.clear()
        return out


# ── 加密 / 解密 ──────────────────────────────────────────────────

def _aad(kind: str, sender: str, to: str) -> bytes:
    return f"catfish-mailbox-v{V}|{kind}|{sender.lower()}|{to.lower()}".encode()


def _wrap_key(my_sk: str, their_pk: str, sender_dev: str, recipient_dev: str) -> bytes:
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey  # noqa: PLC0415
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: PLC0415
    shared = X25519PrivateKey.from_private_bytes(_unb64(my_sk)).exchange(X25519PublicKey.from_public_bytes(_unb64(their_pk)))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f"catfish-mailbox-wrap|{sender_dev}|{recipient_dev}".encode()).derive(shared)


def seal(to: str, kind: str, sender: str, envelope: dict[str, Any]) -> str:
    """给 to 的每台电脑加密这封信。to 还没有公钥 → NoKeyError (拦住不发)。"""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: PLC0415
    ensure_registered()
    keys = lookup([to]).get(to.lower()) or []
    if not keys:
        raise NoKeyError(f"{to} 还没升级到能加密的鲶鱼版本 (或升级后还没打开过), 暂时发不了 —— 请他升级并打开一次鲶鱼")
    d = device()
    aad = _aad(kind, sender, to)
    k, n = ChaCha20Poly1305.generate_key(), secrets.token_bytes(12)
    body = zlib.compress(json.dumps(envelope, ensure_ascii=False).encode("utf-8"), 6)
    wraps = []
    for key in keys:
        wn = secrets.token_bytes(12)
        wk = _wrap_key(d["sk"], key["pubkey"], d["device_id"], key["device_id"])
        wraps.append({"d": key["device_id"], "n": _b64(wn), "w": _b64(ChaCha20Poly1305(wk).encrypt(wn, k, aad))})
    return json.dumps({"v": V, "alg": ALG, "sd": d["device_id"], "spk": d["pk"], "z": 1,
                       "n": _b64(n), "c": _b64(ChaCha20Poly1305(k).encrypt(n, body, aad)), "k": wraps},
                      separators=(",", ":"))


def is_sealed(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("v") == V and "c" in obj and "k" in obj


def open_sealed(obj: dict[str, Any], kind: str, sender: str, me: str) -> dict[str, Any]:
    """解开发给我的一封。解不开 / 发件人公钥对不上 → ValueError (这封丢掉)。"""
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: PLC0415
    d = device()
    mine = next((w for w in obj.get("k") or [] if isinstance(w, dict) and w.get("d") == d["device_id"]), None)
    if not mine:
        raise ValueError("这封不是加密给这台电脑的 (发件时你这台还没登记公钥)")
    spk, sdev = str(obj.get("spk") or ""), str(obj.get("sd") or "")
    _check_sender(sender, spk)
    aad = _aad(kind, sender, me)
    try:
        k = ChaCha20Poly1305(_wrap_key(d["sk"], spk, sdev, d["device_id"])).decrypt(_unb64(mine["n"]), _unb64(mine["w"]), aad)
        body = ChaCha20Poly1305(k).decrypt(_unb64(obj["n"]), _unb64(obj["c"]), aad)
    except (InvalidTag, KeyError, ValueError, TypeError):
        raise ValueError("解密失败 (内容被改过, 或不是发给你的)") from None
    if obj.get("z"):
        body = zlib.decompress(body)
    env = json.loads(body.decode("utf-8"))
    if not isinstance(env, dict):
        raise ValueError("解出来不是信封")
    return env


def _check_sender(sender: str, spk: str) -> None:
    """发件人用的公钥必须是他登记过的 (中央目录) 或本机记下过的 —— 别人冒充不了他。"""
    pins = _pins().get(sender.lower()) or {}
    if spk in pins:
        return
    try:
        keys = lookup([sender], fresh=True).get(sender.lower()) or []
    except ValueError:
        keys = []
    if spk not in {k["pubkey"] for k in keys}:
        raise ValueError(f"发件人 {sender} 的公钥对不上, 这封不收")
