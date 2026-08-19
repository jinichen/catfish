"""向 Companion 要密码这条通道的单测。

**用真 socket 跑**, 不 mock。这条路的关键是线上的字节形状 (NDJSON + JSON-RPC),
mock 掉 socket 就等于只测了自己脑子里的协议 —— 8/18 前端那个 parser 就是这么
挂的: 测了"对象"和"以 { 开头的字符串"两种, 线上真正走的第三种一次没测。

Linux 上 AF_UNIX 一样能跑, 所以这些测试在沙箱 / CI 里都是真的在连。
"""
from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from catfish_tool_bridge import companion_secrets, secret_resolver

SERVICE = "catfish-teaching:neis.ffcs.cn"
#: 假密码。测"不许泄漏"那几条时拿它当探针。
PROBE = "hunter2-DO-NOT-LEAK"


@pytest.fixture
def darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 handles() 的平台判断按成 macOS —— 沙箱 / CI 是 Linux。"""
    monkeypatch.setattr(companion_secrets.platform, "system", lambda: "Darwin")


@contextmanager
def fake_companion(sock_path: Path, responder):
    """起一个假 Companion。`responder(request_bytes) -> bytes | None`。

    返回一个 list, 跑完之后里面是服务端**实际收到**的字节 —— 请求形状也要验,
    不能只验响应解析。
    """
    seen: list[bytes] = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)

    def serve() -> None:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            seen.append(buf)
            out = responder(buf)
            if out is not None:
                conn.sendall(out)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        yield seen
    finally:
        srv.close()
        t.join(timeout=3)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """HOME 指到一个**短**临时目录 —— 顺带把 socket_path() 自己也测了。

    HOME 而不是 CATFISH_HOME: socket 路径照 tool-bridge.sock 那条的规矩走
    `Path.home()`, 因为 Rust 那端只认 $HOME。见 socket_path() 的注释。

    ⚠ 不能用 pytest 的 tmp_path: AF_UNIX 的 sun_path 上限 108 字节 (macOS 104),
      而 tmp_path 里含测试函数名, 这个文件里的名字是中文, 一超就
      `OSError: AF_UNIX path too long` —— 第一次跑就撞了 16 条。
    """
    d = Path(tempfile.mkdtemp(prefix="cf-", dir="/tmp"))
    (d / ".catfish").mkdir()
    monkeypatch.setenv("HOME", str(d))
    try:
        yield d / ".catfish"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def ok_response(pwd: str = PROBE) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"password": pwd}}) + "\n").encode()


# ─────────────────────────── 分流 ───────────────────────────


def test_handles_只认自己的命名空间(darwin: None) -> None:
    assert companion_secrets.handles(SERVICE)
    assert companion_secrets.handles("catfish-teaching:EIS")
    # 4/28 手工建的那条 —— ACL 里就是 security, 一直读得到, 不该改道
    assert not companion_secrets.handles("eis_password")
    assert not companion_secrets.handles("")
    # 前缀要在**开头**, 不是含有
    assert not companion_secrets.handles("evil:catfish-teaching:x")


def test_非_macOS_一律不走这条(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows 凭据管理器没有按程序的 ACL, 没有要绕的东西 (见文件头)
    for sysname in ["Windows", "Linux"]:
        monkeypatch.setattr(companion_secrets.platform, "system", lambda s=sysname: s)
        assert not companion_secrets.handles(SERVICE)


def test_fetch_拒绝没过_handles_的_service(darwin: None) -> None:
    with pytest.raises(companion_secrets.CompanionSecretError, match="不该走 Companion"):
        companion_secrets.fetch("eis_password")


# ─────────────────────────── 真连一次 ───────────────────────────


def test_取到密码(darwin: None, home: Path) -> None:
    with fake_companion(home / "companion-secrets.sock", lambda _: ok_response()) as seen:
        assert companion_secrets.fetch(SERVICE) == PROBE

    # 请求形状: 一行 JSON, JSON-RPC 2.0, method / params 都对
    assert seen[0].endswith(b"\n"), "必须是 NDJSON 一行一帧"
    req = json.loads(seen[0])
    assert req["jsonrpc"] == "2.0"
    assert req["method"] == "secret/get"
    assert req["params"] == {"service": SERVICE}


def test_中文_service_不被转义坏(darwin: None, home: Path) -> None:
    svc = "catfish-teaching:教学登录"
    with fake_companion(home / "companion-secrets.sock", lambda _: ok_response()) as seen:
        assert companion_secrets.fetch(svc) == PROBE
    assert json.loads(seen[0])["params"]["service"] == svc


def test_分几个包发来也读得全(darwin: None, home: Path) -> None:
    """recv 一次读不完是常态, 不能假设"一个包 = 一帧"。

    ⚠ 第一版这条测试是**假的**: 服务端在一个循环里连着 sendall 小片, 客户端那边
      内核早就把它们攒成一个了, recv 一次全拿到 —— 于是"把 _read_line 改成第一
      包就返回"这个变异跑绿了 (M5 没抓到)。中间必须真的**等一下**, 让客户端的
      第一次 recv 只能拿到半截, 才是在测那个 while 循环。
    """
    import time

    sock_path = home / "companion-secrets.sock"
    blob = ok_response()
    split = len(blob) // 2
    assert b"\n" not in blob[:split], "前半段不能含换行, 否则测不到续读"

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        with conn:
            while not conn.recv(4096).endswith(b"\n"):
                pass
            conn.sendall(blob[:split])
            time.sleep(0.15)  # ★ 这一下才让上面那半截真的单独到达
            conn.sendall(blob[split:])

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        assert companion_secrets.fetch(SERVICE) == PROBE
    finally:
        srv.close()
        t.join(timeout=3)


# ─────────────────────────── 连不上 ───────────────────────────


def test_socket_文件不在_说_Companion_没在跑(darwin: None, home: Path) -> None:
    with pytest.raises(companion_secrets.CompanionUnavailable) as ei:
        companion_secrets.fetch(SERVICE)
    msg = str(ei.value)
    assert "Companion" in msg
    assert "socket 文件不存在" in msg
    # 要告诉员工**做什么**, 不能只报一个错
    assert "打开 Companion" in msg


def test_死文件_也归到_没在跑(darwin: None, home: Path) -> None:
    # 上次崩溃留下的 socket 文件: 存在, 但没人 listen
    stale = home / "companion-secrets.sock"
    stale.write_text("")
    with pytest.raises(companion_secrets.CompanionUnavailable) as ei:
        companion_secrets.fetch(SERVICE)
    assert "打开 Companion" in str(ei.value)


def test_连上了但对方一句话不说(darwin: None, home: Path) -> None:
    with fake_companion(home / "companion-secrets.sock", lambda _: None):
        with pytest.raises(companion_secrets.CompanionSecretError) as ei:
            companion_secrets.fetch(SERVICE)
    # 这不是"没在跑" —— 连上了, 是对端有问题。两者员工的动作不一样。
    assert not isinstance(ei.value, companion_secrets.CompanionUnavailable)


# ─────────────────────────── 对端报错 ───────────────────────────


def test_钥匙串里没有(darwin: None, home: Path) -> None:
    resp = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32001, "message": "钥匙串里没有这一条"},
            }
        )
        + "\n"
    ).encode()
    with fake_companion(home / "companion-secrets.sock", lambda _: resp):
        with pytest.raises(companion_secrets.CompanionSecretError, match="钥匙串里没有这一条"):
            companion_secrets.fetch(SERVICE)


def test_空密码当失败(darwin: None, home: Path) -> None:
    # 拿空串去 page.fill 只会让登录失败得更难查
    with fake_companion(home / "companion-secrets.sock", lambda _: ok_response("")):
        with pytest.raises(companion_secrets.CompanionSecretError, match="密码是空的"):
            companion_secrets.fetch(SERVICE)


@pytest.mark.parametrize(
    "body",
    [
        '{"jsonrpc":"2.0","id":1}',                       # 没 result
        '{"jsonrpc":"2.0","id":1,"result":"一个字符串"}',   # result 不是对象
        '{"jsonrpc":"2.0","id":1,"result":{}}',           # 没 password
        '{"jsonrpc":"2.0","id":1,"result":{"password":42}}',  # password 不是字符串
        "[1,2,3]",                                        # 顶层不是对象
    ],
)
def test_形状不对一律报错_不返半个值(darwin: None, home: Path, body: str) -> None:
    with fake_companion(home / "companion-secrets.sock", lambda _: (body + "\n").encode()):
        with pytest.raises(companion_secrets.CompanionSecretError):
            companion_secrets.fetch(SERVICE)


# ─────────────────────────── ★★★ 不许泄漏 ───────────────────────────
#
# 这条通道上跑的每一个字节里都可能有密码。异常消息会进 tool 返回值 → SSE →
# 模型上下文 → hermes state.db, 撤销不了。
#
# tool_bridge_rpc.rs:117 那条反方向的通道就是 `format!("响应非合法 JSON: {e}\n
# 原文: {line}")` —— 在那条上无所谓 (跑的是工具结果), 照抄到这条上就是泄漏。


def test_响应解析不了时_原文不进异常(darwin: None, home: Path) -> None:
    # 半截 JSON, 但里面已经有密码了 —— 正是"对端写到一半崩了"的形状
    broken = ('{"jsonrpc":"2.0","result":{"password":"' + PROBE + '"').encode() + b"\n"
    with fake_companion(home / "companion-secrets.sock", lambda _: broken):
        with pytest.raises(companion_secrets.CompanionSecretError) as ei:
            companion_secrets.fetch(SERVICE)
    assert PROBE not in str(ei.value)
    assert PROBE not in repr(ei.value)


def test_不是_utf8_也不带原文(darwin: None, home: Path) -> None:
    with fake_companion(home / "companion-secrets.sock", lambda _: b"\xff\xfe" + PROBE.encode() + b"\n"):
        with pytest.raises(companion_secrets.CompanionSecretError) as ei:
            companion_secrets.fetch(SERVICE)
    assert PROBE not in str(ei.value)


def test_对端把密码塞进_error_message_也不放大(darwin: None, home: Path) -> None:
    # 对端不该这么干, 但真这么干了, 我们至少不该再复述一遍到别处。
    # 这条测的是**我们的**行为边界: error.message 会原样带出去 (它是给员工看的),
    # 所以对端那侧必须自己保证不塞密码 —— socket.rs 里有对应的一条。
    # 这里只钉死"我们不会额外再拼一份 raw 进去"。
    resp = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "坏了"}})
        + "\n"
    ).encode()
    with fake_companion(home / "companion-secrets.sock", lambda _: resp):
        with pytest.raises(companion_secrets.CompanionSecretError) as ei:
            companion_secrets.fetch(SERVICE)
    assert str(ei.value).count("坏了") == 1


# ─────────────────────────── 接进 secret_resolver ───────────────────────────


def test_resolver_把_catfish_teaching_转给_Companion(darwin: None, home: Path) -> None:
    with fake_companion(home / "companion-secrets.sock", lambda _: ok_response()):
        assert secret_resolver.resolve_secret(f"keychain://{SERVICE}") == PROBE


def test_resolver_连不上时报的是_Companion_那句话(darwin: None, home: Path) -> None:
    # ★ 不能 fallback 回 security —— fallback 会在 ACL 还留着 security 的老条目上
    #   碰巧成功, 于是没人发现这条通道断了 (见 _resolve_keychain 里的注释)。
    with pytest.raises(secret_resolver.SecretResolveError) as ei:
        secret_resolver.resolve_secret(f"keychain://{SERVICE}")
    assert "打开 Companion" in str(ei.value)
    assert "add-generic-password" not in str(ei.value), "这是走了 security 那条老路"


# ─────────── ★★★ 连不上 ≠ 没存过 (8/19 实撞, 半小时) ───────────
#
# 那天 tool-bridge 是旧进程, 走 security 撞上员工看不见的授权框卡 5 秒超时。
# 工具照样返 needs_credential → 界面弹密码框 → 鸿波删了重存三次 → 每次都存成功,
# 每次都读不出来。界面从头到尾没有一处说得出"存没用"。
#
# 判据宽了一格 (把"取不出来"一律当成"该再问一次"), 代价是一个人在那儿转了半小时。


def test_连不上时_ask_employee_是_False(darwin: None, home: Path) -> None:
    with pytest.raises(secret_resolver.SecretResolveError) as ei:
        secret_resolver.resolve_secret(f"keychain://{SERVICE}")
    assert ei.value.ask_employee is False, "连不上还弹密码框 = 让人白输密码"


def test_钥匙串里没有时_ask_employee_是_True(darwin: None, home: Path) -> None:
    # 这种才是"再存一次就好"
    resp = (
        json.dumps({"jsonrpc": "2.0", "id": 1,
                    "error": {"code": -32001, "message": "本机钥匙串里没有这一条"}})
        + "\n"
    ).encode()
    with fake_companion(home / "companion-secrets.sock", lambda _: resp):
        with pytest.raises(secret_resolver.SecretResolveError) as ei:
            secret_resolver.resolve_secret(f"keychain://{SERVICE}")
    assert ei.value.ask_employee is True


def test_默认是_True_不要因为加了字段就把老路堵上(darwin: None) -> None:
    # env:// / 不认识的 scheme 这些老分支没传 ask_employee, 必须还是"可以问"
    for ref in ["env://NO_SUCH_VAR_XYZ", "怪://x", "env://"]:
        with pytest.raises(secret_resolver.SecretResolveError) as ei:
            secret_resolver.resolve_secret(ref)
        assert ei.value.ask_employee is True, ref


def _result_for(exc: Exception) -> dict:
    """跑**生产的**那个函数, 不在测试里另写一套判据。"""
    from catfish_tool_bridge.catfish_tools_browser_actions import _unreadable_result

    return _unreadable_result(
        exc=exc,
        site="neis.ffcs.cn",
        found=f"keychain://{SERVICE}",
        page_url="http://neis.ffcs.cn/cas/login",
        page_title="登录页",
        selector="#pwd",
    )


def test_通道断了_不弹密码框(darwin: None, home: Path) -> None:
    with pytest.raises(secret_resolver.SecretResolveError) as ei:
        secret_resolver.resolve_secret(f"keychain://{SERVICE}")
    out = _result_for(ei.value)

    assert "needs_credential" not in out, "通道断了还弹框 = 让人一遍遍白输密码"
    assert "reason" not in out
    # 话要说清, 否则模型会自己发明"你手动输一下吧"
    assert "再存一次没有用" in out["summary"]
    assert "不是没存过" in out["summary"]
    assert "打开 Companion" in out["error"]


def test_钥匙串里没有_照旧弹框(darwin: None, home: Path) -> None:
    resp = (
        json.dumps({"jsonrpc": "2.0", "id": 1,
                    "error": {"code": -32001, "message": "本机钥匙串里没有这一条"}})
        + "\n"
    ).encode()
    with fake_companion(home / "companion-secrets.sock", lambda _: resp):
        with pytest.raises(secret_resolver.SecretResolveError) as ei:
            secret_resolver.resolve_secret(f"keychain://{SERVICE}")
    out = _result_for(ei.value)

    assert out["needs_credential"] is True
    assert out["reason"] == "unreadable"   # 不是 missing —— 索引里明明有
    assert "summary" not in out


def test_没有_ask_employee_属性的异常_按可以问处理(darwin: None) -> None:
    # 老代码 / 别的错。多问一次是安全的那一侧。
    for e in [RuntimeError("随便什么错"), ValueError(""), OSError()]:
        assert _result_for(e)["needs_credential"] is True


def test_报错里绝不带密码(darwin: None) -> None:
    # exc 的文本会原样拼进 error 字段 → 进模型上下文。下层任何一处把密码放进
    # 异常消息, 都会从这儿漏出去 —— companion_secrets 那边有对应的几条钉着。
    out = _result_for(secret_resolver.SecretResolveError("取不到"))
    assert PROBE not in json.dumps(out, ensure_ascii=False)


def test_resolver_老的手工条目还是走_security(darwin: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # keychain://eis_password: 4/28 手工建的, 冻结的老 skill 在用, 一个字都不能变
    called: list[list[str]] = []

    class R:
        returncode = 0
        stdout = "old-path-password\n"
        stderr = ""

    def fake_run(cmd, **kw):
        called.append(cmd)
        return R()

    monkeypatch.setattr(secret_resolver.subprocess, "run", fake_run)
    monkeypatch.setattr(secret_resolver.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(secret_resolver.shutil, "which", lambda _: "/usr/bin/security")

    assert secret_resolver.resolve_secret("keychain://eis_password") == "old-path-password"
    assert called[0][:2] == ["security", "find-generic-password"]
