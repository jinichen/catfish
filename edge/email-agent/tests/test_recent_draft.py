"""11/11: 草稿箱里最近存的那封 —— 「小鲶存了草稿 → 跳到草稿箱」的兜底。

11/10 鸿波: 邮件落草稿箱后没有跳转。小鲶绕开 `catfish-email draft`, 用自己的脚本直接在 Mail 里建草稿,
last_draft 那一笔就没有。Companion 回合结束时改问这里。
"""
from __future__ import annotations

import json

import pytest

from catfish_email import cli_action
from catfish_email.adapters import apple_mail_recent_draft as rd
from catfish_email.adapters.base import EmailAdapterError


def test_解析_账号_编号_多少秒之前():
    seen = {}

    def run(script: str) -> str:
        seen["script"] = script
        return "Chinatelecom\x1f3542\x1f37\n"

    assert rd.newest_draft(run, 120) == ("Chinatelecom", "3542", 37)
    assert "set maxAge to 120" in seen["script"]
    # 时间在 Mail 里相减 (不把 Date 头拿出来比), 各账号的草稿箱都看
    assert "nowD - (date sent of m)" in seen["script"] and "repeat with acc in accounts" in seen["script"]
    assert 'my resolveInbox(acc, "Drafts")' in seen["script"]


def test_没有新存的_返回None():
    assert rd.newest_draft(lambda s: "", 120) is None
    assert rd.newest_draft(lambda s: "\n", 120) is None


def test_返回不对劲_报错不乱猜():
    with pytest.raises(EmailAdapterError):
        rd.newest_draft(lambda s: "只有一段", 120)


class _Args:
    max_age = 90


def test_命令输出跟那一笔同样的字段_取最新的(capsys):
    class A:
        name = "apple_mail"

        def recent_draft(self, max_age):
            assert max_age == 90
            return ("apple_mail|Chinatelecom|3542", 30)

    class B:
        name = "other"

        def recent_draft(self, max_age):
            return ("other|x|1", 5)

    class NoSupport:
        name = "imap"

    assert cli_action._cmd_recent_draft([A(), NoSupport(), B()], _Args()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == "other|x|1" and out["adapter"] == "other" and set(out) == {"id", "adapter", "created_at"}


def test_都没有_输出null_某个来源出错不影响别的(capsys):
    class Broken:
        name = "apple_mail"

        def recent_draft(self, max_age):
            raise EmailAdapterError("Mail 没开")

    class Empty:
        name = "other"

        def recent_draft(self, max_age):
            return None

    assert cli_action._cmd_recent_draft([Broken(), Empty()], _Args()) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) is None
