"""BL-BROKER-BENCH locustfile — secret-broker PgEncryptedStorage 真热路径.

# 真验证目标

1. **GET /v1/secret/{ref} 路径**: PG SELECT + AES-256-GCM 解密.
   prod 真热路径 (gateway 每次调 MCP server 都拿 OAuth token, 假设没 cache).
2. **threading.Lock 多 worker 不卡**: PgEncryptedStorage 用了 lock 串行化
   psycopg connection, 加 worker 应该接近线性 scale.
3. **AES-GCM 硬件加速真 < 1ms**: 单次解密很快, P99 应 < 50ms.

# 任务比重 (按真 prod 估)

- 70% GET /v1/secret/{ref} — 真热 (gateway pull token)
- 20% POST /v1/secret — 写 (mcp-registry OAuth callback)
- 5%  GET /v1/secret/{ref}/exists — 偶尔 sanity
- 5%  DELETE /v1/secret/{ref} — 员工取消订阅时

# 起始 seed

locust on_start 每 user 写 5 个 secret (oauth:jira:{user}/{idx} 格式), 后续
GET 走 seeded refs. 不依赖外部 seed 脚本.
"""
from __future__ import annotations

import random
import secrets
from locust import HttpUser, between, task


def _fake_token() -> str:
    """模拟 OAuth refresh token, ~40 char hex."""
    return "gho_" + secrets.token_hex(20)


class CatfishSecretBrokerUser(HttpUser):
    wait_time = between(0.1, 1)  # gateway 真热, 间隔短

    def on_start(self):
        # 每 user 独立 sub, 防 ref 冲突
        self.user_sub = f"bench-user-{secrets.token_hex(4)}@bench.dev"
        self.seeded_refs: list[str] = []
        # seed 5 个 secret (跟 prod 员工平均订阅数对齐)
        for i in range(5):
            ref = f"oauth:bench-mcp-{i}:{self.user_sub}"
            with self.client.post(
                "/v1/secret",
                headers=self._headers(),
                json={"ref": ref, "value": _fake_token()},
                catch_response=True,
                name="seed POST /v1/secret",
            ) as r:
                if r.status_code in (200, 201):
                    self.seeded_refs.append(ref)
                    r.success()
                else:
                    r.failure(f"seed status {r.status_code}")

    def _headers(self) -> dict[str, str]:
        return {"X-Catfish-User-Sub": self.user_sub}

    @task(70)
    def get_secret(self):
        """真热路径 — gateway 拿 OAuth token 调 MCP server."""
        if not self.seeded_refs:
            return
        ref = random.choice(self.seeded_refs)
        with self.client.get(
            f"/v1/secret/{ref}",
            headers=self._headers(),
            catch_response=True,
            name="GET /v1/secret/{ref}",
        ) as r:
            if r.status_code == 200 and r.json().get("value"):
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")

    @task(20)
    def post_secret(self):
        """写 — mcp-registry OAuth callback. 模拟 token rotation."""
        ref = f"oauth:bench-mcp-{random.randint(0, 9)}:{self.user_sub}"
        with self.client.post(
            "/v1/secret",
            headers=self._headers(),
            json={"ref": ref, "value": _fake_token()},
            catch_response=True,
            name="POST /v1/secret",
        ) as r:
            if r.status_code in (200, 201):
                if ref not in self.seeded_refs:
                    self.seeded_refs.append(ref)
                r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")

    @task(5)
    def exists_secret(self):
        if not self.seeded_refs:
            return
        ref = random.choice(self.seeded_refs)
        with self.client.get(
            f"/v1/secret/{ref}/exists",
            headers=self._headers(),
            catch_response=True,
            name="GET /v1/secret/{ref}/exists",
        ) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"status {r.status_code}")

    @task(5)
    def delete_secret(self):
        """员工取消订阅. 删后写回 (保持 seeded_refs 不空)."""
        if not self.seeded_refs:
            return
        ref = self.seeded_refs.pop()
        with self.client.delete(
            f"/v1/secret/{ref}",
            headers=self._headers(),
            catch_response=True,
            name="DELETE /v1/secret/{ref}",
        ) as r:
            if r.status_code in (200, 204, 404):
                # 立刻写回防 GET 找不到
                self.client.post(
                    "/v1/secret",
                    headers=self._headers(),
                    json={"ref": ref, "value": _fake_token()},
                    name="seed POST /v1/secret",
                )
                self.seeded_refs.append(ref)
                r.success()
            else:
                r.failure(f"status {r.status_code}")
