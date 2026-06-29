"""Quota 配额限流测试 (五一 sprint 5/3, BL-D9).

覆盖:
- yaml 加载 (默认 / overrides / 缺文件)
- record_usage / sum_tokens_*_since (sqlite store)
- check_quota 4 个维度
- 滑动窗口边界 (1 分钟前的事件不该计入 1 分钟内 quota)
- estimate_tokens
- friendly_quota_message
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from catfish_gateway import quota


@pytest.fixture(autouse=True)
def isolated_db_and_config(tmp_path: Path, monkeypatch) -> None:
    """每个测试用独立 sqlite + 独立 quotas.yaml."""
    monkeypatch.setenv("CATFISH_QUOTA_DB", str(tmp_path / "quota.db"))
    monkeypatch.setenv("CATFISH_QUOTAS_PATH", str(tmp_path / "quotas.yaml"))


def _write_quotas_yaml(tmp_path: Path, content: str) -> None:
    (tmp_path / "quotas.yaml").write_text(content, encoding="utf-8")


# ── yaml 加载 ────────────────────────────────────────────────


def test_load_default_when_no_yaml(tmp_path: Path) -> None:
    config = quota.load_quota_config()
    assert config.default_user.tokens_per_minute == 100_000
    assert config.default_user.tokens_per_day == 1_000_000
    assert config.user_overrides == {}
    assert config.model_quotas == {}
    assert config.department_quotas == {}


def test_load_with_defaults(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 50000
    tokens_per_day: 500000
  per_model:
    "catfish-public-qwen-flash":
      tokens_per_day: 5000000
  per_department:
    "研发部":
      tokens_per_day: 30000000
""")
    config = quota.load_quota_config()
    assert config.default_user.tokens_per_minute == 50_000
    assert config.model_quotas["catfish-public-qwen-flash"].tokens_per_day == 5_000_000
    assert config.department_quotas["研发部"].tokens_per_day == 30_000_000


def test_user_override_higher_than_default(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 100000
overrides:
  users:
    "ceo@ffcs.cn":
      tokens_per_minute: 1000000
""")
    config = quota.load_quota_config()
    assert config.per_user_for("ceo@ffcs.cn").tokens_per_minute == 1_000_000
    # 普通员工走默认
    assert config.per_user_for("alice@ffcs.cn").tokens_per_minute == 100_000


def test_department_override(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_department:
    "研发部":
      tokens_per_day: 50000000
overrides:
  departments:
    "研发部":
      tokens_per_day: 80000000
""")
    config = quota.load_quota_config()
    assert config.per_department_for("研发部").tokens_per_day == 80_000_000


# ── record_usage / sum_*_since ──────────────────────────────


def test_record_and_sum_user(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 2000)
    quota.record_usage("alice@x.com", "研发部", "qwen", 500, 500)
    quota.record_usage("bob@x.com", "研发部", "qwen", 100, 100)

    cutoff = int(time.time() * 1000) - 60_000
    assert quota.sum_tokens_user_since("alice@x.com", cutoff) == 4000  # 1000+2000+500+500
    assert quota.sum_tokens_user_since("bob@x.com", cutoff) == 200


def test_sliding_window_old_events_excluded(tmp_path: Path) -> None:
    """近 1 分钟 quota 不应该包含 1 分 1 秒前的事件."""
    # 直接写一个 65 秒前的 event
    import sqlite3
    db_path = tmp_path / "quota.db"
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 0)  # 触发 schema 创建

    old_ts = int((time.time() - 65) * 1000)  # 65 秒前
    conn = sqlite3.connect(str(db_path))
    with conn:
        conn.execute(
            "INSERT INTO quota_events VALUES (?, ?, ?, ?, ?, ?)",
            (old_ts, "alice@x.com", "研发部", "qwen", 99999, 0),
        )
    conn.close()

    cutoff_60s = int(time.time() * 1000) - 60_000
    used = quota.sum_tokens_user_since("alice@x.com", cutoff_60s)
    # 应该只算最近 1 分钟内的, 65 秒前的 99999 不算
    assert used == 1000  # 只有最初 record 的


