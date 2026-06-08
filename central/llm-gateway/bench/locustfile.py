"""Locust 任务定义 — BL-F10 模拟 N 个员工同时打 gateway.

# 怎么跑

```bash
# headless (CI / 批量)
locust -f bench/locustfile.py \
  --host http://localhost:8999 \
  --headless \
  --users 100 \
  --spawn-rate 10 \
  --run-time 10m \
  --html bench/report-100.html \
  --csv bench/csv-100

# web UI (人工跑, http://localhost:8089)
locust -f bench/locustfile.py --host http://localhost:8999
```

# 任务权重 (按真实员工行为分布)

- 60% chat completion (streaming, 真长 conn)
- 20% quota/audit/me 查询 (短轮询)
- 15% catalog (启动 / 切 model 拉)
- 5% advisory feed (banner pull)

# 各员工独立 token

让 gateway 真按 user 算 quota — 找 quota PG 写 race / lost update.

# 环境 var

- CATFISH_BENCH_TOKEN_PREFIX=dev-token-bench (gateway .env 里要允许这个 prefix)
- CATFISH_BENCH_MODEL=catfish-mock-bench (在 models.bench.yaml 里定义的 mock model)
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, task


MODEL_NAME = os.environ.get("CATFISH_BENCH_MODEL", "catfish-mock-bench")

# 跟 bench/dev_users.bench.yaml 一致. 5 个 token = 5 个虚拟员工.
# 100 locust 用户 ramp-up = 100 / 5 = 20 并发 / 真员工 → 撕 quota PG 写 race.
BENCH_TOKENS = [
    "bench-token-admin",
    "bench-token-alice",
    "bench-token-bob",
    "bench-token-carol",
    "bench-token-dave",
]

# 8 类员工 prompt — 长度 / 复杂度分布跟真员工请求贴近
SAMPLE_PROMPTS = [
    "你好",
    "帮我写一段周报开头",
    "今天有什么新邮件",
    "把这个 5 千字 doc 总结一下要点",
    "明天下午会议提醒我",
    "查一下我上周跟王主任的对话",
    "帮我起草给客户的拜年邮件, 正式语气",
    "解释一下 manifesto 公理 4 的物理意义",
]


class CatfishEmployee(HttpUser):
    """1 个 locust 用户 = 1 个员工 SSO session."""

    # 真员工不会狂打 — 思考 + 打字 + 读 response 之间 2-15s gap
    wait_time = between(2, 15)

    def on_start(self):
        """启动: 随机选 1 个 bench token (5 个池). 多 locust user 共享一个 bench
        员工 = 让 quota PG 写在同 user_email 上高并发 = 撕 race."""
        self.token = random.choice(BENCH_TOKENS)
        self.client.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @task(60)
    def chat_streaming(self):
        """主任务 — chat completion streaming (长 conn 看 gateway buffer)."""
        prompt = random.choice(SAMPLE_PROMPTS)
        with self.client.post(
            "/v1/chat/completions",
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 100,
            },
            stream=True,  # locust 真接 streaming
            catch_response=True,
            name="POST /v1/chat/completions [stream]",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}: {resp.text[:200]}")
                return
            # 真消费 SSE stream (测 gateway streaming 转发 throughput)
            chunks = 0
            try:
                for line in resp.iter_lines():
                    if line:
                        chunks += 1
                if chunks < 5:
                    resp.failure(f"too few chunks: {chunks}")
                else:
                    resp.success()
            except Exception as e:
                resp.failure(f"stream read err: {e}")

    @task(20)
    def my_quota(self):
        # catch_response=True: locust 要 with-block 必须显式 catch (6/8 鸿波修).
        with self.client.get(
            "/api/quota/me",
            name="GET /api/quota/me",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()

    @task(10)
    def my_audit(self):
        with self.client.get(
            "/api/audit/me",
            name="GET /api/audit/me",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()

    @task(5)
    def catalog(self):
        with self.client.get(
            "/v1/catalog",
            name="GET /v1/catalog",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()

    @task(5)
    def advisory(self):
        with self.client.get(
            "/api/advisory/feed.json",
            name="GET /api/advisory/feed.json",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
            else:
                resp.success()
