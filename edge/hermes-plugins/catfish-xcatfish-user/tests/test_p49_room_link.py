"""P49: 横向协同 RoomLink 的三个红线补丁 —— 不变式测试。

# 三个补丁各堵一个洞, 每个洞的失效方式都是**静默**的

  P49.1 issue_room_grant  A 拿到 approve 权限 → 能批准 B 机器上的 execute_code
  P49.2 run_conversation  B 的回复直接进 run.completed → 本地数据出端无审批
  P49.3 register_notify   B 的工具审批推进 A 在 poll 的 SSE → 审批方反了 +
                          event 里的 command 泄露

三个补丁共用一个判据 `current_room_execution_policy() is not None`。判据宽了
会把员工自己的 run 也截住 (Companion 本人的对话突然没回复); 窄了漏洞照旧。
所以每条测试都要成对: room run 生效 + 非 room run 不动。

# 为什么三个必须一起装

只装 P49.3 不装 P49.1: 审批改道给 B 了, 但 A 的 grant 里有 approve, A 直接
POST /approval 照样批。只装 P49.1 不装 P49.2: A 批不了工具了, 但 B 的回复
还是不经审批出端。install() 因此是原子的, 任一失败整体不装。
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─────────────────────────────────────────────────────────────────────
# 桩: 四个 hermes 模块, 语义照抄上游
# ─────────────────────────────────────────────────────────────────────
class _Policy:
    """RoomExecutionPolicy 的最小替身 —— 判据只看 is not None。"""


@pytest.fixture
def hermes(monkeypatch):
    """装四个桩模块, 返一个能切 room/非 room 的控制器。"""
    state = {"policy": None, "grant_calls": [], "notify_cbs": {}}

    # gateway.hosted_room_execution_policy
    pol = types.ModuleType("gateway.hosted_room_execution_policy")
    pol.current_room_execution_policy = lambda: state["policy"]

    # gateway.hosted_room_peer
    hrp = types.ModuleType("gateway.hosted_room_peer")

    def issue_room_grant(secret, **kw):
        state["grant_calls"].append(dict(kw))
        return "tok"
    hrp.issue_room_grant = issue_room_grant

    # run_agent.AIAgent
    ra = types.ModuleType("run_agent")

    class AIAgent:
        def run_conversation(self, user_message, *a, **kw):
            return {"final_response": f"B 的回复: {user_message}", "usage": {}}
    ra.AIAgent = AIAgent

    # tools.approval
    ap = types.ModuleType("tools.approval")

    def register_gateway_notify(session_key, cb):
        state["notify_cbs"][session_key] = cb
    ap.register_gateway_notify = register_gateway_notify

    gw = types.ModuleType("gateway"); gw.__path__ = []
    tools = types.ModuleType("tools"); tools.__path__ = []
    for name, mod in [
        ("gateway", gw), ("tools", tools), ("run_agent", ra),
        ("gateway.hosted_room_execution_policy", pol),
        ("gateway.hosted_room_peer", hrp),
        ("tools.approval", ap),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)

    # 每个测试拿一份干净模块
    sys.modules.pop("plugin_room_link", None)
    import plugin_room_link as rl
    rl.install()
    yield types.SimpleNamespace(
        rl=rl, state=state, AIAgent=AIAgent,
        room=lambda on: state.__setitem__("policy", _Policy() if on else None),
    )
    rl.uninstall()


# ─────────────────────────────────────────────────────────────────────
# P49.1  grant 权限
# ─────────────────────────────────────────────────────────────────────
def test_邀请时不传permissions_只给dispatch和status(hermes):
    """堵 A 拿到 approve 的洞。上游 _handle_room_member_invitation 就是不传的。"""
    from gateway.hosted_room_peer import issue_room_grant
    issue_room_grant(b"x" * 32, room_id="r", member_id="a")
    assert hermes.state["grant_calls"][-1]["permissions"] == ("dispatch", "status")


def test_显式传了permissions_一个字不改(hermes):
    """刷新路径 (_handle_room_member_grant_refresh) 传 claims["permissions"]
    原样继承 —— 限过一次就一直限着, 不会被刷新放大回去。"""
    from gateway.hosted_room_peer import issue_room_grant
    issue_room_grant(b"x" * 32, room_id="r", member_id="a", permissions=["status"])
    assert hermes.state["grant_calls"][-1]["permissions"] == ["status"]


def test_approve和stop永远不在默认里(hermes):
    """判据的正面: 不只是"少了 approve", 是精确等于这两个。"""
    assert set(hermes.rl.ROOM_LINK_PERMISSIONS) == {"dispatch", "status"}
    assert "approve" not in hermes.rl.ROOM_LINK_PERMISSIONS
    assert "stop" not in hermes.rl.ROOM_LINK_PERMISSIONS


# ─────────────────────────────────────────────────────────────────────
# P49.2  出站审批: run 挂起等 B
# ─────────────────────────────────────────────────────────────────────
import threading


def _run_in_thread(hermes, msg, task_id):
    """在后台线程里跑 room run (它会阻塞等审批), 返 (thread, box)。
    box["result"] 在线程结束后可读。"""
    box = {}

    def _t():
        box["result"] = hermes.AIAgent().run_conversation(msg, task_id=task_id)
    th = threading.Thread(target=_t, daemon=True)
    th.start()
    return th, box


def _wait_pending(hermes, run_id, timeout=3.0):
    """等到 run_id 出现在待审批表里 (线程刚起来时表可能还是空的)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(o["run_id"] == run_id for o in hermes.rl.snapshot_pending()["outputs"]):
            return True
        time.sleep(0.02)
    return False


