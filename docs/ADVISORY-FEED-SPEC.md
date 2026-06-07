# Catfish Advisory Feed API Spec

> **目的**: 让 catfish 中央服务能 publish 安全建议 / 升级提示 / 配置推荐, 员工客户端**自己 pull**. 取代主流 fleet 的"IT 强制 push"模式.
>
> **manifesto 公理 4 落地**: 中央服务**物理上**没有 "push to device" API. Advisory Feed 是 pull-based, 员工有"忽略"选项.
>
> **跟 CATFISH-CENTRAL-MANIFESTO.md 联动**: 这是 manifesto 第 3 节场景 1 / 2 (员工离职 wipe / skill 漏洞处理) 的具体技术实现.
>
> **目标读者**: 后端开发 (Python FastAPI) + 前端 (React Tauri) + DevOps (admin UI)

---

## 1. 概述

### 1.1 跟主流 fleet 的本质区别

| 维度 | 主流 fleet (CrowdStrike / Intune) | catfish Advisory Feed |
|---|---|---|
| 控制方向 | Server **push** to device | Server **publish**, client **pull** |
| 强制性 | 强制 (员工不能拒绝) | 员工有"忽略"选项 |
| 设备清单 | Server 知道全员设备状态 | Server 不知道员工设备装了啥 |
| 处理状态 | Server 实时知道 | 员工自愿上报聚合 metadata |
| API surface | `POST /devices/:id/push` (强制) | `GET /advisory/feed.json` (公开 pull) |

### 1.2 信任模型

- **中央 publish 的 advisory 是公开信息** — 任何持有有效 SSO token 的员工都能拉
- 中央**不知道**:
  - 员工本机装了哪些 skill (跟 advisory 匹配是客户端完成的)
  - 员工有没有看到 banner
  - 员工有没有处理 advisory
- 中央**只在员工自愿上报后才知道**:
  - 某员工**自愿**点了"我处理了 advisory X"
  - 聚合: 全公司 advisory X 处理率约 X% (上报员工 / 总员工)

### 1.3 使用场景

3 种触发 Advisory 的场景:

1. **高危 skill 漏洞** — IT 部门发现某 skill 有 SSO token 泄漏漏洞, publish 高 severity advisory
2. **catfish 版本安全更新** — 0.15.x 之前有 path traversal, 发 medium severity advisory
3. **配置推荐变更** — 公司新政策推荐 default model 从 gpt-4o 改 qwen-3-32b, 发 info severity advisory

---

## 2. 数据模型 (JSON Schema)