def test_sum_model_since(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen-flash", 1000, 0)
    quota.record_usage("bob@x.com", "研发部", "qwen-flash", 2000, 0)
    quota.record_usage("alice@x.com", "研发部", "gemini", 500, 0)

    cutoff = 0  # 全部
    assert quota.sum_tokens_model_since("qwen-flash", cutoff) == 3000
    assert quota.sum_tokens_model_since("gemini", cutoff) == 500


def test_sum_dept_since(tmp_path: Path) -> None:
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("bob@x.com", "销售部", "qwen", 2000, 0)

    cutoff = 0
    assert quota.sum_tokens_dept_since("研发部", cutoff) == 1000
    assert quota.sum_tokens_dept_since("销售部", cutoff) == 2000


# ── check_quota 4 维度 ──────────────────────────────────────


def test_check_quota_allowed_when_empty(tmp_path: Path) -> None:
    qc = quota.check_quota("alice@x.com", "研发部", "qwen", 5000)
    assert qc.allowed
    assert qc.dimension == ""


def test_check_quota_per_user_minute_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000
""")
    # 已用 8000
    quota.record_usage("alice@x.com", "研发部", "qwen", 5000, 3000)
    # 再来 5000 → 8000+5000 > 10000 → 拒
    qc = quota.check_quota("alice@x.com", "研发部", "qwen", 5000)
    assert not qc.allowed
    assert qc.dimension == "per_user_minute"
    assert qc.current == 8000
    assert qc.limit == 10000
    assert qc.reset_at > int(time.time())


def test_check_quota_per_model_day_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000000
    tokens_per_day: 100000000
  per_model:
    "qwen-flash":
      tokens_per_day: 1000
""")
    quota.record_usage("alice@x.com", "研发部", "qwen-flash", 800, 0)
    qc = quota.check_quota("alice@x.com", "研发部", "qwen-flash", 500)
    assert not qc.allowed
    assert qc.dimension == "per_model_day"


def test_check_quota_per_dept_day_blocked(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 10000000
    tokens_per_day: 100000000
  per_department:
    "研发部":
      tokens_per_day: 5000
""")
    quota.record_usage("alice@x.com", "研发部", "qwen", 4500, 0)
    qc = quota.check_quota("bob@x.com", "研发部", "qwen", 1000)
    assert not qc.allowed
    assert qc.dimension == "per_dept_day"
    assert qc.current == 4500


def test_check_quota_zero_means_unlimited(tmp_path: Path) -> None:
    """user/model/dept 各维度 tokens_per_*=0 表示不限.

    场景: 内网 LLM (qwen-private) + 内部员工, 全维度不限制 token.
    """
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 0
    tokens_per_day: 0
  per_model:
    "qwen-private":
      tokens_per_day: 0
""")
    # 写超大量, 但任意维度 = 0 不应触发限流
    quota.record_usage("alice@x.com", "研发部", "qwen-private", 10_000_000, 0)
    qc = quota.check_quota("alice@x.com", "研发部", "qwen-private", 1_000_000)
    assert qc.allowed, f"全维度 0 应该不限, 实际 dimension={qc.dimension}"


def test_user_override_takes_effect(tmp_path: Path) -> None:
    """ceo override 高 quota 不被 default user 限."""
    _write_quotas_yaml(tmp_path, """
defaults:
  per_user:
    tokens_per_minute: 1000
overrides:
  users:
    "ceo@ffcs.cn":
      tokens_per_minute: 1000000
""")
    # ceo 用 5000 (高过默认 1000) 但他是 override 的高 quota
    quota.record_usage("ceo@ffcs.cn", "研发部", "qwen", 5000, 0)
    qc = quota.check_quota("ceo@ffcs.cn", "研发部", "qwen", 5000)
    assert qc.allowed  # 5000+5000 < 1000000


