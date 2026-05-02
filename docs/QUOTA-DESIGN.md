# 鲶鱼 · Quota 配额管理设计 v0.1

> **版本**: v0.1, 2026-05-03 起草 (五一假期 Day 7)
> **状态**: spec + middleware + 单元测试 ship, 部门级配额生产 Phase 2 (Q3 2026)
> **决策签名**: 鸿波 5/3 拍板 — 三维限流 (per user / per model / per department), sliding window, sqlite 存

---

## 1. 使命

> 客户 IT 拿鲶鱼第二个问题 — "**LLM token 用量怎么控?**" — 鲶鱼答 "三维 quota: 个人 / 模型 / 部门, sliding window 限流, 超额返 429".

商业角度: **客户用自己的 LLM**, 但 quota 还是要 — 因为
- 公网 LLM (qwen-flash / gemini) 按 token 收费, 员工乱用部门预算超
- 私有 LLM 也有并发限制 (推理资源有限), 高峰期 quota 必要
- 合规要求: 哪个员工 / 部门用了多少 token, 客户 IT 必须能看 + 限

---

## 2. 三维 Quota 模型 (鸿波 5/3 拍板)

```
┌────────────────────────────────────────────────────────────┐
│ 维度 1: per-user (员工个人)                                  │
│   alice 每分钟 ≤ 10 万 token / 每天 ≤ 100 万 token          │
│   防员工乱跑大 prompt 烧公网 token                            │
│                                                              │
│ 维度 2: per-model (模型粒度)                                  │
│   catfish-public-gemini-flash 全员每天 ≤ 1 千万 token        │
│   catfish-private-main 不限 (内网, 自己付电费)                │
│   防个别员工把公网额度独吞                                    │
│                                                              │
│ 维度 3: per-department (部门聚合)                             │
│   研发部 每天 ≤ 5 千万 token (manager 在仪表盘改)             │
│   销售部 每天 ≤ 1 千万 token                                  │
│   manager 在自己部门内重新分配 quota 给员工                   │
└────────────────────────────────────────────────────────────┘

任意一维超过 → 拒绝 (429), 提示员工换模型 / 等下分钟 / 找 manager 升级 quota.
```

**Sliding window (滑动窗口)**: 每分钟和每天各一个窗口, sqlite 存近 1 小时 / 近 7 天的 token 用量, 实时累加.

---

## 3. Quota 配置 (yaml)

`central/llm-gateway/config/quotas.yaml`:

```yaml
# 默认 quota (没显式配置的用户走这个)
defaults:
  per_user:
    tokens_per_minute: 100000   # 10 万
    tokens_per_day: 1000000     # 100 万
  per_model:
    catfish-public-qwen-flash:
      tokens_per_day: 10000000  # 1 千万
    catfish-public-gemini-flash:
      tokens_per_day: 5000000   # 500 万
    catfish-public-gemini-pro:
      tokens_per_day: 2000000   # 200 万
    # private model 不限 (省略)
  per_department:
    "研发部":
      tokens_per_day: 50000000  # 5 千万
    "销售部":
      tokens_per_day: 10000000  # 1 千万

# 个别用户 override (manager 在 UI 改)
overrides:
  users:
    "ceo@ffcs.cn":
      tokens_per_minute: 500000
      tokens_per_day: 5000000
  departments:
    "研发部":
      tokens_per_day: 80000000  # 临时升级
```

---

## 4. Sliding window 实现

**SQLite 存 token 用量事件** (`~/.catfish/quota.db`):

```sql
CREATE TABLE quota_events (
  ts INTEGER NOT NULL,           -- unix timestamp ms
  user_email TEXT NOT NULL,
  department TEXT NOT NULL,
  model TEXT NOT NULL,
  tokens_in INTEGER NOT NULL,
  tokens_out INTEGER NOT NULL
);
CREATE INDEX idx_quota_ts_user ON quota_events(ts, user_email);
CREATE INDEX idx_quota_ts_model ON quota_events(ts, model);
CREATE INDEX idx_quota_ts_dept ON quota_events(ts, department);
```

**查询** (per-user 1 分钟内):
```sql
SELECT SUM(tokens_in + tokens_out) FROM quota_events
WHERE user_email = ? AND ts >= ?  -- ? = now - 60s
```

每分钟 1 query (通过限流时检查), 老数据 1h 后异步清理. 每个 quota 维度 < 1ms 查询.

---

## 5. 超额行为

API 返 HTTP 429:

```json
{
  "error": "quota_exceeded",
  "dimension": "per_user_minute",
  "current_usage": 105000,
  "limit": 100000,
  "reset_at": 1747005060,
  "message": "alice@ffcs.cn 每分钟 quota 超 (105K/100K). 等 30 秒后重试, 或换 catfish-private-main (内网不限)."
}
```

Companion 端友好提示:
- "你这分钟用得太多, 30 秒后再试" (employee)
- "今天部门 quota 快满了 (4500万/5000万), 限 manager 来升级" (manager 看到)

---

## 6. Manager 改 quota (Phase 2.5 完整, 5/3 spec only)

`PATCH /v1/quota/department/{name}` (manager / admin):
```json
{
  "tokens_per_day": 80000000  # 临时升 5 千万 → 8 千万
}
```

写到 `quotas.yaml` overrides.departments. Audit log 记: "manager alice@ffcs.cn 把 研发部 quota 5000万 → 8000万".

5/3 ship 范围: spec + middleware 限流 + 个人 / 模型维度 endpoint. 部门 PATCH endpoint Phase 2 集成 manager UI 一起做.

