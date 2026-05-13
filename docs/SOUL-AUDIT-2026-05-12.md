# SOUL.md 必要性审计 — 2026-05-12

鸿波提问: "为什么 SOUL.md 有 DEMO 相关内容, 正式系统到底哪些必要?"

# 1. 体量

`/Users/chenhongbo/person_task/catfish/edge/identity/SOUL.md`

| 指标 | 值 |
|---|---|
| 行数 | **2032** |
| 字节 | **109,572** |
| 估算 token | **~55K** (中文 ~2 char/token) |
| 占 32K window | **170%** (单 chat system prompt 就超 1 倍 context) |
| 占 256K window | 21% |
| 章节数 (`##`) | 36 段 |

**每次 chat 都全量注入 system prompt 顶部** — 每条对话烧 55K input token, 跨 5 轮对话累计 250K+ → BL-FIX23 context overflow 警报根因之一.

# 2. 章节地图 + 三分类

| 行号 | 段标题 | 分类 | 理由 |
|---|---|---|---|
| 1-13 | 你是小鲶 / 身份 | **CORE** | 品牌识别 |
| 14-29 | 品牌铁律 (不说 hermes) | **CORE** | 安全/品牌 |
| 30-38 | 五条核心哲学 | **CORE** | 产品定位 |
| 39-101 | 专家代理人铁律 BL-FED2 | **EXAMPLE** | 通用纪律但 §跟 5/14 demo 配合 (86-99) 是 KA017 demo |
| 102-134 | Unified Inbox BL-D14 | **EXAMPLE** | 通用入口纪律, 但 §跟 ChatGPT 区别 (126-131) 是营销话术 |
| 135-225 | Agent DAG 规划 | **CORE** | 通用 plan/verify 纪律, 但例 137 "修订《资质管理办法》" 是 FFCS |
| 226-255 | 长 docx 输出 | **CORE** | 通用 token 限制 |
| 256-294 | 文档修订 3 步 | **CORE** | 通用纪律 |
| 295-335 | 做完才说 BL-FIX23 | **CORE** | turn 控制铁律 |
| 336-387 | 做完不再问 BL-FIX24 | **CORE** | 同上 |
| 388-449 | 请示停顿 BL-FIX46 | **CORE** | 同上 |
| 450-506 | /goal 锁定 | **CORE** | 长 attention 兜底 |
| 507-595 | 教学边界 ★★★ MM9-FREEZE | **CORE** | catfish 卖点根基 |
| 596-604 | 语气 | **CORE** | |
| 605-644 | 情绪/关系建立 BL-E16 | **CORE** | 通用边界 |
| 645-682 | 工具偏好表 + MCP 命名 | **CORE** | 工具路由必需 |
| **663-682** | **内网域名 http 不升 https** | **DEMO/FFCS** | 整段 .ffcs.cn / .10086.cn / 中国电信内网 — 非 FFCS 客户用不上, 是**伪装成安全的业务知识** |
| 683-708 | 多模态能力 | **CORE** | 防 self-deny |
| 709-742 | 别让员工跑 shell | **CORE** | 通用 UX |
| 743-749 | 你不做的事 | **CORE** | |
| 750-807 | 复述模式 attention hot-fix | **CORE** | 例 769 用 EIS 可去 |
| 808-878 | 记忆覆盖 BL-MM1 | **CORE** | 通用纪律 |
| **879-1052** | **主动学偏好 BL-MM5 (174L 最长!)** | **REDUNDANT** | 跟 BL-MM7 + Memory 写入 3 段重叠 60% |
| 1053-1100 | BL-MM7 user_profile 工具 | **CORE** | 工具纪律 |
| 1101-1133 | BL-MM8 fingerprint | **CORE** | 工具纪律 |
| 1134-1192 | BL-MM9 propose_skill | **CORE** | 工具纪律 |
| 1193-1259 | 批量抓取优先级 | **EXAMPLE** | 通用方法但例全是 EIS 145 条资质 |
| 1260-1319 | 数据=代码统计 | **CORE** | LLM 物理限制 |
| 1320-1389 | execute_code 红线 | **CORE** | sandbox 边界 |
| 1390-1415 | 工具失败不幻觉 | **CORE** | |
| 1416-1448 | catfish_run_skill 不要 skills_list 验证 | **CORE** | 双 skill 系统硬事实 |
| 1449-1552 | secret_ref 凭据 (104L) | **CORE** | P1 安全, 但例全是 keychain://eis_password |
| **1553-1745** | **Skill 生成纪律 (193L 第二长!)** | **REDUNDANT** | 跟 BL-MM9 重叠, lifecycle 阶段 3/4/5 详细可拆 docs/ |
| **1746-1866** | **Memory 写入 (121L)** | **REDUNDANT** | 跟 BL-MM1/MM5/MM7 重叠 60% |
| 1867-1918 | 情绪信号 | **CORE** | |
| 1919-1926 | 创业语境 | **CORE** | 短小 |
| 1927-1984 | 浏览器自动化 BL-FIX44 | **EXAMPLE** | 通用 4 步, 例 1935 "鸿波 5/11 EIS 实测" |
| 1985-2020 | archive_ref BL-Q3-ARCHIVE | **CORE** | gateway 协议 |
| 2021-2029 | 引用资料 | **CORE** | |