# ── estimate_tokens / friendly_quota_message ────────────────


def test_estimate_tokens_short_text() -> None:
    assert quota.estimate_tokens("") == 1000  # 最低 1000
    assert quota.estimate_tokens("hi") == 1000
    assert quota.estimate_tokens("a" * 10) == 1000  # < 4000 字符


def test_estimate_tokens_long_text() -> None:
    text = "a" * 8000  # 8000 字符 = ~2000 token
    assert quota.estimate_tokens(text) == 2000


# ─── friendly_quota_message (P3.5.29 + P3.5.139) ─────────────────
#
# friendly_message 里"建议换的 model 名" 由 _resolve_friendly_model 拿,
# chain (P3.5.139 鸿波"都要去除硬编码"):
#   1. roles.yaml load 成功 → roles_module.resolve_or_none(role)
#   2. .env CATFISH_FALLBACK_{ROLE.upper()} → env var
#   3. 空字符串 (文案降级)
#
# 测试三种情况都验证, 不再写死 "catfish-private-main" 字面值.


def _load_test_roles_yaml(tmp_path: Path) -> None:
    """测试用 fixture: 写一份 roles.yaml + load 进 roles 模块."""
    from catfish_gateway import roles as roles_module

    yaml_path = tmp_path / "roles.yaml"
    yaml_path.write_text(
        """
roles:
  chat_default: test-main-model
  public_flash: test-flash-model
""",
        encoding="utf-8",
    )
    roles_module.load_roles(yaml_path)


def _reset_roles_module() -> None:
    """测试间 reset roles 模块状态, 防 test pollution."""
    from catfish_gateway import roles as roles_module

    roles_module._roles = None  # type: ignore[attr-defined]
    roles_module._fallback_chain = None  # type: ignore[attr-defined]


def test_friendly_message_per_user_minute(tmp_path: Path) -> None:
    """roles.yaml load 成功 → 文案带 resolve 后真 model 名 (chat_default)."""
    _load_test_roles_yaml(tmp_path)
    try:
        qc = quota.QuotaCheck(
            allowed=False,
            dimension="per_user_minute",
            current=105000,
            limit=100000,
            reset_at=int(time.time()) + 30,
        )
        msg = quota.friendly_quota_message(qc, "alice@x.com", "qwen-flash")
        assert "分钟" in msg
        # P3.5.139: roles.yaml `chat_default: test-main-model` → 文案应有这个名
        assert "test-main-model" in msg
    finally:
        _reset_roles_module()


def test_friendly_message_per_model_day(tmp_path: Path) -> None:
    """roles.yaml load 成功 → per_model_day 文案带 chat_default + public_flash."""
    _load_test_roles_yaml(tmp_path)
    try:
        qc = quota.QuotaCheck(
            allowed=False,
            dimension="per_model_day",
            current=5000000,
            limit=5000000,
            reset_at=int(time.time()) + 86400,
        )
        msg = quota.friendly_quota_message(qc, "alice@x.com", "gemini-pro")
        assert "gemini-pro" in msg
        assert "test-main-model" in msg
        assert "test-flash-model" in msg
    finally:
        _reset_roles_module()


