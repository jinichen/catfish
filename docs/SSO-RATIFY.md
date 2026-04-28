# SSO 6 决策点 · Ratify 表

**给鸿波**. 5 分钟读完, 每个决策圈一个选项, 拍完我立即按方案开干.

> 本文是 `docs/AUTH-DESIGN.md` § 6 的精简版 + 今天 (2026-04-28) 对话学到的新考量. 拍完决策, 我会把结论同步回 AUTH-DESIGN.md § 13 决策签名表.

---

## 全局背景 (1 分钟)

**为啥要做**: 现在 catfish 用硬编码 dev_token, 单用户. 客户 IT 一看是玩具. 5 月 demo 必须看到 "陈鸿波 / 张三 / 李四 各自登录, 看不到对方对话".

**架构现状**: gateway (中央, 8999) 看 Bearer token 决定谁是谁. tool-bridge / Companion (边缘) 不直接管 auth, 但要把 token 传给 gateway.

**目标客户**: 中国电信内部 / 央国企部门 — 都用飞书, 多数自己有 SSO 系统 (基于 OIDC 或 SAML).

---

## 决策 1: Phase 1 一期支持哪几个 IdP?

> 客户用什么登录系统?

| 选项 | 覆盖率 | 工作量 |
|---|---|---|
| (A) 只飞书 | ~35% | 最小 |
| **(B) 飞书 + 自建 OIDC** ⭐ | ~45% | 几乎跟 A 一样 |
| (C) 飞书 + 钉钉 + 企微 | ~85% | +5 天 |

**我推荐 B**. 理由:
- 飞书必做 (国央企用飞书的 80%+)
- 自建 OIDC = 给"自己有 SSO 的客户"留接口, 工作量几乎是 0 (OIDCProvider 已经写好, 飞书也是 OIDC 适配)
- 钉钉 / 企微留 Phase 2, 客户问就说"Q3 出"

**今天新考量**: 中国电信集团客户**自己有 FFCS-SSO**. 自建 OIDC 让它能直接接, 不用等 Phase 2.

> **拍板**: ⬜ A · ⬜ B · ⬜ C

---

## 决策 2: 走自建 SSO server 还是直接接 IdP?

> gateway 自己验 IdP token, 还是中间加一个 catfish-sso 服务?

| 选项 | 简单度 | 中央复杂度 |
|---|---|---|
| **(A) 直接接** ⭐ | 简单 | 低 (gateway 直接验 JWT) |
| (B) 自建 catfish-sso | 复杂 +1 周 | 中央多一个服务 |

**我推荐 A**. 理由:
- 简单 = demo 风险低
- gateway 直接验 IdP 签的 JWT, 不需要再代理一层
- 跟 STRATEGY "中央最小" 一致 (中央服务越少越好部署)
- 唯一不便: 客户 IT 要自己配回调 URL — 但这是一次性 5 分钟工作, 文档写清楚就行

> **拍板**: ⬜ A · ⬜ B

---

## 决策 3: 用户身份 (User.sub) 格式

> audit log / RBAC / quota 都靠这个 ID. 用什么?

| 选项 | 跨 IdP 通用 | 中国企业风格 |
|---|---|---|
| **(A) email** ⭐ | ✓ | OK |
| (B) 工号 (employee_no) | ✗ | ✓ |
| (C) `<idp>:<user_id>` 组合 | ✓ | 别扭 |

**我推荐 A (email)**. 理由:
- 飞书 / 钉钉 / 企微 / 自建 OIDC **都有 email**
- audit log 看 `chenhongbo@ffcs.cn` 比 `1234567` 清晰
- 唯一缺点: 员工 email 改了要重新 onboarding (但极少发生)

**今天新考量**: 你用户名是 `chenhongbo` — 假设客户场景 email 也是 `chenhongbo@ffcs.cn` 这种, 跟你日常一致.

> **拍板**: ⬜ A · ⬜ B · ⬜ C

---

## 决策 4: 离线 / IdP 不可达兜底

> VPN 断了 / 出差 / IdP 维护时, 鲶鱼怎么办?

| 选项 | 安全 | 体验 |
|---|---|---|
| **(A) 进入"只读模式"** ⭐ | ✓ | 中 (能看历史不能调 LLM) |
| (B) 缓存 user info 30 天 + 全功能 | 中 (撤销延迟) | 好 |
| (C) 永远依赖 IdP 在线 | ✓✓ | 差 (出差不能用) |

**我推荐 A**. 理由:
- 客户 IT 最关心"撤销立刻失效", A 满足 (撤销后下次 token 过期就用不了, 1h 内)
- B 的 30 天太长, 客户 IT 接受不了 (员工已离职但 30 天还能用 = 合规风险)
- C 太严, 出差 = 完全不能用, 客户员工会骂