def test_room_run挂起等B_批了output原样给A(hermes):
    """堵"B 的本地数据出端无审批"的洞 —— 正面: B 放行后 A 拿到完整回复。"""
    hermes.room(True)
    th, box = _run_in_thread(hermes, "查进度", "run-1")
    assert _wait_pending(hermes, "run-1"), "run 没挂起进待审批表"
    assert th.is_alive(), "run 没阻塞, 直接返回了 —— 回复已经出端"

    snap = hermes.rl.snapshot_pending()
    assert snap["outputs"][0]["final_response"] == "B 的回复: 查进度"

    assert hermes.rl.resolve_output("run-1", "approve") is True
    th.join(timeout=3.0)
    assert not th.is_alive(), "B 批了 run 还没醒"
    assert box["result"]["final_response"] == "B 的回复: 查进度"
    assert not box["result"].get("failed")
    assert hermes.rl.snapshot_pending()["outputs"] == [], "放行后表里还留着"


def test_B拒绝_run以failed结束_A收到明确原因(hermes):
    """反面: 拒了不是给 A 一个空 output 让它猜, 是 failed + 说清楚为什么。"""
    hermes.room(True)
    th, box = _run_in_thread(hermes, "查进度", "run-2")
    assert _wait_pending(hermes, "run-2")
    hermes.rl.resolve_output("run-2", "deny")
    th.join(timeout=3.0)
    r = box["result"]
    assert r["final_response"] == "", "拒了回复还出去了"
    assert r["failed"] is True
    assert r["error"] == hermes.rl.OUTBOUND_DECLINED


def test_超时当拒绝_不无限占executor线程(hermes, monkeypatch):
    """B 不在电脑前, run 不能永远挂着。"""
    monkeypatch.setattr(hermes.rl, "OUTBOUND_APPROVAL_TIMEOUT_SECONDS", 0.3)
    hermes.room(True)
    th, box = _run_in_thread(hermes, "x", "run-3")
    th.join(timeout=3.0)
    assert not th.is_alive(), "超时了 run 还挂着"
    assert box["result"]["failed"] is True
    assert box["result"]["error"] == hermes.rl.OUTBOUND_TIMED_OUT
    assert hermes.rl.snapshot_pending()["outputs"] == [], "超时后表里还留着"


def test_非room_run一个字不动_也不阻塞(hermes):
    """判据不能宽 —— 员工自己在 Companion 里的对话既不被截也不被挂起。"""
    hermes.room(False)
    t0 = time.time()
    r = hermes.AIAgent().run_conversation("查进度", task_id="run-4")
    assert time.time() - t0 < 0.5, "非 room run 被阻塞了"
    assert r["final_response"] == "B 的回复: 查进度"
    assert not r.get("failed")
    assert hermes.rl.snapshot_pending()["outputs"] == []


