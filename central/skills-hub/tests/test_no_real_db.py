"""11/14: 普通测试绝不能连到开发机真库 (CATFISH_DB_URL 来自 .env)。第一条就查, 放在最前面跑也查。"""
from catfish_skills_hub import storage


def test_普通测试不连真库(client=None):
    assert storage._pg_url() is None, "测试进程里 CATFISH_DB_URL 不该有值 —— 会往开发机真库写假数据"