# 3. 关键诊断

**真正的 DEMO/FFCS 特定**: §663-682 (内网域名 http) — 整段 `.ffcs.cn / .10086.cn / .chinatelecom.cn` 是中国电信内网约定. 卖给字节/美团/华为则需要换域名. **是伪装成 CORE 安全的 FFCS 业务知识**.

**EXAMPLE (通用纪律 + FFCS 例子)**: §39-101 BL-FED2 的 5/14 demo 段, §1193-1259 批量抓取的 EIS 145 条例子, §1320-1389 execute_code 的 EIS 抓取踩坑 — 可保留方法论但抽掉具体 demo 数据.

**REDUNDANT (3 个段最值得砍)**:

1. §879-1052 BL-MM5 (174L) ↔ §1053-1100 BL-MM7 ↔ §1746-1866 Memory 写入 — **3 段都在讲"什么时候写记忆 + 红线 + 4 步自检"**, 自检 4 步 §1791 跟 §1838 几乎逐字重复
2. §1553-1745 Skill 生成 (193L) ↔ §1134-1192 BL-MM9 — Review-A/B/lifecycle 阶段 3/4/5 完全可移到 `docs/SKILL-LIFECYCLE.md`
3. §295-449 三段"做完才说/做完不再问/请示停顿" 共 154L — 同源 turn 控制纪律可合并 ~60L

# 4. 优化方案 (5 条, 累加效果)

## 方案 1: 拆 SOUL_LITE.md (核心铁律, ~600 行 / ~16K token)

保留:
- §1-38 身份/品牌/哲学
- §295-506 turn 控制 4 段 (合并)
- §507-595 教学边界 ★★★
- §596-682 (去掉 663-682 内网 http) 语气+工具偏好
- §683-749 多模态/shell/红线
- §1390-1448 工具失败/run_skill
- §1449-1502 secret_ref 核心
- §1985-2020 archive_ref

降到 ~16K token (-70%).

## 方案 2: 移到 `docs/` (按需 read, 不注入)

| SOUL.md 段 | 目标文件 |
|---|---|
| §1553-1745 Skill 生成 | `docs/SKILL-LIFECYCLE.md` (本来就引用) |
| §808-878 + §879-1052 记忆 | `docs/MEMORY-DISCIPLINE.md` |
| §39-101 BL-FED2 详细 | `docs/A2A-AGENT-PROXY.md` |
| §1193-1259 批量抓取 | `docs/BATCH-SCRAPING.md` |

SOUL_LITE 保留每段 1-2 句指针 + "细则见 docs/X.md, 触发场景再 read".

## 方案 3: 按场景注入 SOUL_<scenario>.md

gateway 已有 `system_prompt_assembler` (rule-based). 加规则:
- 检测到 `catfish_browser_*` tool 候选 → 注入 §1927-1984 浏览器纪律 + §663-682 内网 http
- 检测到 `skill_manage / catfish_propose_skill` → 注入 §1553-1745
- 检测到 `memory_save / catfish_remember` → 注入 §1746-1866
- 检测 sender_id ≠ owner → 注入 §39-101 BL-FED2

## 方案 4: 客户独立 SOUL_<customer>.md

- `SOUL_FFCS.md`: §663-682 内网 http (`.ffcs.cn` 域名表), §86-99 KA017 demo, §1193-1259 EIS 145 条例子
- 通用 SOUL.md 去掉 FFCS 特定例子, 只留方法论模板

## 方案 5: REDUNDANT 合并 (一周内可做, 零风险)

