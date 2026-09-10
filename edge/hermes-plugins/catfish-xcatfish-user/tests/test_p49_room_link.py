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
# P49.2  出站截走
# ─────────────────────────────────────────────────────────────────────
def test_room_run的回复被截走_output置空(hermes):
    """堵"B 的本地数据出端无审批"的洞。"""
    hermes.room(True)
    r = hermes.AIAgent().run_conversation("查进度", task_id="run-1")
    assert r["final_response"] == "", "output 没置空, A 会直接拿到 B 的回复"
    snap = hermes.rl.snapshot_pending()
    assert [o["run_id"] for o in snap["outputs"]] == ["run-1"]
    assert snap["outputs"][0]["final_response"] == "B 的回复: 查进度"


def test_非room_run一个字不动(hermes):
    """判据不能宽 —— 员工自己在 Companion 里的对话不许被截。"""
    hermes.room(False)
    r = hermes.AIAgent().run_conversation("查进度", task_id="run-2")
    assert r["final_response"] == "B 的回复: 查进度"
    assert hermes.rl.snapshot_pending()["outputs"] == []


def _swap_impl(hermes, fn):
    """换掉底层 run_conversation 的实现再重新装 patch。

    顺序要紧: **先 uninstall 再换**。反过来的话 uninstall 会把 _ORIG 里存的
    fixture 原始 fake 写回去, 换上的实现就丢了 —— 第一版就是这么写的, 结果
    test_空回复 跑的是 fake, test_原dict 更是"假通过" (fake 返新 dict, 原 dict
    当然没被改)。
    """
    hermes.rl.uninstall()
    hermes.AIAgent.run_conversation = fn
    hermes.rl.install()


def test_截走不动hermes持有的原dict(hermes):
    """浅拷贝再改。hermes 内部可能还引用着那个 dict (usage 统计等)。"""
    hermes.room(True)
    orig_result = {"final_response": "secret", "usage": {"t": 1}}
    _swap_impl(hermes, lambda self, *a, **kw: orig_result)
    r = hermes.AIAgent().run_conversation("x", task_id="run-3")
    assert r["final_response"] == ""
    assert r is not orig_result, "返回了 hermes 的原 dict 而不是拷贝"
    assert orig_result["final_response"] == "secret", "改到了 hermes 的原 dict"


def test_空回复不进待审批表(hermes):
    """final_response 为空 (模型没说话) 没什么可审的, 别往表里塞空条目。"""
    hermes.room(True)
    _swap_impl(hermes, lambda self, *a, **kw: {"final_response": ""})
    hermes.AIAgent().run_conversation("x", task_id="run-4")
    assert hermes.rl.snapshot_pending()["outputs"] == []


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
def test_pop之后不再出现(hermes):
    hermes.room(True)
    hermes.AIAgent().run_conversation("x", task_id="run-7")
    assert hermes.rl.pop_output("run-7")["final_response"].startswith("B 的回复")
    assert hermes.rl.pop_output("run-7") is None
    assert hermes.rl.snapshot_pending()["outputs"] == []


def test_超过TTL自动清掉(hermes, monkeypatch):
    """B 一直不处理不能无限攒。"""
    hermes.room(True)
    hermes.AIAgent().run_conversation("x", task_id="run-8")
    assert len(hermes.rl.snapshot_pending()["outputs"]) == 1
    # 插入用的是真实时钟; 现在把时钟拨到 TTL 之后再看一眼
    later = time.time() + hermes.rl.PENDING_TTL_SECONDS + 1
    monkeypatch.setattr(hermes.rl.time, "time", lambda: later)
    assert hermes.rl.snapshot_pending()["outputs"] == []


def test_TTL之内不清(hermes, monkeypatch):
    """跟上一条配对: 证明 GC 是按 TTL 算的, 不是每次 snapshot 都清空。"""
    hermes.room(True)
    hermes.AIAgent().run_conversation("x", task_id="run-8b")
    later = time.time() + hermes.rl.PENDING_TTL_SECONDS - 60
    monkeypatch.setattr(hermes.rl.time, "time", lambda: later)
    assert len(hermes.rl.snapshot_pending()["outputs"]) == 1


def test_快照不带内部时间戳(hermes):
    """_at 是内部字段, 不该透给 Companion。"""
    hermes.room(True)
    hermes.AIAgent().run_conversation("x", task_id="run-9")
    from tools.approval import register_gateway_notify
    register_gateway_notify("run-9", lambda d: None)
    hermes.state["notify_cbs"]["run-9"]({"command": "c"})
    snap = hermes.rl.snapshot_pending()
    assert "_at" not in snap["outputs"][0]
    assert "_at" not in snap["approvals"][0]


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