def test_friendly_message_roles_unloaded_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3.5.139: roles 没 load → 走 .env CATFISH_FALLBACK_{ROLE.upper()}.

    模拟 roles.yaml load 失败 (没调 load_roles) + 客户配了 .env 兜底.
    """
    _reset_roles_module()
    monkeypatch.setenv("CATFISH_FALLBACK_CHAT_DEFAULT", "env-fallback-main")
    monkeypatch.setenv("CATFISH_FALLBACK_PUBLIC_FLASH", "env-fallback-flash")

    qc = quota.QuotaCheck(
        allowed=False,
        dimension="per_model_day",
        current=5000000,
        limit=5000000,
        reset_at=int(time.time()) + 86400,
    )
    msg = quota.friendly_quota_message(qc, "alice@x.com", "gemini-pro")
    assert "env-fallback-main" in msg
    assert "env-fallback-flash" in msg


def test_friendly_message_roles_unloaded_no_env_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P3.5.139: roles 没 load + .env 没配 → 文案降级渲染空字符串.

    略丑 ("换 (内网不限)"), 但比静默走硬编码字面值清晰. dev 启动 gateway
    没 .env 时可见, 不该出现在正常 production.
    """
    _reset_roles_module()
    monkeypatch.delenv("CATFISH_FALLBACK_CHAT_DEFAULT", raising=False)
    monkeypatch.delenv("CATFISH_FALLBACK_PUBLIC_FLASH", raising=False)

    qc = quota.QuotaCheck(
        allowed=False,
        dimension="per_user_minute",
        current=105000,
        limit=100000,
        reset_at=int(time.time()) + 30,
    )
    msg = quota.friendly_quota_message(qc, "alice@x.com", "qwen-flash")
    # 文案降级: chat_default 空字符串 → "换  (内网不限)"
    assert "catfish-private-main" not in msg
    assert "catfish-public-qwen-flash" not in msg
    assert "换  (内网不限)" in msg or "换 (内网不限)" in msg


# ── 部门级聚合 (5/2 RBAC manager Dashboard 用) ────────────────


def test_top_users_in_department_basic(tmp_path: Path) -> None:
    """top_users_in_department 按 token 用量降序返 top N 员工."""
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 500)
    quota.record_usage("bob@x.com", "研发部", "qwen", 800, 200)
    quota.record_usage("carol@x.com", "研发部", "qwen", 100, 0)
    cutoff = int(time.time() * 1000) - 86_400_000

    top = quota.top_users_in_department("研发部", cutoff, limit=10)
    assert len(top) == 3
    assert top[0] == {"user_email": "alice@x.com", "tokens_used": 1500}
    assert top[1] == {"user_email": "bob@x.com", "tokens_used": 1000}
    assert top[2] == {"user_email": "carol@x.com", "tokens_used": 100}


def test_top_users_department_isolation(tmp_path: Path) -> None:
    """部门隔离: 销售部员工不出现在研发部 top."""
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("david@x.com", "销售部", "qwen", 9999, 0)
    cutoff = int(time.time() * 1000) - 86_400_000

    top_dev = quota.top_users_in_department("研发部", cutoff)
    assert len(top_dev) == 1
    assert top_dev[0]["user_email"] == "alice@x.com"

    top_sales = quota.top_users_in_department("销售部", cutoff)
    assert len(top_sales) == 1
    assert top_sales[0]["user_email"] == "david@x.com"


def test_top_users_limit(tmp_path: Path) -> None:
    """limit 截断 top N."""
    for i in range(15):
        quota.record_usage(f"user{i}@x.com", "研发部", "qwen", 100 * (i + 1), 0)
    cutoff = int(time.time() * 1000) - 86_400_000

    top = quota.top_users_in_department("研发部", cutoff, limit=5)
    assert len(top) == 5
    # 最大的应该在前 (user14 = 1500)
    assert top[0]["tokens_used"] == 1500


def test_audit_summary_dept_basic(tmp_path: Path) -> None:
    """audit_summary_dept_since 返 request_count / total_tokens / by_model / by_user."""
    quota.record_usage("alice@x.com", "研发部", "qwen", 1000, 500)
    quota.record_usage("bob@x.com", "研发部", "qwen", 800, 200)
    quota.record_usage("alice@x.com", "研发部", "gemini", 300, 100)
    cutoff = int(time.time() * 1000) - 86_400_000

    s = quota.audit_summary_dept_since("研发部", cutoff)
    assert s["request_count"] == 3
    assert s["total_tokens"] == 2900

    # by_model 按 token 降序
    assert len(s["by_model"]) == 2
    assert s["by_model"][0]["model"] == "qwen"
    assert s["by_model"][0]["count"] == 2
    assert s["by_model"][0]["total_tokens"] == 2500
    assert s["by_model"][1]["model"] == "gemini"

    # by_user 按 token 降序
    assert len(s["by_user"]) == 2
    assert s["by_user"][0]["user_email"] == "alice@x.com"
    assert s["by_user"][0]["total_tokens"] == 1900