- §879-1052 + §1746-1866 + §1791 + §1838 自检 4 步 → 合成单段 ~80L (省 ~210L)
- §295-335 + §336-387 + §388-449 turn 控制三连 → 合成单段 ~60L (省 ~95L)
- §1553-1745 阶段 3/4/5 → 移 docs, SOUL 留 30L 入口 (省 ~160L)

# 5. token 估算

| 状态 | 行 | token | 每次 chat 烧 |
|---|---|---|---|
| 现状 | 2032 | ~55K | 100% |
| 方案 1 (SOUL_LITE 单文件) | ~600 | ~16K | 29% |
| 方案 1+5 (合并冗余) | ~450 | ~12K | 22% |
| 方案 1+2+3 (按需注入) | ~300 base + 平均 5K 场景 | ~10K | 18% |
| 方案 1+2+3+4 (FFCS 独立) | 同上, 别家客户更小 | ~8K | 15% |

# 6. 风险

| 风险 | 缓解 |
|---|---|
| 拆 §663-682 http 升级 → demo .ffcs.cn 撞 ERR_CONNECTION_REFUSED | gateway 加 URL 检测, 命中 `.ffcs.cn / .10086.cn` 时强制注入这段 |
| 拆 §1553-1745 Skill 生成 → LLM 自决创建脏 skill | 保留 §1611 红线"不自动 create" 在 SOUL_LITE |
| 拆 turn 控制三段 → 5/9 demo 翻车风险 | 合并时保留所有 ❌ 反例字面量, 别压缩例子 |
| 教学边界 §507-595 ★★★ 永远 CORE | 不动, 5/12 翻 8 次的根因 |
| §39-101 BL-FED2 信任分层 60-70 + 红线 72-79 不能完全移 docs | 必须留 SOUL_LITE, 否则 sender ≠ owner 时漏信息 |

# 7. 推荐落地顺序

| 优先级 | 项 | 收益 | 风险 |
|---|---|---|---|
| **本周** | 方案 5 (合并 redundant 3 组) | 立刻省 ~30% token | 零风险 |
| **下周** | 方案 2 (移 Skill lifecycle / Memory 细则到 docs/) | 再省 ~30% token | 低 (配 catfish_read_doc 让模型按需 read) |
| **P2** | 方案 3 (gateway tool-候选感知注入) | 再省 ~15% token | 中 (改 system_prompt_assembler) |
| **客户>2 时** | 方案 4 (SOUL_<customer>.md) | 别家客户 50% 起步 | 高 (维护两套需流程) |

# 附: 当前 SOUL.md 跨段重复段落 (清晰证据)

**重复 1 — "什么时候写记忆"**:
- §867 BL-MM1 红线段
- §891-895 BL-MM5 触发条件
- §1763-1772 Memory 写入触发条件
- → 3 处都列同样 "员工 explicit ask / 跨 session 价值 / 5 分钟内提 ≥3 次"

**重复 2 — 自检 4 步**:
- §1791 BL-MM5 自检 4 步
- §1838 Memory 写入自检 4 步
- → 几乎逐字重复 (4 个问题文本一致)

**重复 3 — Skill lifecycle**:
- §1138-1192 BL-MM9 propose_skill 工具描述
- §1553-1745 Skill 生成纪律全章
- → 后者展开前者 4x, 加阶段 3/4/5 详细 — 模型读完前者已够, 后者是 backlog

# 8. 行动建议

**鸿波 5/13 起手**:

1. 第一周做方案 5 (本文 §4.5), 把 3 个 REDUNDANT 段合并 → SOUL.md 立刻瘦到 ~1400 行 / ~38K token
2. 第二周做方案 2 (本文 §4.2), 把 4 段移 docs/ + SOUL 留入口 → ~900 行 / ~24K token
3. 第三周看实际 token 累积是否还撑 (跨 5 轮对话 ~120K), 决定是否上方案 3

每步做完跑一遍 fed_demo.sh + 5 个 BL-FIX23 回归 case 验证行为不退化.

---

**审计员**: catfish-claude (Plan, 2026-05-12 末班车)
**触发**: 鸿波问 "正式系统到底哪些必要?"
**关联**: BL-FIX23 (context overflow), BL-FIX40 (employee_journal 截断), BL-FIX41 (tool_msgs 累积) — 这些都是 SOUL.md + 累积 messages 共同撑爆的症状
