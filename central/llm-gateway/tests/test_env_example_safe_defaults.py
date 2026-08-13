""".env.example 不许把员工电脑默认暴露到局域网。

# 病历 (8/13)

5/6 安全 P0 G1 把 gateway 的**代码默认值**改成 `127.0.0.1`
(app.py: `os.environ.get("HOST", "127.0.0.1")`, 附注释"防员工电脑跑 gateway 时
同公司局域网扫端口蹭 quota")。

但 `.env.example` 里还写着 `HOST=0.0.0.0`。而所有人都是 `cp .env.example .env`
起步的 —— env 一旦有值, 代码默认值就永远走不到。**那次修复对新装的人零效果。**

实测: 鸿波本机 `.env:45` 就是 `HOST=0.0.0.0`, 每次手动启动都刷这条警告:

    [catfish] ⚠️ HOST=0.0.0.0 — gateway 暴露到所有网卡 (局域网可访问).
              仅服务器部署用. 员工电脑应改回 127.0.0.1.

同一次修复里 `central/skills-hub/.env.example` 改对了 (`HOST=127.0.0.1` + 说明),
这份漏了。而症状被记成"已知坑"而不是 bug —— docs/CATFISH-DEV-PITFALLS.md:203
把"手动启动是 0.0.0.0 / launchd 是 127.0.0.1"写进了对照表; launchd plist 5/23
单独改回 127.0.0.1, 手动这条路没人管。

# 这个文件钉什么

模板里的**安全默认值**。不是"文档写对", 是"照着抄的人默认就是安全的"。

服务器部署要 0.0.0.0 是合法需求 —— 那是部署文档 (DEPLOYMENT-50-USERS.md) 的事,
不该由员工电脑的模板承担。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _env_examples() -> list[Path]:
    """员工电脑上会跑的服务的 .env.example。

    只管这几个 —— delivery/ 下的私有部署样例、docs 里的服务器部署说明
    本来就该是 0.0.0.0, 不在管辖范围。
    """
    return [
        p for p in (
            _ROOT / "central/llm-gateway/.env.example",
            _ROOT / "central/skills-hub/.env.example",
        ) if p.exists()
    ]


def test_at_least_one_template_found():
    """定位失败要红, 不能静默 skip。

    一个"找不到文件所以通过"的安全测试比没有更糟 —— 它在报表上是绿的。
    (今天刚踩过: 跨端契约那条因为路径写错, 一直 skip 却看着像跑过了。)
    """
    found = _env_examples()
    assert found, (
        f"一个 .env.example 都没找到 (_ROOT={_ROOT}) —— 路径推导错了, "
        "这个测试正在假装通过"
    )


@pytest.mark.parametrize("path", _env_examples(), ids=lambda p: p.parent.name)
def test_host_default_is_loopback(path: Path):
    """★ 模板里的 HOST 必须是回环地址。"""
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = re.match(r"^\s*HOST\s*=\s*(\S*)", line)
        if not m:
            continue
        host = m.group(1).strip().strip('"').strip("'")
        assert host in ("127.0.0.1", "localhost", "::1"), (
            f"{path.relative_to(_ROOT)}:{i}  HOST={host}\n"
            "员工电脑照抄这份模板就会把网关暴露到局域网 —— 同网段任何人都能打, "
            "而网关手里有上游 API key 和内网模型地址。\n"
            "服务器部署要 0.0.0.0 请写在部署文档里, 不要放进员工模板。"
        )
        return
    pytest.fail(f"{path.relative_to(_ROOT)} 里没有 HOST= 行 —— 模板结构变了, 这条要更新")