def test_audit_summary_dept_empty(tmp_path: Path) -> None:
    """空部门返 0 + 空数组."""
    cutoff = int(time.time() * 1000) - 86_400_000
    s = quota.audit_summary_dept_since("不存在的部门", cutoff)
    assert s["request_count"] == 0
    assert s["total_tokens"] == 0
    assert s["by_model"] == []
    assert s["by_user"] == []


# ── update_department_quota (5/2 RBAC manager PUT) ──────────


def test_update_department_quota_creates_yaml(tmp_path: Path) -> None:
    """yaml 不存在 → 创建并写入."""
    yaml_path = tmp_path / "quotas.yaml"
    import os
    os.environ["CATFISH_QUOTAS_PATH"] = str(yaml_path)

    ok = quota.update_department_quota("研发部", 30_000_000)
    assert ok
    assert yaml_path.exists()

    config = quota.load_quota_config()
    dq = config.department_quotas.get("研发部")
    assert dq is not None
    assert dq.tokens_per_day == 30_000_000


def test_update_department_quota_handles_none_section(tmp_path: Path) -> None:
    """yaml 'departments:' 后只有注释 (None) 时也能正确处理."""
    yaml_path = tmp_path / "quotas.yaml"
    yaml_path.write_text("""
overrides:
  departments:
    # comment only, value None
""")
    import os
    os.environ["CATFISH_QUOTAS_PATH"] = str(yaml_path)

    ok = quota.update_department_quota("engineering", 50_000_000)
    assert ok
    text = yaml_path.read_text()
    assert "engineering" in text
    assert "50000000" in text


def test_update_department_quota_overwrite(tmp_path: Path) -> None:
    """已有 dept override → 更新."""
    yaml_path = tmp_path / "quotas.yaml"
    import os
    os.environ["CATFISH_QUOTAS_PATH"] = str(yaml_path)

    quota.update_department_quota("研发部", 30_000_000)
    quota.update_department_quota("研发部", 80_000_000)  # 改

    config = quota.load_quota_config()
    assert config.department_quotas["研发部"].tokens_per_day == 80_000_000


# ── 全局聚合 (5/2 RBAC admin) ────────────────────────────────


def test_top_departments(tmp_path: Path) -> None:
    """全局 top N 部门按 token 降序."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 500)
    quota.record_usage("b@x.com", "研发部", "qwen", 800, 200)
    quota.record_usage("c@x.com", "销售部", "gemini", 5000, 0)
    quota.record_usage("d@x.com", "财务部", "qwen", 100, 100)

    cutoff = int(time.time() * 1000) - 86_400_000
    top = quota.top_departments(cutoff, limit=10)
    assert len(top) == 3
    assert top[0]["department"] == "销售部"
    assert top[0]["tokens_used"] == 5000
    assert top[1]["department"] == "研发部"
    assert top[1]["tokens_used"] == 2500
    assert top[2]["department"] == "财务部"


def test_audit_summary_global(tmp_path: Path) -> None:
    """全员聚合 - 4 数字 + 3 维分布."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 500)
    quota.record_usage("b@x.com", "研发部", "qwen", 800, 200)
    quota.record_usage("c@x.com", "销售部", "gemini", 5000, 0)

    cutoff = int(time.time() * 1000) - 86_400_000
    s = quota.audit_summary_global_since(cutoff)

    assert s["request_count"] == 3
    assert s["total_tokens"] == 7500
    assert s["active_users"] == 3
    assert s["active_departments"] == 2

    # 部门 / 模型 / 员工 都应该有
    assert len(s["by_department"]) == 2
    assert len(s["by_model"]) == 2
    assert len(s["by_user"]) == 3


