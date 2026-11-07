"""同事群聊 · 发大文件 (11/07)。一条消息里的文件合计超过 5MB 走这里; 5MB 以内照旧走邮筒分块 (colleague_rooms_files.py)。

为什么另走一条: 邮筒是「给每个人各投一份、收件人一次取完」—— 10 人的群发 5MB, 中央就暂存 50MB, 离线久的人上线一口气全拿。
大文件 (单个最多 200MB) 改成**中央只存一份密文, 大家各自来取, 都取完就删** (中央 room_link_blob_router.py, 迁移 021):

  发件人:  随机 32 字节密钥 K, 文件按 1MB 一块用 ChaCha20-Poly1305 加密 (nonce = 块号, 附加数据 = 文件编号),
           密文先存本机 ~/.catfish/collab_rooms/blobs/<文件编号>.bin (自己能打开、断了能续传),
           中央建一个暂存 (收件人 = 发送那一刻已加入的人) → 群消息照常发 (端到端加密), 文件清单里带
           {blob: {id, key=K}} → 后台一块块传上去, 传完 finish。
  收件人:  收到消息记一行 (等着), 后台看中央传完了没; 传完就一块块下到本机 .bin, 解一遍核对 sha256, 对了就报 taken;
           大家都 taken 了中央删内容, 不然 7 天到期删。
  打开:    点「打开」才解密写到 ~/Downloads/鲶鱼群文件/<群名>/ (员工自己点的)。

中央看到的只有密文和「谁发的、给谁、多大、几块」; K 只在端到端加密的那封群消息里。本机的 .bin 也是密文,
K 存在本机群聊库里 (库整库加密, 见 colleague_rooms_crypt.py)。界面从 Companion 传文件进来先「暂存」(stage):
1MB 一段写到 ~/.catfish/collab_rooms/staging/, 发送时再读 (RPC 一行最多 16MB, 大文件不能一口气传)。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import colleague_rooms_store as store

CHUNK = 1024 * 1024
TAG = 16
MAX_FILE = 200 * 1024 * 1024       # 单个文件上限 (跟中央默认一致)
MAX_TOTAL = 200 * 1024 * 1024      # 一条消息合计
STAGE_MAX = MAX_FILE
STAGE_KEEP = 24 * 3600             # 暂存一天没发就清掉
BLOBS_PATH = "/api/room-link/blobs"
CENTRAL_TOO_OLD_BLOB = "中央端版本太旧, 还不支持群里发大文件 (超过 5MB) —— 请管理员把中央端升级到最新版; 5MB 以内的照常能发"
_STAGE_ID = re.compile(r"s_[0-9a-f]{16}")
_BLOB_ID = re.compile(r"b_[0-9a-f]{32}")
_FILE_ID = re.compile(r"f_[0-9a-f]{16}")

# bigfiles 表 (colleague_rooms_store._SCHEMA) 的 state:
#         uploading (我发的, 正往中央传) / sent (传完了) / local (群里没别人, 只存本机)
#         waiting (别人发的, 等他传完) / downloading / ready (收齐核对过) / gone (中央已经删了) / failed

_worker_lock = threading.Lock()
_working = False


def _db():
    """本机群聊库 (表 bigfiles 在 colleague_rooms_store._SCHEMA 里)。"""
    return store.tx()


def _dir(name: str) -> Path:
    d = store.rooms_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def blob_path(file_id: str) -> Path:
    return _dir("blobs") / f"{file_id}.bin"


# ── 暂存 (界面一段段传进来) ──────────────────────────────────────

def stage(params: dict[str, Any]) -> dict[str, Any]:
    """{stage_id?, offset, data(base64)} → {stage_id, size}。offset 必须接在已有的后面 (断了从 size 续)。"""
    sid = str(params.get("stage_id") or "") or f"s_{secrets.token_hex(8)}"
    if not _STAGE_ID.fullmatch(sid):
        raise ValueError("暂存编号不对")
    p = _dir("staging") / sid
    have = p.stat().st_size if p.exists() else 0
    offset = int(params.get("offset") or 0)
    if offset != have:
        return {"ok": False, "stage_id": sid, "size": have}   # 界面从 size 接着传
    try:
        data = base64.b64decode(str(params.get("data") or ""), validate=True)
    except ValueError:
        raise ValueError("这段文件内容读不出来") from None
    if have + len(data) > STAGE_MAX:
        raise ValueError(f"文件太大 (单个最多 {MAX_FILE // 1024 // 1024}MB) —— 更大的用网盘")
    with open(p, "ab") as f:
        f.write(data)
    return {"ok": True, "stage_id": sid, "size": have + len(data)}


def staged(sid: str) -> Path:
    if not _STAGE_ID.fullmatch(str(sid or "")):
        raise ValueError("暂存编号不对")
    p = _dir("staging") / sid
    if not p.exists():
        raise ValueError("文件没传完 (或者放太久被清掉了), 重新选一下")
    return p


# ── 发 ───────────────────────────────────────────────────────────

def _nonce(i: int) -> bytes:
    return i.to_bytes(12, "big")


def encrypt_to_local(src: Path, file_id: str) -> dict[str, Any]:
    """明文 → 本机密文 .bin; 返回 {size, sha256, n, key}。"""
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: PLC0415
    key = ChaCha20Poly1305.generate_key()
    aead, h, n, size = ChaCha20Poly1305(key), hashlib.sha256(), 0, 0
    dst = blob_path(file_id)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while block := fi.read(CHUNK):
            h.update(block)
            size += len(block)
            fo.write(aead.encrypt(_nonce(n), block, file_id.encode()))
            n += 1
    if not n:
        dst.unlink(missing_ok=True)
        raise ValueError("文件是空的")
    os.chmod(dst, 0o600)
    return {"size": size, "sha256": h.hexdigest(), "n": n, "key": base64.urlsafe_b64encode(key).decode()}


def _ct_size(size: int, n: int) -> int:
    return size + TAG * n


def _http(method: str, path: str, *, json: Any = None, content: bytes | None = None) -> tuple[int, Any]:
    """跟中央打交道 (带员工登录令牌; 外发记录用途「群聊 · 发文件」)。连不上 → ValueError。"""
    import httpx  # noqa: PLC0415
    from . import skill_publish  # noqa: PLC0415
    token = skill_publish._read_id_token()
    if not token:
        raise ValueError("还没登录鲶鱼账号 (Companion 里先登录)")
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.request(method, f"{skill_publish.GATEWAY_URL}{path}", json=json, content=content,
                          headers={"Authorization": f"Bearer {token}", "X-Catfish-Source": "room-file",
                                   **({"Content-Type": "application/octet-stream"} if content is not None else {})})
    except httpx.RequestError as e:
        raise ValueError(f"连不上中央服务: {e}") from None
    if r.headers.get("content-type", "").startswith("application/octet-stream"):
        return r.status_code, r.content
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"detail": r.text[:300]}


def _detail(data: Any) -> str:
    return str(data.get("detail") if isinstance(data, dict) else data)[:200]


def prepare_send(room_id: str, msg_id: str, me: str, files: list[dict[str, Any]], to: list[str], at: str) -> list[dict[str, Any]]:
    """发送前: 每个文件加密到本机、在中央建好暂存 (建不了就整条不发, 报人话)。返回消息里带的清单。
    files: [{name, path(暂存的明文)}]。"""
    out = []
    for f in files:
        fid = f"f_{secrets.token_hex(8)}"
        info = encrypt_to_local(Path(f["path"]), fid)
        blob_id = None
        if to:
            code, data = _http("POST", BLOBS_PATH, json={"recipients": to, "size": _ct_size(info["size"], info["n"]),
                                                          "n": info["n"]})
            if code in (404, 405):
                blob_path(fid).unlink(missing_ok=True)
                raise ValueError(CENTRAL_TOO_OLD_BLOB)
            if code >= 400:
                blob_path(fid).unlink(missing_ok=True)
                raise ValueError(f"中央暂存文件失败 ({code}): {_detail(data)}")
            blob_id = data["id"]
        with _db() as con:
            con.execute("INSERT INTO bigfiles (room_id, file_id, msg_id, sender, mine, name, size, sha256, n, key, blob_id, state, at)"
                        " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (room_id, fid, msg_id, me, f["name"], info["size"], info["sha256"], info["n"], info["key"], blob_id,
                         "uploading" if blob_id else "local", at))
        out.append({"id": fid, "name": f["name"], "size": info["size"], "sha256": info["sha256"], "n": info["n"],
                    **({"blob": {"id": blob_id, "key": info["key"]}} if blob_id else {})})
        try:
            Path(f["path"]).unlink()
        except OSError:
            pass
    return out


# ── 收 ───────────────────────────────────────────────────────────

def clean_meta(f: dict[str, Any]) -> dict[str, Any] | None:
    """收到的清单里一个大文件 (带 blob) 的核对。不合规 → None。"""
    b = f.get("blob") if isinstance(f.get("blob"), dict) else None
    if not b or not _BLOB_ID.fullmatch(str(b.get("id") or "")):
        return None
    try:
        key = base64.urlsafe_b64decode(str(b.get("key") or ""))
        size, n = int(f.get("size") or 0), int(f.get("n") or 0)
    except (ValueError, TypeError):
        return None
    if len(key) != 32 or not 0 < size <= MAX_FILE or n != max(1, -(-size // CHUNK)):
        return None
    return {"blob": {"id": b["id"], "key": b["key"]}}


def register_incoming(room_id: str, msg: dict[str, Any]) -> bool:
    """别人发来的消息里有大文件: 各记一行「等着」。返回有没有。"""
    any_big = False
    with _db() as con:
        for f in msg.get("files") or []:
            b = f.get("blob")
            if not b:
                continue
            any_big = True
            con.execute("INSERT OR IGNORE INTO bigfiles (room_id, file_id, msg_id, sender, mine, name, size, sha256, n, key, blob_id, state, at)"
                        " VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, 'waiting', ?)",
                        (room_id, f["id"], msg["msg_id"], msg.get("from") or "", f["name"], f["size"], f["sha256"], f["n"],
                         b["key"], b["id"], msg.get("at") or ""))
    return any_big


# ── 后台: 传 / 下 ────────────────────────────────────────────────

def _due(limit: int = 4) -> list[dict[str, Any]]:
    with _db() as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM bigfiles WHERE state IN ('uploading', 'waiting', 'downloading') AND next_at <= ? ORDER BY at LIMIT ?",
            (time.time(), limit)).fetchall()]


def _set(room_id: str, file_id: str, **kw: Any) -> None:
    cols = ", ".join(f"{k} = ?" for k in kw)
    with _db() as con:
        con.execute(f"UPDATE bigfiles SET {cols} WHERE room_id = ? AND file_id = ?", (*kw.values(), room_id, file_id))


def _later(r: dict[str, Any], err: str, wait: float = 60) -> None:
    """网络问题: 过一会再来 (越来越慢, 最多 30 分钟一次)。"""
    tries = r["tries"] + 1
    _set(r["room_id"], r["file_id"], tries=tries, error=err[:200], next_at=time.time() + min(1800, wait * tries))


def _upload(r: dict[str, Any]) -> None:
    path, bid = blob_path(r["file_id"]), r["blob_id"]
    if not path.exists():
        _set(r["room_id"], r["file_id"], state="failed", error="本机的文件副本不见了")
        return
    sizes = [CHUNK + TAG] * (r["n"] - 1) + [_ct_size(r["size"], r["n"]) - (CHUNK + TAG) * (r["n"] - 1)]
    with open(path, "rb") as f:
        f.seek(sum(sizes[:r["got"]]))
        for i in range(r["got"], r["n"]):
            data = f.read(sizes[i])
            try:
                code, body = _http("PUT", f"{BLOBS_PATH}/{bid}/chunks/{i}", content=data)
            except ValueError as e:
                _later(r, str(e))
                return
            if code in (404, 409, 410):
                _set(r["room_id"], r["file_id"], state="failed", error=f"中央不收了 ({code}): {_detail(body)}")
                return
            if code >= 400:
                _later(r, f"{code}: {_detail(body)}")
                return
            _set(r["room_id"], r["file_id"], got=i + 1, tries=0, error=None)
    try:
        code, body = _http("POST", f"{BLOBS_PATH}/{bid}/finish")
    except ValueError as e:
        _later(r, str(e))
        return
    if code >= 400:
        _later(r, f"{code}: {_detail(body)}")
        return
    _set(r["room_id"], r["file_id"], state="sent", error=None)


def _download(r: dict[str, Any]) -> None:
    bid = r["blob_id"]
    try:
        code, info = _http("GET", f"{BLOBS_PATH}/{bid}")
    except ValueError as e:
        _later(r, str(e))
        return
    if code == 404 or (code == 200 and info.get("purged")):
        _set(r["room_id"], r["file_id"], state="gone", error="中央已经删了 (超过 7 天没取, 或发件人那边出了问题)")
        return
    if code >= 400:
        _later(r, f"{code}: {_detail(info)}")
        return
    if not info.get("complete"):
        _set(r["room_id"], r["file_id"], state="waiting", next_at=time.time() + 30)   # 发件人还在传
        return
    part = blob_path(r["file_id"]).with_suffix(".part")
    got = r["got"] if part.exists() else 0
    _set(r["room_id"], r["file_id"], state="downloading", got=got)
    with open(part, "ab" if got else "wb") as f:
        for i in range(got, r["n"]):
            try:
                code, data = _http("GET", f"{BLOBS_PATH}/{bid}/chunks/{i}")
            except ValueError as e:
                _later(r, str(e))
                return
            if code == 410:
                _set(r["room_id"], r["file_id"], state="gone", error="中央已经删了")
                return
            if code >= 400 or not isinstance(data, bytes):
                _later(r, f"{code}: {_detail(data)}")
                return
            f.write(data)
            f.flush()
            _set(r["room_id"], r["file_id"], got=i + 1, tries=0, error=None)
    if _sha_of(part, r) != r["sha256"]:
        part.unlink(missing_ok=True)
        if r["tries"] >= 2:
            _set(r["room_id"], r["file_id"], state="failed", got=0, error="收下来的内容核对不上 (可能被改过)")
        else:
            _later(r, "收下来的内容核对不上, 重新下", 5)
            _set(r["room_id"], r["file_id"], got=0)
        return
    part.replace(blob_path(r["file_id"]))
    _set(r["room_id"], r["file_id"], state="ready", error=None)
    try:
        _http("POST", f"{BLOBS_PATH}/{bid}/taken")   # 报不上也没事: 中央 7 天到期照样删
    except ValueError:
        pass


def _decrypt_iter(path: Path, r: dict[str, Any]):
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305  # noqa: PLC0415
    aead = ChaCha20Poly1305(base64.urlsafe_b64decode(r["key"]))
    total = _ct_size(r["size"], r["n"])
    with open(path, "rb") as f:
        for i in range(r["n"]):
            want = min(CHUNK + TAG, total - i * (CHUNK + TAG))
            block = f.read(want)
            try:
                yield aead.decrypt(_nonce(i), block, r["file_id"].encode())
            except InvalidTag:
                raise ValueError("解密失败") from None


def _sha_of(path: Path, r: dict[str, Any]) -> str:
    h = hashlib.sha256()
    try:
        for block in _decrypt_iter(path, r):
            h.update(block)
    except ValueError:
        return ""
    return h.hexdigest()


def work() -> None:
    """后台传 / 下一轮 (一次最多几个文件)。"""
    for r in _due():
        try:
            if r["mine"] and r["state"] == "uploading":
                _upload(r)
            elif not r["mine"]:
                _download(r)
        except Exception as e:  # noqa: BLE001  一个文件出错不连累别的
            _later(r, f"{type(e).__name__}: {e}")


def kick() -> None:
    """有活就起一个后台线程干完 (同时只跑一个)。"""
    global _working
    with _worker_lock:
        if _working:
            return
        _working = True

    def run() -> None:
        global _working
        try:
            for _ in range(200):
                if not _due(1):
                    break
                work()
        finally:
            with _worker_lock:
                _working = False

    threading.Thread(target=run, name="room-bigfiles", daemon=True).start()


def pending() -> bool:
    return bool(_due(1))


# ── 给界面看 / 打开 / 清理 ───────────────────────────────────────

def status(room_id: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    with _db() as con:
        rows = con.execute(f"SELECT file_id, mine, state, got, n, error FROM bigfiles WHERE room_id = ? AND file_id IN ({q})",
                           (room_id, *ids)).fetchall()
    out = {}
    for r in rows:
        have = r["state"] in ("ready", "sent", "local", "uploading")
        out[r["file_id"]] = {"have": have, "got": r["got"], "big": r["state"], **({"error": r["error"]} if r["error"] else {})}
    return out


def retry(room_id: str, file_id: str) -> dict[str, Any]:
    """界面「重试」: 失败的从头来 (中央删了的没法重试)。"""
    with _db() as con:
        con.execute("UPDATE bigfiles SET state = CASE mine WHEN 1 THEN 'uploading' ELSE 'waiting' END, tries = 0, next_at = 0, error = NULL"
                    " WHERE room_id = ? AND file_id = ? AND state = 'failed'", (room_id, file_id))
    kick()
    return {"ok": True}


def row(room_id: str, file_id: str) -> dict[str, Any] | None:
    with _db() as con:
        r = con.execute("SELECT * FROM bigfiles WHERE room_id = ? AND file_id = ?", (room_id, file_id)).fetchone()
    return dict(r) if r else None


def write_plain(r: dict[str, Any], target: Path) -> None:
    tmp = target.with_suffix(target.suffix + ".part")
    h = hashlib.sha256()
    with open(tmp, "wb") as f:
        for block in _decrypt_iter(blob_path(r["file_id"]), r):
            h.update(block)
            f.write(block)
    if h.hexdigest() != r["sha256"]:
        tmp.unlink(missing_ok=True)
        raise ValueError("本机副本核对不上, 打不开")
    tmp.replace(target)


def drop_orphans() -> None:
    """消息删了 (保留期到了 / 删了群) 的大文件连本机副本一起删; 暂存放了一天没发的删。"""
    with _db() as con:
        gone = con.execute("SELECT room_id, file_id FROM bigfiles b WHERE NOT EXISTS "
                           "(SELECT 1 FROM messages m WHERE m.room_id = b.room_id AND m.msg_id = b.msg_id)").fetchall()
        for g in gone:
            con.execute("DELETE FROM bigfiles WHERE room_id = ? AND file_id = ?", (g["room_id"], g["file_id"]))
        keep = {r["file_id"] for r in con.execute("SELECT file_id FROM bigfiles").fetchall()}
    for p in _dir("blobs").glob("f_*"):
        if p.name.split(".")[0] not in keep:
            p.unlink(missing_ok=True)
    now = time.time()
    for p in _dir("staging").glob("s_*"):
        try:
            if now - p.stat().st_mtime > STAGE_KEEP:
                p.unlink()
        except OSError:
            pass
