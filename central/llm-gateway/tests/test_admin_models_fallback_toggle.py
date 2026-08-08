"""失败切换 (fallback) 全局开关的上报 —— 从 test_admin_models_api.py 拆出来 (8/8).

拆的原因很实在: 那个文件 790 行, 我把这条测试从 11 行改写到 43 行之后
**822 行, 越过了 CLAUDE.md 的 800 行红线**。而它本来也不属于那个文件的
主题 (模型 CRUD 的护栏), 它验的是"接口有没有如实把全局开关的状态告诉界面"。

为什么这条测试值得存在: 管理员会在 /admin/models 上认真配一条 fallback 链,
而 auto_fallback 全局关着时那条链**永远不执行**。配了不生效且没有任何提示,
是最难查的一类 —— 所以接口必须把这个状态报给界面, 界面才能挂那条橙色横幅。

fixture 从原文件 import 复用 (tests/ 是包, 有 __init__.py)。
"""
from __future__ import annotations

from .test_admin_models_api import client  # noqa: F401  pytest fixture


def test_列表要告诉界面_fallback_全局开没开(client, monkeypatch, tmp_path):
    """不告诉界面的话, 管理员会认真配一条永不执行的链 —— 配置了不生效
    且无任何提示, 是最难查的一类。

    8/8: 原来直接断言 `is False`, 注释写「models.yaml 没开且无 env → 应为 False」。
    但这个 fixture **没有** 隔离 CATFISH_CONFIG, 读的是仓库里那份生产
    config/models.yaml —— 于是 8/8 把生产配置的 auto_fallback 打开时这条当场红。

    它测的本来就不是"生产配置里这个值是几", 而是"接口有没有如实把这个值
    告诉界面, 且 env 能覆盖 yaml"。所以改成自带一份 yaml, 两个方向各测一次,
    跟生产配置解耦。
    """
    from catfish_gateway import config as C

    def _yaml(flag: str) -> None:
        p = tmp_path / f"models-{flag}.yaml"
        p.write_text(
            f"version: 1\nauto_fallback: {flag}\nmodels:\n"
            "  - name: m1\n    tier: public\n"
            '    display_name: "m1"\n'
            "    upstream:\n      model: openai/x\n      api_key_env: K\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CATFISH_CONFIG", str(p))
        C._CACHE = None
        C._CACHE_STAMP = None
        C._CACHE_CHECKED_AT = 0.0

    c, *_ = client
    monkeypatch.delenv("CATFISH_AUTO_FALLBACK", raising=False)

    _yaml("false")
    body = c.get("/api/admin/models").json()
    assert "auto_fallback" in body, "接口必须把这个值报给界面"
    assert body["auto_fallback"] is False, "yaml 关且无 env → False"

    _yaml("true")
    assert c.get("/api/admin/models").json()["auto_fallback"] is True, "yaml 开 → True"

    # env 覆盖 yaml (跟 fallback.py:425 的判定顺序一致)
    _yaml("false")
    monkeypatch.setenv("CATFISH_AUTO_FALLBACK", "1")
    assert c.get("/api/admin/models").json()["auto_fallback"] is True, "env 应能覆盖 yaml"
