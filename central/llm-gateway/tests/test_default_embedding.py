"""默认向量模型 —— 一个 mode 一个默认 (9/23).

9/23 之前向量模型由 roles.yaml 的 `embedding` 角色单独指定, 跟模型页的「默认」
徽章是两份真相。现在只有模型上的 `default` 标志, 对话一个、向量一个。

从 test_admin_models_api.py 拆出来 (那边到了 854 行, 红线 800); 夹具和
`_model` 直接从那边拿, 两边测的是同一个接口。
"""
from __future__ import annotations

from .test_admin_models_api import _model, client  # noqa: F401  (client 是 pytest 夹具)


def test_默认向量模型有替代者时不能删(client):
    """★★★ 8/14 事故的那条: 删掉向量模型 → /v1/embeddings 404 → Companion 静默退回
    本机 ONNX → Windows 没编 ort, 等于没有向量。对话默认删了能自动换人, 向量不行 ——
    换向量模型 = 已算好的向量全部作废, 得由人明确做。

    9/23 鸿波: "如果确实没有向量模型为什么不能删" —— 唯一的向量模型**可以删**
    (不用中央向量是合法选择), 只拦"有别的向量模型但默认在它身上"这一种。
    """
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding", default=True))
    c.put("/api/admin/models/embed2", json=_model("embed2", mode="embedding"))
    r = c.delete("/api/admin/models/embed")
    assert r.status_code == 400 and "embed2" in r.json()["detail"]
    assert "embed" in store
    # 反过来: embed2 不是默认, 能删
    assert c.delete("/api/admin/models/embed2").status_code == 200


def test_唯一的向量模型可以删(client):
    """不用中央向量是合法的部署选择 —— 网关把后果说清楚, 但不拦。"""
    c, store, *_ = client
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding"))
    r = c.delete("/api/admin/models/embed")
    assert r.status_code == 200, r.text
    assert "embed" not in store
    assert store["m1"]["default"] is True, "对话默认不受影响"


def test_换默认向量模型要确认重建索引(client):
    c, store, *_ = client
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding", default=True))
    r = c.put("/api/admin/models/embed2", json=_model("embed2", mode="embedding", default=True))
    assert r.status_code == 400 and "重建索引" in r.json()["detail"]
    assert store["embed"]["default"] is True and "embed2" not in store

    r = c.put(
        "/api/admin/models/embed2",
        json={**_model("embed2", mode="embedding", default=True), "confirm_reindex": True},
    )
    assert r.status_code == 200, r.text
    assert store["embed2"]["default"] is True
    assert store["embed"]["default"] is False, "同 mode 的旧默认要被清掉"
    assert "confirm_reindex" not in store["embed2"], "确认位不是模型字段, 不能进库"
    # 现在 embed 不是默认了, 能删
    assert c.delete("/api/admin/models/embed").status_code == 200


def test_设对话默认不会清掉向量默认(client):
    c, store, *_ = client
    c.put("/api/admin/models/embed", json=_model("embed", mode="embedding", default=True))
    c.put("/api/admin/models/m1", json=_model("m1", default=True))
    c.put("/api/admin/models/m2", json=_model("m2", default=True))
    assert store["m1"]["default"] is False and store["m2"]["default"] is True
    assert store["embed"]["default"] is True, "跨 mode 不该互相清"