**今天新考量**: 之前讨论过出差 + BYOK + VPN. SSO 跟 BYOK 是两个事 — BYOK 是"用谁家 LLM", SSO 是"你是谁". 出差时**SSO 可能也断** (公司 IdP 在内网). A 模式下: 出差 = 只读, 不能调 LLM. **跟 BYOK 一致**.

> **拍板**: ⬜ A · ⬜ B · ⬜ C

---

## 决策 5: session 存哪 (跟决策 4 配套)

> token 信息存中央还是无状态?

| 选项 | 中央复杂度 | 主动 logout |
|---|---|---|
| **(A) JWT stateless** ⭐ | 0 | ✗ (要等 token 过期) |
| (B) 中央 Redis | 中 (多一个服务) | ✓ |

**我推荐 A**. 理由:
- 跟 STRATEGY "中央最小" 完全一致
- 用短 TTL (1h access + 7d refresh) 缓解"无法主动 logout"问题
- 客户撤销 = 1h 内必然失效 (refresh 也会撞 IdP 验证, 立刻挂)
- 中央**不存任何 session**, 部署只是 1 个 fastapi, 不要 Redis

**今天新考量**: 我们今天 ship 了 session_facts (~/.catfish/session_facts.json 边缘文件). 它跟 SSO session **完全无关** — facts 是对话内容上下文, SSO 是身份验证. 别混淆.

> **拍板**: ⬜ A · ⬜ B

---

## 决策 6: dev_token 是否保留作为生产兜底?

> 客户 IT 配错 SSO / SSO 服务挂了, dev_token 救命用?

| 选项 | 可救场 | 安全 |
|---|---|---|
| **(A) 保留 + warning banner** ⭐ | ✓ | 中 (有警告 + audit 标记) |
| (B) env=prod 时彻底禁用 | ✗ | ✓✓ |

**我推荐 A**. 理由:
- 客户 IT 第一次配 SSO 必撞坑 (回调 URL / jwks / claims mapping), B 模式下 catfish 直接不能用
- A 模式下: 客户 IT 配错 → 用 dev_token 临时进 → 发现 SSO 是错的 → 修. 不影响业务.
- 安全兜底: dev_token 设置时 audit log 标 `auth_method=dev_token` + UI 顶部 warning banner "你在用 dev token, 不是真 SSO". IT 看到立刻知道问题.

**今天新考量**: 我自己今天 demo 一直用 dev_token (`50c0...6668`). 真删了我自己 demo 先撞墙. 保留是务实选择.

> **拍板**: ⬜ A · ⬜ B

---

## 如果你全部采纳推荐 (A2-A6 + B1)

```
Phase 1A · AuthProvider 抽象重构          1-2 天
Phase 1B · OIDCProvider + 飞书 adapter    3-4 天
Phase 1C · Companion OAuth flow           2-3 天
─────────────────────────────────────
总计                                       6-9 天
```

5 月 demo 可用 = ~7 天后. 时间够用 (剩 ~10 工作日 buffer 1-3 天).

---

## 全部 6 个决策一次拍

> ✅ **全部采纳推荐 (1B + 2A + 3A + 4A + 5A + 6A)**
> 鸿波签字: 陈鸿波  日期: 2026-04-28
>
> 同步至 `docs/AUTH-DESIGN.md` § 13 决策签名表. Phase 1A 立即开干.

---

## 你想偏离推荐? 标出来

如果某个决策你不同意推荐, 在下面写下选择 + 一句话理由, 我会:
1. 同步到 AUTH-DESIGN.md § 13
2. 调整 Phase 1A/B/C 的实施细节
3. 重估 timeline

```
决策 1 我选: ___  理由: _______________________
决策 2 我选: ___  理由: _______________________
决策 3 我选: ___  理由: _______________________
决策 4 我选: ___  理由: _______________________
决策 5 我选: ___  理由: _______________________
决策 6 我选: ___  理由: _______________________
```

---

## 拍完之后我立即做什么

签字 / 微信发我一句"全采纳推荐" / 或者标出偏离 → 我:

1. 把决策同步回 `docs/AUTH-DESIGN.md` § 13
2. 起 commit `docs(auth): 6 决策签字 + Phase 1A 立即开干`
3. 当天开 Phase 1A (AuthProvider ABC + DevTokenProvider 包装), 不破坏现有行为
4. Phase 1A 跑通 → 第二天 Phase 1B (飞书 adapter) → 第四五天 Phase 1C (Companion OAuth)

总: 你拍完 → 7 天后 SSO 可演 = 5月 demo 前安全交付.