def test_audit_period_totals_window_bounded(tmp_path: Path) -> None:
    """BL-AUDIT-UX-P1 (5/17): audit_period_totals 按 start/end 窗口边界 SELECT.

    防回归: 老 audit_summary_global_since 是 ts >= cutoff (开放上界), 新 helper
    要 ts >= start AND ts < end (闭开区间), 防"上期数据"跟"本期"窗口重叠.
    """
    # 3 条都是当前时间 (后面 manipulate ts 不容易 — 改成验证 window 行为)
    quota.record_usage("a@x.com", "研发部", "qwen", 100, 100)
    quota.record_usage("b@x.com", "研发部", "qwen", 200, 200)

    now_ms = int(time.time() * 1000)
    # 窗口 [now-1h, now] → 应包含全 2 条
    s_in = quota.audit_period_totals(now_ms - 3_600_000, now_ms + 1000)
    assert s_in["request_count"] == 2
    assert s_in["total_tokens"] == 600
    assert s_in["active_users"] == 2
    assert s_in["active_departments"] == 1

    # 窗口 [yesterday, now-1h] → 应不含任何 (新写的 record 在 now 附近)
    s_out = quota.audit_period_totals(now_ms - 86_400_000, now_ms - 3_600_000)
    assert s_out["request_count"] == 0
    assert s_out["total_tokens"] == 0


def test_audit_period_totals_excludes_internal_loopback(tmp_path: Path) -> None:
    """BL-AUDIT-INTERNAL-SPLIT 合规: internal:* user 不算 audit period totals."""
    quota.record_usage("real@x.com", "研发部", "qwen", 500, 500)
    quota.record_usage("internal:gateway-loopback", "", "qwen", 9999, 9999)

    now_ms = int(time.time() * 1000)
    s = quota.audit_period_totals(now_ms - 3_600_000, now_ms + 1000)
    assert s["request_count"] == 1  # 不含 internal
    assert s["total_tokens"] == 1000


def test_audit_period_totals_empty_returns_zeros(tmp_path: Path) -> None:
    """空窗口 → 全 0, 不 raise."""
    now_ms = int(time.time() * 1000)
    s = quota.audit_period_totals(now_ms - 1000, now_ms - 500)
    assert s == {
        "request_count": 0,
        "total_tokens": 0,
        "active_users": 0,
        "active_departments": 0,
    }


# ── BL-AUDIT-UX-P2: drill-down filter ────────────────────────────


def test_audit_summary_global_filter_by_model(tmp_path: Path) -> None:
    """filter_model=X → 只返该 model 数据 (top-level + by_model 都收紧)."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 500)
    quota.record_usage("a@x.com", "研发部", "gemini", 200, 100)
    quota.record_usage("b@x.com", "销售部", "qwen", 800, 400)

    cutoff = int(time.time() * 1000) - 3_600_000
    s = quota.audit_summary_global_since(cutoff, filter_model="qwen")

    # 只算 qwen 的 2 条
    assert s["request_count"] == 2
    assert s["total_tokens"] == 1000 + 500 + 800 + 400
    # 跨 2 个员工 / 2 个部门 (qwen 被 a 和 b 用)
    assert s["active_users"] == 2
    # by_model 自然就剩 qwen 1 项
    assert len(s["by_model"]) == 1
    assert s["by_model"][0]["model"] == "qwen"


def test_audit_summary_global_filter_by_dept(tmp_path: Path) -> None:
    """filter_dept=研发部 → 只返该部门数据."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("b@x.com", "研发部", "gemini", 500, 0)
    quota.record_usage("c@x.com", "销售部", "qwen", 9999, 0)

    cutoff = int(time.time() * 1000) - 3_600_000
    s = quota.audit_summary_global_since(cutoff, filter_dept="研发部")

    assert s["request_count"] == 2  # 不含销售
    assert s["total_tokens"] == 1500
    assert s["active_users"] == 2
    # by_dept 只剩研发部
    dept_names = {d["department"] for d in s["by_department"]}
    assert dept_names == {"研发部"}


