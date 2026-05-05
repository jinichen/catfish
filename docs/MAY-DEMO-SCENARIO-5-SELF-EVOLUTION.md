# Demo 第 5 场景候选 — Self-Evolution (LLM 自学新 skill)

> **5/5 晚 鸿波拍板**: 加为 demo 候选场景, 60 秒演示"鲶鱼能学新工作流".
> **要不要演**: 看 demo 时间 / 现场氛围, 弹性加. 17 分钟主体仍是原 4 场景.
> **位置**: 第 4 场景跨 session 记忆讲完后, 如果还有 90 秒余量, 加这个第 5.

---

## 场景定位

**卖点**: Self-Evolution — 鲶鱼能把员工的重复工作**学成 skill**, 之后自动跑.
**反方对比**: ChatGPT 是聊天工具, 每次员工要重复打字; 鲶鱼**记住流程**, 第二次直接调.
**护城河暗示**: 这是 Phase 1 单员工副手, Phase 3 跨员工 federation 时, 优秀 skill 还能跨员工共享 (Skills Hub).

---

## 60 秒台词

### 第 0 秒 - 切场景

> "再演一个 — 鲶鱼能把员工的工作**学成 skill**. 这是我们叫 Self-Evolution 的卖点."

### 第 5 秒 - 员工说话

打字 (Companion 对话框):

```
我每天早上要查 EIS 资质表, 把 next 14 天到期的行抓出来发邮件.
写成 skill, 以后我说"查到期"就跑.
```

### 第 15 秒 - 鲶鱼 quote 步骤 (期望响应)

```
好. 我打算这么写:

skill 名: productivity/eis-expiry-check
步骤 1: 用 catfish-browser-task 开 EIS 资质列表
步骤 2: 提取 next 14 天到期的行 (字段: 编号 / 类别 / 部门 / 到期日)
步骤 3: 输出 markdown 表格 + 草拟邮件主题"EIS 资质到期提醒 - <日期>"
步骤 4: 弹邮件草稿给你审, 你点发送

这样写对吗? Yes 我就建.
```

**演示要点**: 强调"步骤先 review", **不是直接建**.

### 第 35 秒 - 员工微调

```
步骤 2 改成 next 30 天, 其他 OK.
```

### 第 45 秒 - 鲶鱼建 skill (期望响应)

工具调用展开看到:

```
✓ skill_manage(action=list)              [BL-C13 重复检查 - 没撞]
✓ skill_manage(action=create, ...)        [创建 ~/.hermes/skills/productivity/eis-expiry-check/]
✓ catfish_skill_dry_run(...)              [BL-C12 dry-run 验证 - 通过]

回话:
"建好了. dry-run 通过 (没真发邮件, 只跑了步骤模板).
明早你说'查到期', 就直接走这个 skill."
```

### 第 60 秒 - 客户问"那我能看到这 skill 吗"

```bash
# 当场切到 Companion 仪表盘 LearningCard:
"这里 — '今天新增 1 个 skill: productivity/eis-expiry-check'
       '调用 0 次, 健康' (BL-C15 字段)
你随时能改 / 删 / 备份, 全在你笔记本里 ~/.hermes/skills/."
```

---

## 演示要点 (3 个亮点)

1. **双确认 (SOUL 纪律)** — quote 步骤 → review → 员工 yes 才真建. 强调"鲶鱼不自作主张乱建 skill"
2. **创建后立即 dry-run** — 防废 skill, BL-C12. 说"如果跑不通鲶鱼自己删, 不留垃圾"
3. **审计透明** — Dashboard LearningCard 看到所有 skill 增减, 调用次数. BL-C14/C15 整套
4. **不存凭据** — skill 只存步骤模板, 密码/token 走 keychain. 客户问"安全吗"有现成答案

---

## fallback 话术 (出 bug 怎么办)

| 现场情况 | 说什么 |
|---|---|
| 鲶鱼没 quote 步骤直接建了 | "这是 LLM 偶尔越权 - SOUL 纪律是软约束. 但你看 BL-C12 dry-run 是工程级硬约束, skill 跑不通自动回滚." 然后跑 BL-C12 演示验证 |
| dry-run 失败 | "看, 我们设计的就是 fail-safe — 跑不通就不留, 你看现在 ~/.hermes/skills/ 没这个目录." `ls ~/.hermes/skills/productivity/` 演示 |
| 鲶鱼建了一个跟已有 skill 重名 | "BL-C13 dedup 应该挡, 这条是 LLM 没 list 就建. 我们补救方案是手动 list 然后删一个." 现场演示 |
| LLM 不知道怎么 quote | "我 quote 步骤前要先调 skill_manage(action=list) 看现有 skill, 现在跳过." (这条要在台前用一句话糊过去, 客户不会深究) |

---

## 不演的版本 (口播 30 秒)

如果时间紧 (主体 4 场景已经 17 分钟), 可以不真演, 只口播带过:

> "鲶鱼还有个 Self-Evolution 卖点 — 员工说'把这流程做成 skill', 鲶鱼会:
>   1. 先 quote 步骤给员工 review (不自作主张)
>   2. 员工 yes 才真建
>   3. 建完立即跑一次 dry-run 验证, 跑不通自动删
>   4. 全过程 Dashboard 审计可看
>
> 这是 Phase 1 单员工副手. Phase 3 federation 时, 优秀 skill 还能跨员工共享 (Skills Hub) — 一个员工写完, 整个公司受益.
>
> 不展开, 下次 PoC 给你们看完整流程."

---

## 真机彩排前检查 (5/9-5/12)

加这场景前确认:

- [ ] BL-C12/C13/C14/C15/C16 真机跑通 (理论上 5/2 已 ship, 跑一遍验证)
- [ ] `~/.hermes/skills/` 目录干净 (重复 demo 前清掉之前建的, 防 dedup 撞)
- [ ] 真机 6 个候选模型至少 1 个能调 (deepseek-flash 工作即可)
- [ ] catfish-browser-task tool 真能开 EIS 列表 (BL-X4)
- [ ] LearningCard "今天新增 skill" 字段真显示 (5/2 BL-C15 ship 但要真验)

---

## 风险评估

**5/14 demo 加这场景**:

| 风险 | 概率 | mitigation |
|---|---|---|
| LLM 不按 SOUL quote 直接建 | 🟡 中 | 现场承认是 LLM 越权, 但 BL-C12 拦得住 — 反而是卖点 |
| dry-run 失败 | 🟢 低 | EIS 资质列表是熟场景 (场景 1 同一个), browser-task 健康 |
| 60 秒讲不完 | 🟡 中 | 留 90 秒 buffer, 不行就用"不演的版本"口播带过 |
| 客户问"那我自己能写 skill 吗" | 🟢 低 | "能, `skill_manage(action=create)` tool 暴露给员工自己也能调. PoC 时给你们看 SDK." |
| BL-C 那套实际跑出 bug | 🟡 中 | 5/2 ship 5/13 才用, 中间 11 天没真跑过. **必须 5/9-5/10 真机彩排时验证** |

**风险综合判断**: 🟡 中风险高回报. 加这场景能强化 Self-Evolution 卖点, 但 5/9-5/10 真机彩排发现 BL-C 实际有问题就**只口播不真演**.

---

最后更新: 2026-05-05 23:xx (5/5 晚)
执行: 5/9-5/12 彩排时跑一遍验证, 5/14 demo 当天根据时间余量决定演不演
