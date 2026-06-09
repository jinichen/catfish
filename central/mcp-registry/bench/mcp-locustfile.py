"""BL-MCP-BENCH locustfile — mcp-registry 真热路径 (6/9 鸿波 B 方案).

# 任务比重 (按真 prod 负载估)

- 60% GET /v1/mcp/registry — 员工开 web settings 页, dept 过滤, join 订阅状态
- 20% GET /v1/mcp/manifest/{id} — 单 connector 详情
- 15% GET /v1/mcp/subscribed — 我的订阅
- 5%  GET /health — sanity

# 真验证目标

P99 < 100ms — 防 manifests join subscriptions 出 N+1 query.
mcp-registry/manifests/ 现 4 个 yaml (jira/gitlab/filesystem/time), prod 部署
后客户可能加到几十个, 加上 1000 emp × 平均 5 订阅 = 5000 row PG join, 这里
50 user × 1 min smoke 不会触发 worst case 但能看 baseline.

# 鉴权

mcp-registry 信任 gateway 注入的 X-Catfish-User-Sub / X-Catfish-User-Dept header.
bench 直接发 (跟 prod 没区别, gateway 已经验过 JWT 注入).
"""
from __future__ import annotations

import random
from locust import HttpUser, between, task


# bench 模拟员工身份 (mcp-registry 拿 sub 做 dept 过滤 + 订阅 join)
_FAKE_USERS = [
    ("alice@bench.dev", "engineering"),
    ("bob@bench.dev", "engineering"),
    ("carol@bench.dev", "sales"),
    ("dave@bench.dev", "legal"),
    ("eve@bench.dev", "marketing"),
]

# manifests/ 里 4 个 connector id (跟 yaml 文件名对齐)
_CONNECTOR_IDS = ["jira", "gitlab", "filesystem", "time"]


class CatfishMcpRegistryUser(HttpUser):
    wait_time = between(0.5, 2)  # 模拟员工 settings 页停留 + 操作间隔

    def on_start(self):
        sub, dept = random.choice(_FAKE_USERS)
        self.user_sub = sub
        self.user_dept = dept

    def _headers(self) -> dict[str, str]:
        return {
            "X-Catfish-User-Sub": self.user_sub,
            "X-Catfish-User-Dept": self.user_dept,
        }

    @task(60)
    def list_registry(self):
        """真热路径 — 员工开 settings 页拉 connector 列表 + 订阅状态."""
        with self.client.get(
            "/v1/mcp/registry",
            headers=self._headers(),
            catch_response=True,
            name="GET /v1/mcp/registry",
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")

    @task(20)
    def get_manifest(self):
        connector_id = random.choice(_CONNECTOR_IDS)
        with self.client.get(
            f"/v1/mcp/manifest/{connector_id}",
            headers=self._headers(),
            catch_response=True,
            name="GET /v1/mcp/manifest/{id}",
        ) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code == 404:
                # bench 数据 / manifests 不齐时可能 404, 算 success 防误报 fail
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")

    @task(15)
    def list_subscribed(self):
        """我的订阅 — 空表也要返 [] 不挂."""
        with self.client.get(
            "/v1/mcp/subscribed",
            headers=self._headers(),
            catch_response=True,
            name="GET /v1/mcp/subscribed",
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")

    @task(5)
    def health(self):
        with self.client.get(
            "/health",
            catch_response=True,
            name="GET /health",
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"status {r.status_code}")