def _swap_impl(hermes, fn):
    """换掉底层 run_conversation 的实现再重新装 patch。

    顺序要紧: **先 uninstall 再换**。反过来的话 uninstall 会把 _ORIG 里存的
    fixture 原始 fake 写回去, 换上的实现就丢了 —— 第一版就是这么写的, 结果
    两条测试"假通过" (跑的是 fake)。
    """
    hermes.rl.uninstall()
    hermes.AIAgent.run_conversation = fn
    hermes.rl.install()


def test_拒绝时不动hermes持有的原dict(hermes):
    """浅拷贝再改。hermes 内部可能还引用着那个 dict (usage 统计等)。"""
    hermes.room(True)
    orig_result = {"final_response": "secret", "usage": {"t": 1}}
    _swap_impl(hermes, lambda self, *a, **kw: orig_result)
    th, box = _run_in_thread(hermes, "x", "run-5")
    assert _wait_pending(hermes, "run-5")
    hermes.rl.resolve_output("run-5", "deny")
    th.join(timeout=3.0)
    assert box["result"] is not orig_result, "返回了 hermes 的原 dict 而不是拷贝"
    assert orig_result["final_response"] == "secret", "改到了 hermes 的原 dict"
    assert "failed" not in orig_result, "failed 标记写进了原 dict"


def test_批准时返回的就是原dict_不多拷一份(hermes):
    """放行路径原样返回 —— 不需要拷, 也别拷 (usage 等字段 hermes 要用)。"""
    hermes.room(True)
    orig_result = {"final_response": "ok", "usage": {"t": 1}}
    _swap_impl(hermes, lambda self, *a, **kw: orig_result)
    th, box = _run_in_thread(hermes, "x", "run-6")
    assert _wait_pending(hermes, "run-6")
    hermes.rl.resolve_output("run-6", "approve")
    th.join(timeout=3.0)
    assert box["result"] is orig_result


def test_空回复不挂起(hermes):
    """final_response 为空 (模型没说话) 没什么可审的, 直接过。"""
    hermes.room(True)
    _swap_impl(hermes, lambda self, *a, **kw: {"final_response": ""})
    t0 = time.time()
    r = hermes.AIAgent().run_conversation("x", task_id="run-7")
    assert time.time() - t0 < 0.5
    assert hermes.rl.snapshot_pending()["outputs"] == []


def test_resolve不认识的run_id返False(hermes):
    assert hermes.rl.resolve_output("nope", "approve") is False


def test_resolve非法choice抛错(hermes):
    with pytest.raises(ValueError):
        hermes.rl.resolve_output("x", "maybe")


def test_uninstall叫醒所有挂起的run(hermes):
    """单测/进程退出时不能留线程挂着等 10 分钟。"""
    hermes.room(True)
    th, box = _run_in_thread(hermes, "x", "run-8")
    assert _wait_pending(hermes, "run-8")
    hermes.rl.uninstall()
    th.join(timeout=3.0)
    assert not th.is_alive(), "uninstall 没叫醒挂起的 run"
    assert box["result"]["failed"] is True
    hermes.rl.install()  # fixture teardown 还要 uninstall 一次


# ─────────────────────────────────────────────────────────────────────
# P49.3  审批改道
# ─────────────────────────────────────────────────────────────────────
def test_room_run的审批只存不推(hermes):
    """堵"审批方反了 + command 泄露"的洞。原 cb 是 hermes 推 SSE 用的,
    room run 下**不能被调到** —— 调到就等于 A 看到了 B 要跑的 command。"""
    hermes.room(True)
    hermes_cb_called = []
    from tools.approval import register_gateway_notify
    register_gateway_notify("run-5", lambda d: hermes_cb_called.append(d))

    registered = hermes.state["notify_cbs"]["run-5"]
    registered({"command": "rm -rf /tmp/x", "description": "清理"})

    assert hermes_cb_called == [], "原 cb 被调了 —— command 推进了 A 的 SSE"
    snap = hermes.rl.snapshot_pending()
    assert [a["run_id"] for a in snap["approvals"]] == ["run-5"]
    assert snap["approvals"][0]["command"] == "rm -rf /tmp/x"


def test_非room_run的审批原样走hermes(hermes):
    """判据不能宽 —— 员工自己的审批还得弹到自己的 Companion (P15 那条路)。"""
    hermes.room(False)
    hermes_cb_called = []
    from tools.approval import register_gateway_notify
    register_gateway_notify("run-6", lambda d: hermes_cb_called.append(d))

    hermes.state["notify_cbs"]["run-6"]({"command": "ls"})
    assert len(hermes_cb_called) == 1
    assert hermes.rl.snapshot_pending()["approvals"] == []