def test_audit_summary_global_filter_by_user(tmp_path: Path) -> None:
    """filter_user=a@x.com → 只返该员工数据."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("a@x.com", "研发部", "gemini", 500, 0)
    quota.record_usage("b@x.com", "研发部", "qwen", 9999, 0)

    cutoff = int(time.time() * 1000) - 3_600_000
    s = quota.audit_summary_global_since(cutoff, filter_user="a@x.com")

    assert s["request_count"] == 2  # 不含 b
    assert s["total_tokens"] == 1500
    assert s["active_users"] == 1
    user_emails = {u["user_email"] for u in s["by_user"]}
    assert user_emails == {"a@x.com"}


def test_audit_summary_global_filter_internal_excluded(tmp_path: Path) -> None:
    """drill-down filter 不影响 internal_* (loopback 永远独立算)."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("internal:gateway-loopback", "", "qwen", 5000, 0)

    cutoff = int(time.time() * 1000) - 3_600_000
    s = quota.audit_summary_global_since(cutoff, filter_model="qwen")
    # 业务部分 filter 生效 (qwen 1 条业务)
    assert s["request_count"] == 1
    assert s["total_tokens"] == 1000
    # internal 不受 filter 影响, 仍算 5000
    assert s["internal_request_count"] == 1
    assert s["internal_tokens"] == 5000


def test_audit_period_totals_filter_applies(tmp_path: Path) -> None:
    """audit_period_totals 同样支持 filter, 给 trend ↑↓ 当前期 vs 上期同 filter 用."""
    quota.record_usage("a@x.com", "研发部", "qwen", 1000, 0)
    quota.record_usage("a@x.com", "研发部", "gemini", 9999, 0)

    now_ms = int(time.time() * 1000)
    s = quota.audit_period_totals(
        now_ms - 3_600_000, now_ms + 1000, filter_model="qwen",
    )
    assert s["request_count"] == 1
    assert s["total_tokens"] == 1000


# ── P3.5.93 (6/23 鸿波): web 编辑 UI 后端 CRUD + ruamel comment preserve ─


_YAML_WITH_COMMENTS = """defaults:
  per_user:
    # BL-FIX38 (5/10): demo/调试 100K/min 太低
    # 5/9 前所有 chat 挂 dev-user 不限速
    tokens_per_minute: 3000000
    tokens_per_day: 300000000
  per_model:
    Qwen3.6-Flash:
      tokens_per_day: 5000000
overrides:
  users:
    # 5/24 BL-CHENHONGBO-DEV-QUOTA: 鸿波 sysadmin 主开发 临时 100M
    chenhongbo@ffcs.cn:
      tokens_per_minute: 30000000
      tokens_per_day: 300000000
"""


def test_p3_5_93_ruamel_preserves_comments_on_write(tmp_path: Path) -> None:
    """治本 6 周 bug: pyyaml safe_dump 吹掉 yaml comments.

    ruamel round_trip 写后 yaml 注释还在. 真因 audit-5 (6/23 鸿波 catch).
    """
    _write_quotas_yaml(tmp_path, _YAML_WITH_COMMENTS)
    ok, msg = quota.put_dept_override("研发部", 100_000_000)
    assert ok, msg

    result = (tmp_path / "quotas.yaml").read_text(encoding="utf-8")
    # 4 行历史 BL-* 注释全保留
    assert "BL-FIX38 (5/10)" in result
    assert "5/9 前所有 chat" in result
    assert "BL-CHENHONGBO-DEV-QUOTA" in result
    # 新写的部门 override 也在
    assert "研发部" in result
    assert "100000000" in result