### 2.1 Advisory 对象

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://catfish.example.com/schemas/advisory.json",
  "title": "Advisory",
  "type": "object",
  "required": ["id", "severity", "category", "title", "published"],
  "properties": {
    "id": {
      "type": "string",
      "pattern": "^CATFISH-ADV-\\d{4}-\\d{3,}$",
      "description": "格式: CATFISH-ADV-YYYY-NNN (e.g. CATFISH-ADV-2026-007)"
    },
    "severity": {
      "type": "string",
      "enum": ["critical", "high", "medium", "low", "info"],
      "description": "critical=立即处理, high=24h 内, medium=本周, low=本月, info=知悉"
    },
    "category": {
      "type": "string",
      "enum": [
        "skill_vulnerability",
        "mcp_vulnerability",
        "catfish_update",
        "policy_recommendation",
        "external_status",
        "deprecation_notice"
      ]
    },
    "target": {
      "type": "object",
      "description": "可选, 命中匹配信号. 客户端用这个判断 advisory 是否影响自己",
      "properties": {
        "skill": { "type": "string", "description": "命中 skill 名" },
        "skill_version_pattern": { "type": "string", "description": "语义化版本 range, e.g. <0.2.0" },
        "mcp_name": { "type": "string" },
        "catfish_version_pattern": { "type": "string" }
      }
    },
    "title": { "type": "string", "maxLength": 120 },
    "description": { "type": "string", "description": "markdown 格式, 渲染在详情页" },
    "recommendation": { "type": "string", "maxLength": 500 },
    "published": { "type": "string", "format": "date-time" },
    "expires": {
      "type": "string",
      "format": "date-time",
      "description": "可选, 过期后客户端不再显示 banner"
    },
    "remediation_actions": {
      "type": "array",
      "description": "推荐的一键修复操作, 客户端可绑定 UI 按钮",
      "items": {
        "type": "object",
        "required": ["label", "kind"],
        "properties": {
          "label": { "type": "string", "description": "按钮文案, e.g. '卸载并升级'" },
          "kind": {
            "type": "string",
            "enum": [
              "uninstall_skill",
              "install_skill",
              "uninstall_and_install_skill",
              "update_catfish",
              "open_settings",
              "open_url"
            ]
          },
          "params": {
            "type": "object",
            "description": "kind 对应参数, e.g. uninstall_skill 需要 skill_path"
          }
        }
      }
    },
    "references": {
      "type": "array",
      "items": { "type": "string", "format": "uri" },
      "description": "CVE / GitHub issue / blog URL"
    },
    "tags": {
      "type": "array",
      "items": { "type": "string" },
      "description": "便于客户端 filter, e.g. ['eis', 'sso']"
    }
  }
}
```

### 2.2 Feed 响应格式

`GET /advisory/feed.json` 返回:

```json
{
  "version": "1.0",
  "generated_at": "2026-06-07T10:00:00Z",
  "advisories": [
    { /* Advisory 对象 */ },
    { /* Advisory 对象 */ }
  ],
  "metadata": {
    "total": 2,
    "active_count": 2,
    "expired_count": 0
  }
}
```

### 2.3 Severity 分级语义

| Severity | 中文 | UI 视觉 | 客户端默认行为 |
|---|---|---|---|
| critical | 严重 | 红色 banner + 弹窗强提示 | banner 不可 dismiss, "稍后提醒" 间隔 1 小时 |
| high | 高危 | 红色 banner | banner 默认显示, 可 "稍后提醒" 24h |
| medium | 中等 | 黄色 banner | banner 默认显示, 可 dismiss |
| low | 低 | 灰色 banner (折叠) | dashboard advisory 列表显示, 不打扰 |
| info | 信息 | 仅日志 | 不显示 banner, 仅 advisory list |

---

## 3. Server API

### 3.1 公开 endpoint: `GET /advisory/feed.json`

**鉴权**: 有效 SSO token (跟其它 `/api/*` 同级)

**响应**: 上面 §2.2

**缓存策略**:
- `Cache-Control: public, max-age=3600` (1 小时, 客户端 6 小时 polling 一次)
- `ETag` based — 同一 advisory 集合返同一 etag, 客户端 `If-None-Match` 走 304
- 不走 CDN (advisory 内容是企业级, 不公网)

**Rate limit**:
- 60 req/min/user (公开 pull 模式, 防员工恶意 polling)

**实现** (FastAPI):

```python
# central/llm-gateway/src/catfish_gateway/advisory_router.py
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Response
from .auth import User, get_current_user
from . import advisory_store  # 后端存 advisory 的模块

router = APIRouter(prefix="/advisory", tags=["advisory"])

@router.get("/feed.json")
async def get_feed(
    response: Response,
    user: User = Depends(get_current_user),
):
    """Public advisory feed — pull-based, no push.

    跟 catfish-central-manifesto 公理 4 一致 — 中央不知道员工设备状态.
    任何 SSO 鉴权过的员工都能拉同一份 feed, 中央不区分 personalize.
    """
    advisories = advisory_store.list_active()  # 过滤 expired
    etag = advisory_store.compute_etag(advisories)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisories": [a.to_dict() for a in advisories],
        "metadata": {
            "total": len(advisories),
            "active_count": sum(1 for a in advisories if not a.expired()),
            "expired_count": sum(1 for a in advisories if a.expired()),
        },
    }
```

### 3.2 Admin endpoint: `POST /admin/advisory`

**鉴权**: sysadmin only (跟现有 admin RBAC 一致)

**Request body**: 单个 Advisory 对象 (符合 §2.1 Schema)

**Response**: `{ok: true, id: "CATFISH-ADV-2026-007"}`

**审计**: 写 `users-audit` 表, `action: "advisory.publish"`

### 3.3 Admin endpoint: `DELETE /admin/advisory/{id}`

**鉴权**: sysadmin only

**行为**: 撤销 advisory (改 `expires=now`), 不真删 (保留审计历史)

### 3.4 员工自愿上报: `POST /v1/advisory/ack`

**鉴权**: 员工 SSO token

**Request body**:
```json
{
  "advisory_id": "CATFISH-ADV-2026-007",
  "status": "acknowledged" | "remediated" | "ignored",
  "note": "可选, 员工备注"
}
```

**Response**: `{ok: true}`

**关键设计**:
- 这是**员工 voluntary disclosure** — 中央不知道员工是否真处理了, 只知道员工 "声称"处理了
- 中央**不存** 员工本机 advisory 状态 — 这些状态留员工本机 SQLite
- 中央只存 ack 行为本身 (谁在什么时间 ack 了什么) 用于聚合统计

**实现要点**:
```python
@router.post("/v1/advisory/ack")
async def ack_advisory(
    req: AckReq,
    user: User = Depends(get_current_user),
):
    """员工自愿上报 advisory 处理状态.

    跟 manifesto 公理 4 一致 — 中央不主动查员工状态, 只接收员工自愿上报.
    数据用于聚合统计 (e.g. advisory X 处理率), 不用于 per-user audit.
    """
    advisory_store.record_ack(
        advisory_id=req.advisory_id,
        user_email=user.sub,
        status=req.status,
        note=req.note,
        ts=datetime.now(timezone.utc),
    )
    return {"ok": True}
```

### 3.5 Admin 看聚合 dashboard: `GET /admin/advisory/{id}/stats`

**鉴权**: admin / sysadmin

**Response**:
```json
{
  "advisory_id": "CATFISH-ADV-2026-007",
  "total_users": 500,
  "ack_counts": {
    "acknowledged": 320,
    "remediated": 280,
    "ignored": 12
  },
  "no_response_count": 168,
  "by_department": [
    {"dept": "tech", "remediated": 95, "total": 100},
    {"dept": "finance", "remediated": 30, "total": 80}
  ]
}
```

**关键设计**:
- 这是**聚合统计**, 不是 per-user 调取
- `no_response_count` 是计算出的 (total - ack_counts 之和), 不知道这些员工**是没看到 / 看到没点 / 处理了但没上报**
- IT 看到 "168 人没响应" → 走 HR 跟那 168 人**人对人**沟通, 不能 catfish 自动催

---

## 4. 客户端协议 (Tauri / Rust)

### 4.1 Polling 频率

- 启动时立即拉一次
- 之后每 6 小时拉一次 (跟 etag 比, 304 → 不重新解析)
- catfish 关机时跳过 (不开启时不 polling)

### 4.2 本地匹配逻辑

客户端拿到 feed 后, 对每个 advisory 跑匹配:

```rust
// edge/companion-app/src-tauri/src/advisory.rs
pub struct AdvisoryMatcher {
    installed_skills: Vec<SkillEntry>,        // catfish 本机已装 skill
    catfish_version: String,                  // 当前 catfish 版本
    installed_mcps: Vec<McpServerEntry>,      // 已配 MCP
}

impl AdvisoryMatcher {
    pub fn matches(&self, advisory: &Advisory) -> bool {
        let Some(target) = &advisory.target else {
            return true;  // 无 target → 全员相关 (e.g. policy_recommendation)
        };

        // 匹配 skill
        if let Some(skill_name) = &target.skill {
            let matches_skill = self.installed_skills.iter().any(|s| {
                s.name == *skill_name
                    && target.skill_version_pattern.as_ref()
                        .map_or(true, |pat| version_match(&s.version, pat))
            });
            if matches_skill {
                return true;
            }
        }

        // 匹配 mcp
        if let Some(mcp_name) = &target.mcp_name {
            if self.installed_mcps.iter().any(|m| m.name == *mcp_name) {
                return true;
            }
        }

        // 匹配 catfish 版本
        if let Some(catfish_pat) = &target.catfish_version_pattern {
            if version_match(&self.catfish_version, catfish_pat) {
                return true;
            }
        }

        false
    }
}
```

**关键**: 匹配在客户端跑, **中央不知道员工装了啥**.

### 4.3 Banner 触发逻辑

```rust
// 本机 advisory 状态 (留 SQLite, 中央不知道)
#[derive(Serialize, Deserialize)]
pub struct LocalAdvisoryState {
    pub advisory_id: String,
    pub status: LocalAdvisoryStatus,  // unseen | seen | snoozed_until | acked | dismissed
    pub last_shown: Option<DateTime<Utc>>,
    pub snooze_until: Option<DateTime<Utc>>,
}

impl AdvisoryProcessor {
    /// 拿到 feed 后, 决定哪些 advisory 需要在 dashboard 顶部弹 banner.
    pub fn pending_banners(&self) -> Vec<Advisory> {
        let mut out = Vec::new();
        for advisory in self.matched_advisories() {
            let state = self.local_state(&advisory.id);
            let should_show = match state.status {
                LocalAdvisoryStatus::Unseen => true,
                LocalAdvisoryStatus::Seen => false,
                LocalAdvisoryStatus::Snoozed => {
                    state.snooze_until.map_or(true, |t| Utc::now() > t)
                }
                LocalAdvisoryStatus::Acked => false,
                LocalAdvisoryStatus::Dismissed => {
                    // critical 不允许永久 dismiss, 24h 后重新弹
                    if advisory.severity == Severity::Critical {
                        Utc::now() > state.last_shown.unwrap() + Duration::hours(24)
                    } else {
                        false
                    }
                }
            };
            if should_show {
                out.push(advisory);
            }
        }
        out.sort_by_key(|a| -severity_weight(a.severity));
        out
    }
}
```

### 4.4 员工操作 → 上报

员工点 `[立即处理]` / `[稍后提醒]` / `[我已了解]` 时, 客户端:

1. 更新本机 `LocalAdvisoryState` (SQLite)
2. **可选** 调 `POST /v1/advisory/ack` 上报 (员工**默认开启自愿上报**, 设置里可关闭)
3. 如果绑了 `remediation_actions`, 触发对应操作 (uninstall_skill 调 E7 phase 2 的 `uninstall_skill` Tauri command)

### 4.5 隐私设置

catfish UI 设置 → 隐私:

```
☑ 自动上报 advisory 处理状态 (默认开)
   公司 IT 能看到聚合处理率, 但看不到你具体的内容.
   关闭后 advisory 仍能正常使用, 只是公司不知道你处理了没.
```

---

## 5. Admin UI (publish + dashboard)

### 5.1 Publish 表单

central admin web (`/admin/advisory`) 加新 page:

```
┌──────────────────────────────────────────────────────────┐
│  Advisory 发布                            [取消] [发布]  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ID: CATFISH-ADV-2026-008 (自动生成, 可改)              │
│                                                          │
│  Severity:  ○ critical ● high ○ medium ○ low ○ info     │
│                                                          │
│  Category:  [skill_vulnerability ▼]                     │
│                                                          │
│  Target:                                                 │
│    Skill name: [eis-login         ]                     │
│    Version pattern: [<0.2.0       ] (semver range)      │
│                                                          │
│  标题: [eis-login v0.1.0 SSO token 泄漏漏洞    ]        │
│                                                          │
│  描述 (markdown):                                        │
│  ┌────────────────────────────────────────────────────┐ │
│  │ 在特定输入下, skill 会把 SSO token 写到 ...        │ │
│  │                                                    │ │
│  │ ## 影响                                            │ │
│  │ ...                                                │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  推荐操作: [立即卸载并升级到 v0.2.0          ]          │
│                                                          │
│  References:                                             │
│    + https://github.com/.../CVE-2026-XXXX               │
│                                                          │
│  过期日期: [2026-12-07] (可空, 默认 6 月)               │
│                                                          │
│  Remediation actions:                                    │
│    + [卸载并升级] kind=[uninstall_and_install_skill ▼]  │
│      params: {"skill_path": "...", "new_version": ...}  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 5.2 Advisory 列表 + 统计 dashboard

```
┌──────────────────────────────────────────────────────────┐
│  Advisory 列表                              [+ 发布]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ ID                       Severity  Title          状态  │
│ ─────────────────────────────────────────────────────── │
│ CATFISH-ADV-2026-007    🔴 high   eis-login...  active │
│   ├ 处理率: 56% (280/500 已修复)              [查看]  │
│ CATFISH-ADV-2026-006    🟡 medium catfish 0.15...active│
│ CATFISH-ADV-2026-005    🟡 medium policy 推荐..  active│
│ CATFISH-ADV-2026-004    ⚫ info  钉钉接入指南.. active │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 6. 数据存储

### 6.1 Schema

```sql
-- central/llm-gateway/alembic/versions/20260607_advisory.py
CREATE TABLE IF NOT EXISTS advisories (
    id VARCHAR(64) PRIMARY KEY,             -- CATFISH-ADV-2026-007
    severity VARCHAR(16) NOT NULL,
    category VARCHAR(32) NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    recommendation TEXT,
    target_json JSONB,                       -- skill/mcp/version 匹配规则
    references_json JSONB,                   -- URL 列表
    tags_json JSONB,
    remediation_actions_json JSONB,
    published TIMESTAMPTZ NOT NULL,
    expires TIMESTAMPTZ,
    published_by VARCHAR(255) NOT NULL,      -- admin email
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS ix_advisories_published ON advisories(published DESC);
CREATE INDEX IF NOT EXISTS ix_advisories_active ON advisories(expires) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS advisory_acks (
    advisory_id VARCHAR(64) NOT NULL,
    user_email VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL,             -- acknowledged | remediated | ignored
    note TEXT,
    ts TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (advisory_id, user_email)
);

CREATE INDEX IF NOT EXISTS ix_advisory_acks_advisory ON advisory_acks(advisory_id);
CREATE INDEX IF NOT EXISTS ix_advisory_acks_user ON advisory_acks(user_email);
```

### 6.2 客户端本地状态

```sql
-- edge/companion-app/src-tauri SQLite migration
CREATE TABLE IF NOT EXISTS advisory_local_state (
    advisory_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                    -- unseen | seen | snoozed | acked | dismissed
    last_shown TEXT,                          -- ISO 8601
    snooze_until TEXT,
    acked_at TEXT,
    upload_to_central INTEGER DEFAULT 1      -- 是否自愿上报 (1 = 上报, 0 = 不报)
);
```

---

## 7. 实施 3 阶段

### Phase 1: Static feed + 客户端 polling (1 周)

最小可用:
- [ ] Server: `GET /advisory/feed.json` 读静态 yaml 文件
- [ ] Client: Rust polling + 本地匹配 + dashboard banner UI
- [ ] 无 admin publish UI — sysadmin 手改 yaml + git push 测试

验收: 鸿波本机能看到 banner

### Phase 2: Admin publish + 持久化 (1 周)

- [ ] DB migration `advisories` 表
- [ ] Admin REST API (publish / list / revoke)
- [ ] Admin UI (Phase 1 表单)
- [ ] Server 改为读 DB 而不是 yaml

验收: 中央 admin 能发 advisory, 客户端 6h 内拉到

### Phase 3: 自愿上报 + 聚合统计 (1 周)

- [ ] DB migration `advisory_acks` 表
- [ ] Client UI 加 [我已了解] / [立即处理] / [稍后提醒] 按钮 + 上报逻辑
- [ ] 隐私设置开关 "自动上报 advisory 状态"
- [ ] Admin dashboard 加聚合统计 (处理率 / no_response_count / by_department)

验收: 中央能看 advisory X 处理率 56% (但看不到具体哪 56% 员工)

**总计**: 3 周, 1 个工程师可完成.

---

## 8. 测试

### 8.1 API contract test

```python
# central/llm-gateway/tests/test_advisory.py
def test_feed_returns_active_only(client):
    resp = client.get("/advisory/feed.json", headers={"Authorization": "Bearer xxx"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(a["expires"] is None or a["expires"] > NOW for a in data["advisories"])

def test_feed_etag_caching(client):
    r1 = client.get("/advisory/feed.json", headers={"Authorization": "Bearer xxx"})
    etag = r1.headers["ETag"]
    r2 = client.get("/advisory/feed.json", headers={
        "Authorization": "Bearer xxx",
        "If-None-Match": etag,
    })
    assert r2.status_code == 304
```

### 8.2 客户端匹配 test

```rust
// edge/companion-app/src-tauri/src/advisory/tests.rs
#[test]
fn test_skill_version_pattern_match() {
    let matcher = AdvisoryMatcher::new(
        vec![SkillEntry { name: "eis-login", version: "0.1.0", .. }],
        "0.15.1", vec![]
    );
    let advisory = Advisory {
        target: Some(Target {
            skill: Some("eis-login"),
            skill_version_pattern: Some("<0.2.0"),
            ..
        }),
        ..
    };
    assert!(matcher.matches(&advisory));
}

#[test]
fn test_no_target_matches_all() {
    let matcher = AdvisoryMatcher::default();
    let advisory = Advisory { target: None, .. };
    assert!(matcher.matches(&advisory));  // policy_recommendation 全员相关
}
```

---

## 9. 安全 / 隐私 review

### 9.1 是否违反 manifesto?

| Manifesto 公理 | 本 API 设计 | OK? |
|---|---|---|
| 1. 员工主权 | advisory 本身是公开信息, 不动员工资产 | ✓ |
| 2. 数据零出端 | 员工对话内容**不上报**. 仅 advisory ack metadata 自愿上报 | ✓ |
| 3. 中央 0 控制 | advisory 是建议, 员工可忽略. remediation_actions 是按钮提示, 不自动执行 | ✓ |
| 4. API 物理无能 | 中央**只有** publish + pull-feed + ack 上报 3 个 endpoint. 没有 "push to device" / "query device state" API | ✓ |

### 9.2 攻击面

- 恶意员工恶意 polling → rate limit 60/min/user 兜底
- 恶意员工伪造 ack → 影响聚合统计但不影响其他员工 (ack 只是 metadata)
- 恶意 admin publish 假 advisory → 员工 dashboard 显示, 但员工有"忽略" 选项. 跟现有 admin 信任模型一致

---

## 10. 跟 patent / moat 联动

- **patent 1 dependent claim 加这个**: "...所述中央服务仅通过 publish-feed 提供建议, 不主动推送到员工设备, 员工设备客户端 pull-based 决定是否响应..."
- **moat counter-positioning 具体落地**: Anthropic Enterprise 是 push-based, catfish 是 pull-based + 员工可忽略. 写进白皮书.

---

## 报告结尾

总工程量 **3 周 1 人**. 跟 manifesto 公理 4 完全一致, 是 catfish "中央 publish + 客户端 pull" 哲学的标准实现.

下一步 ship 顺序:
1. **Phase 1 静态 feed** (1 周, 鸿波本机能跑)
2. **Phase 2 admin publish** (1 周, IT 能发 advisory)
3. **Phase 3 自愿上报** (1 周, 聚合统计)

— spec 完
