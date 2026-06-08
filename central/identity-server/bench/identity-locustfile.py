"""BL-F11 identity-server locust tasks — 模拟 N 个 service / gateway worker 同时打 identity.

# 怎么跑

```bash
# headless
locust -f bench/identity-locustfile.py \
  --host http://localhost:8998 \
  --headless \
  --users 1000 \
  --spawn-rate 10 \
  --run-time 5m \
  --html bench/report-identity-1000.html
```

# 任务权重 (按 prod 真实 identity 流量分布)

- 70% POST /token  (client_credentials grant) — 主流量, bcrypt verify + JWT RSA sign
- 25% GET /.well-known/jwks.json — gateway worker 每 1h 拉一次 + 启动拉
- 5% GET /healthz — sanity baseline

# 为啥不测 /userinfo / authorization_code

- /userinfo 要先有 access_token, 加状态依赖, 复杂度 ↑ 收益 ↓
- authorization_code 要走 /authorize HTML form + cookie session, locust 模拟麻烦
- client_credentials 已经撕到 identity 真瓶颈 (bcrypt 12-round + JWT sign), 够了

# 真瓶颈预测

bcrypt 12 rounds ≈ 250-400ms / verify (Python C ext, 释放 GIL).
identity 单 worker 单 vCPU 极限 ≈ 1000 / 300 = 3-4 verify/s.
4 worker 多核 ≈ 12-16 verify/s. 1000 user 持续打 → 在 bcrypt 上排队.
**如果 P99 > 30s, 不是 identity bug, 是 bcrypt 12 round 在 prod 物理上限**.

# 环境 var

- BENCH_CLIENT_SECRET=bench-secret-2026 (跟 clients.bench.yaml 对齐)
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, task


# 跟 bench/clients.bench.yaml 一致 — 5 个 service client 共享一个 secret
BENCH_CLIENT_SECRET = os.environ.get("BENCH_CLIENT_SECRET", "bench-secret-2026")
BENCH_CLIENT_IDS = [
    "bench-client-1",
    "bench-client-2",
    "bench-client-3",
    "bench-client-4",
    "bench-client-5",
]

# scope 多样 — 模拟真 prod 不同服务请求不同 scope
SAMPLE_SCOPES = [
    "chat.completions",
    "chat.completions audit.write",
    "chat.completions tools.invoke",
    "chat.completions audit.write tools.invoke",
]


class CatfishServiceClient(HttpUser):
    """1 个 locust 用户 = 1 个服务进程 (hermes-cli / gateway / etc) 在 prod."""

    # 真服务不会狂打 — token 有效期通常 1h, 1 个服务 1h 才 refresh 一次
    # bench 故意压: 0.5-3s 间隔 = 模拟极端高峰 (多服务同时启动 / token 同时过期)
    wait_time = between(0.5, 3)

    @task(70)
    def get_token_client_credentials(self):
        """主任务 — POST /token client_credentials grant.

        Server 跑 bcrypt verify (12 round, ~300ms) + JWT RSA sign + PG audit log.
        这条路径是 identity 在 prod 最热的真路径.
        """
        client_id = random.choice(BENCH_CLIENT_IDS)
        scope = random.choice(SAMPLE_SCOPES)
        with self.client.post(
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": BENCH_CLIENT_SECRET,
                "scope": scope,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            name="POST /token (client_credentials)",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}: {resp.text[:200]}")
            else:
                # 验返了 access_token
                try:
                    body = resp.json()
                    if "access_token" not in body:
                        resp.failure(f"no access_token in response: {body}")
                    else:
                        resp.success()
                except Exception as e:
                    resp.failure(f"json parse: {e}")

    @task(25)
    def get_jwks(self):
        """gateway worker 启动 + 缓存过期时拉 JWKS 验 JWT 签名.
        无 auth, 纯 JSON 序列化 + HTTP. 应该 P50 < 5ms.
        """
        with self.client.get(
            "/.well-known/jwks.json",
            name="GET /.well-known/jwks.json",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()

    @task(5)
    def get_healthz(self):
        """liveness — 跑 sanity 看负载下 identity 是不是还活着."""
        with self.client.get(
            "/healthz",
            name="GET /healthz",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()