def test_p3_5_93_get_full_config_dict(tmp_path: Path) -> None:
    """读完整 config 给 admin UI."""
    _write_quotas_yaml(tmp_path, _YAML_WITH_COMMENTS)
    cfg = quota.get_full_config_dict()
    # 是纯 dict (不是 CommentedMap), 给 FastAPI JSON 序列化
    assert isinstance(cfg, dict)
    assert cfg["defaults"]["per_user"]["tokens_per_minute"] == 3000000
    assert cfg["overrides"]["users"]["chenhongbo@ffcs.cn"]["tokens_per_day"] == 300000000


def test_p3_5_93_update_default_per_user(tmp_path: Path) -> None:
    _write_quotas_yaml(tmp_path, _YAML_WITH_COMMENTS)
    ok, msg = quota.update_default_per_user(5_000_000, 500_000_000)
    assert ok, msg

    cfg = quota.load_quota_config()
    assert cfg.default_user.tokens_per_minute == 5_000_000
    assert cfg.default_user.tokens_per_day == 500_000_000


def test_p3_5_93_put_and_delete_per_model(tmp_path: Path) -> None:
    ok, _ = quota.put_per_model("MyModel-X", 10_000_000)
    assert ok
    cfg = quota.load_quota_config()
    assert cfg.model_quotas["MyModel-X"].tokens_per_day == 10_000_000

    ok, _ = quota.delete_per_model("MyModel-X")
    assert ok
    cfg = quota.load_quota_config()
    assert "MyModel-X" not in cfg.model_quotas


def test_p3_5_93_delete_per_model_idempotent(tmp_path: Path) -> None:
    ok, _ = quota.delete_per_model("不存在")
    assert ok  # idempotent — 不抛, 返 success


def test_p3_5_93_put_and_delete_user_override(tmp_path: Path) -> None:
    ok, _ = quota.put_user_override("alice@x.com", 2_000_000, 200_000_000)
    assert ok
    cfg = quota.load_quota_config()
    assert cfg.user_overrides["alice@x.com"].tokens_per_minute == 2_000_000
    assert cfg.user_overrides["alice@x.com"].tokens_per_day == 200_000_000

    ok, _ = quota.delete_user_override("alice@x.com")
    assert ok
    cfg = quota.load_quota_config()
    assert "alice@x.com" not in cfg.user_overrides


def test_p3_5_93_put_user_override_rejects_bad_email(tmp_path: Path) -> None:
    ok, msg = quota.put_user_override("not-an-email", 1000, 1000)
    assert not ok
    assert "email" in msg.lower() or "格式" in msg


def test_p3_5_93_put_user_override_rejects_negative(tmp_path: Path) -> None:
    ok, msg = quota.put_user_override("a@x.com", -1, 1000)
    assert not ok
    assert "负" in msg


def test_p3_5_93_put_and_delete_dept_override(tmp_path: Path) -> None:
    ok, _ = quota.put_dept_override("市场部", 50_000_000)
    assert ok
    cfg = quota.load_quota_config()
    # overrides.departments 写入 department_quotas (load_quota_config 把 defaults
    # 和 overrides 合并, override 后写覆盖 default)
    assert cfg.department_quotas["市场部"].tokens_per_day == 50_000_000

    ok, _ = quota.delete_dept_override("市场部")
    assert ok


def test_p3_5_93_put_per_department_default(tmp_path: Path) -> None:
    ok, _ = quota.put_per_department("人力资源部", 5_000_000)
    assert ok
    cfg = quota.load_quota_config()
    assert cfg.department_quotas["人力资源部"].tokens_per_day == 5_000_000


def test_p3_5_93_old_update_department_quota_still_works(tmp_path: Path) -> None:
    """老接口 BL-D9 5/2 兼容 — manager UI /api/quota/department/{dept} 仍调."""
    assert quota.update_department_quota("研发部", 100_000_000) is True
    cfg = quota.load_quota_config()
    assert cfg.department_quotas["研发部"].tokens_per_day == 100_000_000