---

## 7. 实施 (5/3 ship 范围)

### 7.1 quota.py (核心)

```python
@dataclass
class QuotaCheck:
    allowed: bool
    dimension: str           # per_user_minute / per_user_day / per_model_day / per_dept_day
    current: int
    limit: int
    reset_at: int

def check_quota(user_email: str, department: str, model: str, est_tokens: int) -> QuotaCheck:
    """请求来时调一次, 看是否拒绝.

    estimated tokens = max(prompt_tokens 估算, 1000). 真实 tokens 在 response 完才知道,
    estimated 用粗略估算 (1 token ≈ 4 字符).
    """
    now = time.time()
    config = load_quota_config()

    # 1. per-user 1 minute
    used_min = sum_tokens_since(user_email, now - 60)
    limit_min = config.per_user_for(user_email).tokens_per_minute
    if used_min + est_tokens > limit_min:
        return QuotaCheck(False, "per_user_minute", used_min, limit_min, int(now + 60))

    # 2. per-user 1 day
    used_day = sum_tokens_since(user_email, now - 86400)
    limit_day = config.per_user_for(user_email).tokens_per_day
    if used_day + est_tokens > limit_day:
        return QuotaCheck(False, "per_user_day", used_day, limit_day, int(now + 86400))

    # 3. per-model 1 day
    used_model = sum_model_tokens_since(model, now - 86400)
    limit_model = config.per_model_for(model).tokens_per_day
    if used_model + est_tokens > limit_model:
        return QuotaCheck(False, "per_model_day", used_model, limit_model, int(now + 86400))

    # 4. per-department 1 day
    used_dept = sum_dept_tokens_since(department, now - 86400)
    limit_dept = config.per_department_for(department).tokens_per_day
    if used_dept + est_tokens > limit_dept:
        return QuotaCheck(False, "per_dept_day", used_dept, limit_dept, int(now + 86400))

    return QuotaCheck(True, "", 0, 0, 0)


def record_usage(user_email: str, department: str, model: str, tokens_in: int, tokens_out: int):
    """请求完成后调一次, 写真实 token 用量到 sqlite."""
    db.insert("quota_events", ...)
```

### 7.2 middleware

`/v1/chat/completions` 入口加:

```python
@app.post("/v1/chat/completions")
async def chat(req: ChatReq, user = Depends(get_current_user)):
    est_tokens = estimate_tokens(req.messages)
    qc = check_quota(user.email, user.department, req.model, est_tokens)
    if not qc.allowed:
        raise HTTPException(429, detail={
            "error": "quota_exceeded",
            "dimension": qc.dimension,
            "current": qc.current,
            "limit": qc.limit,
            "reset_at": qc.reset_at,
            "message": friendly_quota_message(qc, user, req.model),
        })
    # 调 LLM
    response = await stream_llm(...)
    # 完成后记真实 token
    record_usage(user.email, user.department, req.model, response.usage.in, response.usage.out)
```

### 7.3 endpoint

- `GET /v1/quota/me` (employee+) — 自己今日 / 本分钟用量 + 上限
- `GET /v1/quota/department/{name}` (manager 限 managed_departments / admin) — 部门聚合
- `GET /v1/quota/all` (admin) — 全员

### 7.4 测试

- check_quota 每个维度
- record_usage 写入 sqlite
- 滑动窗口边界 (59 秒前的事件不该计入 1 分钟 quota)
- 超额返 429
- yaml overrides 优先于 defaults

---

## 8. Companion 仪表盘

现有 `QuotaCard.tsx` 增强 (5/3 ship 范围只动后端, UI 部分 Phase 2 跟 manager 工具一起做):
- employee: 显示自己今日 / 本分钟用量 + 距重置时间
- manager: 加部门聚合 + 子员工排行
- admin: 加全部门列表 + 全员排行

---

## 9. 5 月 demo 应对话术

客户问 "**LLM 用量怎么控?**":

> "鲶鱼三维 quota — **个人 / 模型 / 部门**.
>
> 个人: alice 每分钟 ≤ 10 万 token, 每天 ≤ 100 万 (你们 IT 在 yaml 配)
> 模型: catfish-public-gemini-flash 全员每天 ≤ 500 万 (公网 LLM 按 token 收, 限了别超预算)
> 部门: 研发部每天 ≤ 5 千万 (manager 在仪表盘改, audit 留痕)
>
> 任意一维超 → HTTP 429, 员工友好提示 ('换 catfish-private-main 内网不限', '等 30 秒'). 不是闷着拒.
>
> 滑动窗口 sqlite 存 (~/.catfish/quota.db), 每查 < 1ms. spec 在 docs/QUOTA-DESIGN.md, 五一已 MVP 实施."

---

## 10. 决策签名

> v0.1 = 2026-05-03 鸿波拍板.
> 决策点:
> 1. 三维 quota (per-user / per-model / per-department), 不做 per-company-overall (Phase 3 加)
> 2. Sliding window 1min / 1day, sqlite 存 (Phase 2 升级 Redis)
> 3. estimated tokens 粗估 (max(4 字符 = 1 token, 1000)), 真实用量响应完才记
> 4. 超额返 HTTP 429 + friendly_quota_message
> 5. yaml overrides 优先于 defaults
> 6. manager 改部门 quota 走 PATCH /v1/quota/department/{name} (5/3 spec, Phase 2 ship UI)

5/3 ship 范围: spec + quota.py 核心 + middleware + endpoint + 单元测试. 部门 PATCH UI 跟 RBAC manager 仪表盘一起 Phase 2 集成.
