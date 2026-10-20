"""出厂模型只在全新库上播种一次 (10/21).

鸿波 10/21: 每次升级中央端, 已经删掉的供应商和模型又回来了。原来每次启动都按
ON CONFLICT DO NOTHING 补齐, 删掉的模型库里没有 → 每次重启都插回来, 随后拆供应商
又把删掉的供应商建回来。现在看 gateway_config_meta.seeded_at (迁移 20261021_012)。

真 SQL 那一半 (迁移补标 + 删掉后再"升级"一次) 在 test_provider_split_real_pg.py 末尾。
"""
from __future__ import annotations


def test_播种过的库不再补出厂模型_删掉的不会回来(monkeypatch):
    """10/21 鸿波: 每次升级中央端, 删掉的供应商和模型又回来了.

    原来每次启动都按 ON CONFLICT DO NOTHING 补齐, 删掉的模型库里没有 → 每次重启都插回来。
    现在 seeded_at 有值就一条都不插。
    """
    from catfish_gateway import model_store as MS

    sqls: list[str] = []

    class _Cur:
        rowcount = 0

        def execute(self, sql, args=None):
            sqls.append(sql)

        def fetchone(self):
            return ("2026-10-20 00:00:00+00",)  # 早就播过了

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(MS, "is_enabled", lambda: True)
    monkeypatch.setattr(MS, "_conn", lambda: _Conn())

    n = MS.seed_from_yaml([{"name": "被删掉的出厂模型", "default": True}])

    assert n == 0
    assert not [q for q in sqls if "INSERT" in q], "播种过的库又插了出厂模型 —— 删掉的会在升级后回来"
    assert "FOR UPDATE" in sqls[0], "4 个 worker 同时启动, 查标记必须加锁, 否则可能播两次"
