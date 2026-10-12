"""提醒同步到微信 (10/12, wechat_push.py): 开关 / 读 hermes .env / 推送结果 / 闹钟联动。
全程假发送, 不碰真微信。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from catfish_tool_bridge import local_pim, reminders, wechat_push

ENV = """# hermes
export WEIXIN_TOKEN="tok-abc"
WEIXIN_ACCOUNT_ID=acc-1
WEIXIN_HOME_CHANNEL='wxid_me'
OTHER=1
"""


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(wechat_push, "_catfish_dir", lambda: tmp_path / ".catfish")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CATFISH_TASK_LIBRARY_PATH", str(tmp_path / "task_library.db"))
    # 走 Windows 分支 (Catfish 自己的任务库): 不然 mac 上会去写真 Reminders.app
    monkeypatch.setattr("platform.system", lambda: "Windows")
    (tmp_path / "hermes").mkdir()
    return tmp_path


def turn_on(tmp_path, on=True):
    d = tmp_path / ".catfish"
    d.mkdir(exist_ok=True)
    (d / "notify_prefs.json").write_text(json.dumps({"wechat_reminders": on}), encoding="utf-8")


def test_off_by_default_and_reads_switch(iso):
    assert wechat_push.enabled() is False
    turn_on(iso)
    assert wechat_push.enabled() is True


def test_reads_weixin_env_with_quotes_and_export(iso):
    (iso / "hermes" / ".env").write_text(ENV, encoding="utf-8")
    t = wechat_push.target()
    assert t == {"token": "tok-abc", "account_id": "acc-1", "chat_id": "wxid_me", "base_url": ""}


def test_no_binding_means_no_target_and_status_says_why(iso):
    assert wechat_push.target() is None
    assert wechat_push.push("x") is False
    st = json.loads((iso / ".catfish" / "notify_status.json").read_text(encoding="utf-8"))
    assert st["ok"] is False and "绑定微信" in st["error"]


def test_push_success_and_stale_session_message(iso, monkeypatch):
    (iso / "hermes" / ".env").write_text(ENV, encoding="utf-8")
    sent = []

    async def fake_send(tgt, text):
        sent.append((tgt["chat_id"], text))
        return {"success": True}

    monkeypatch.setattr(wechat_push, "_send", fake_send)
    assert wechat_push.push("⏰ 小鲶提醒\n交月报") is True
    assert sent == [("wxid_me", "⏰ 小鲶提醒\n交月报")]

    async def stale(tgt, text):
        return {"error": "Weixin send failed: ret=-14 errmsg=session timeout"}

    monkeypatch.setattr(wechat_push, "_send", stale)
    assert wechat_push.push("x") is False
    st = json.loads((iso / ".catfish" / "notify_status.json").read_text(encoding="utf-8"))
    assert "给小鲶发一句话" in st["error"]


def test_hermes_rate_limit_report_means_send_one_message():
    # 10/12 真机: 7 天没聊, iLink 拒主动推送, hermes 报 "rate limited; cooldown active"
    msg = wechat_push._friendly("Weixin send failed: iLink sendmessage rate limited; cooldown active for 30.0s")
    assert "给小鲶发一句话" in msg


def test_dates_are_not_mistaken_for_error_codes():
    assert "过期" not in wechat_push._friendly("截止 2026-2-1 发送失败")


def test_alarm_pushes_only_when_switch_on(iso, monkeypatch):
    calls = []
    monkeypatch.setattr(wechat_push, "push", lambda text, tgt=None: calls.append(text) or True)
    toasts = []
    r = reminders.tool_create_reminder({"title": "交月报", "due_date_iso": "2026-10-09T09:30:00"})
    local_pim.fire_due_alarms(datetime(2026, 10, 9, 9, 31), lambda t, b: toasts.append((t, b)))
    assert toasts and calls == []          # 没开: 只弹系统通知

    turn_on(iso)
    r2 = reminders.tool_create_reminder({"title": "进度会", "due_date_iso": "2026-10-09T14:00:00"})
    local_pim.fire_due_alarms(datetime(2026, 10, 9, 14, 0, 5), lambda t, b: toasts.append((t, b)))
    assert calls == ["⏰ 小鲶提醒: 进度会 · 10-09 14:00"]  # 一行, 微信里是一个气泡
    assert r["task_id"] != r2["task_id"]


def test_push_failure_never_blocks_the_alarm(iso, monkeypatch):
    turn_on(iso)

    def boom(text, tgt=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(wechat_push, "push", boom)
    r = reminders.tool_create_reminder({"title": "打电话", "due_date_iso": "2026-10-09T10:00:00"})
    toasts = []
    assert local_pim.fire_due_alarms(datetime(2026, 10, 9, 10, 0, 5), lambda t, b: toasts.append((t, b))) == [r["task_id"]]
    assert toasts  # 系统通知照弹, 闹钟照样标记已弹