# ─────────────────────────────────────────────────────────────────────
# 待审批表的生命周期
# ─────────────────────────────────────────────────────────────────────
def _push_approval(hermes, run_id, command="c"):
    """往 approvals 表塞一条 (走真实的 P49.3 改道路径)。"""
    from tools.approval import register_gateway_notify
    register_gateway_notify(run_id, lambda d: None)
    hermes.state["notify_cbs"][run_id]({"command": command})


def test_pop_approval之后不再出现(hermes):
    hermes.room(True)
    _push_approval(hermes, "run-9")
    assert hermes.rl.pop_approval("run-9")["command"] == "c"
    assert hermes.rl.pop_approval("run-9") is None
    assert hermes.rl.snapshot_pending()["approvals"] == []


def test_approvals超过TTL自动清掉(hermes, monkeypatch):
    """B 一直不处理不能无限攒。(outputs 表由 run 自己收尾, 不靠 TTL。)"""
    hermes.room(True)
    _push_approval(hermes, "run-10")
    assert len(hermes.rl.snapshot_pending()["approvals"]) == 1
    later = time.time() + hermes.rl.PENDING_TTL_SECONDS + 1
    monkeypatch.setattr(hermes.rl.time, "time", lambda: later)
    assert hermes.rl.snapshot_pending()["approvals"] == []


def test_approvals_TTL之内不清(hermes, monkeypatch):
    """跟上一条配对: GC 是按 TTL 算的, 不是每次 snapshot 都清空。"""
    hermes.room(True)
    _push_approval(hermes, "run-11")
    later = time.time() + hermes.rl.PENDING_TTL_SECONDS - 60
    monkeypatch.setattr(hermes.rl.time, "time", lambda: later)
    assert len(hermes.rl.snapshot_pending()["approvals"]) == 1


def test_快照不透内部字段(hermes):
    """_at / _gate / _decision 是内部字段, 不该透给 Companion —— Event 对象
    透出去 json 直接炸, 时间戳和决定透出去是多余信息。"""
    hermes.room(True)
    _push_approval(hermes, "run-12")
    th, _ = _run_in_thread(hermes, "x", "run-13")
    assert _wait_pending(hermes, "run-13")
    snap = hermes.rl.snapshot_pending()
    for item in snap["approvals"] + snap["outputs"]:
        leaked = [k for k in item if k.startswith("_")]
        assert not leaked, f"透了内部字段: {leaked}"
    import json
    json.dumps(snap)  # 能序列化 —— Event 对象混进去这里会炸
    hermes.rl.resolve_output("run-13", "deny")
    th.join(timeout=3.0)


# ─────────────────────────────────────────────────────────────────────
# 装载
# ─────────────────────────────────────────────────────────────────────
def test_install幂等_不双包(hermes):
    """装两次不能包两层 —— 双包会让截走的回复被截两次 (第二次截到空)。"""
    before = hermes.AIAgent.run_conversation
    hermes.rl.install()
    assert hermes.AIAgent.run_conversation is before


def test_uninstall还原三个目标(hermes):
    from gateway import hosted_room_peer
    from tools import approval
    a, b, c = (hosted_room_peer.issue_room_grant,
               hermes.AIAgent.run_conversation,
               approval.register_gateway_notify)
    hermes.rl.uninstall()
    assert hosted_room_peer.issue_room_grant is not a
    assert hermes.AIAgent.run_conversation is not b
    assert approval.register_gateway_notify is not c
    hermes.rl.install()  # fixture 的 teardown 还要 uninstall 一次


def test_三个目标都在fail_loud名单里():
    """upstream 改名必须在插件装载期炸, 不是运行时静默漏。"""
    import plugin_verify as v
    targets = {(m, a) for m, a, _ in v._PATCH_TARGETS}
    assert ("gateway.hosted_room_peer", "issue_room_grant") in targets
    assert ("gateway.hosted_room_execution_policy", "current_room_execution_policy") in targets
    assert ("tools.approval", "register_gateway_notify") in targets
    assert "run_conversation" in v._AIAGENT_METHOD_TARGETS
