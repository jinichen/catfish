# SOUL.md C 类精简 audit — 2026-07-26

> **BL-SOUL-C-SLIM** · 鸿波拍板
> 触发: 读 Anthropic Thariq《The new rules of context engineering for Claude 5 models》
> (https://x.com/trq212/status/2080710971228918066) 后问"对鲶鱼有没有可借鉴的"

## 结果

`edge/identity/SOUL.md` · **292 → 260 行** (措辞压缩 · **内容零删**)
备份: `edge/identity/SOUL.md.bak-before-c-slim-20260726`

## ⚠ 关键约束 · 多模型支持 (以后 audit 必读)

鲶鱼支持 **7 个模型** (`central/llm-gateway/config/models.yaml`):

| 模型 | 类型 |
|---|---|
| catfish-private-main | 内网 (qwen_v3_5_122b_a10b) |
| catfish-private-vision | 内网 vision |
| catfish-private-embed | 内网 embedding |
| catfish-public-qwen | 公网 |
| catfish-public-deepseek-flash | 公网 |
| catfish-public-gemini-pro | 公网 (gemini-2.5-pro) |
| catfish-public-gemini-flash | 公网 |

**能力跨度大** · 从内网 122b 到 gemini-2.5-pro.

→ **所有硬阈值必须保留**:
- `>30 条必须 execute_code` (LLM 累加必错)
- `≥3 tool / 长文档 / 多步骤 → plan`
- 时长 3 档 (<5min 直接做 / 5-30min todo / >30min catfish_run_task)
- `evidence_count ≥3` (skill 抽)
- `满 3 次返 should_confirm` (偏好画像)
- `每 session 最多 1 次主动确认`
- `5-10 个 session 才用 1 次` (关系建立 sparse)
- 视觉估坐标不优先 (122b 偏 50-100 像素)

**这些是弱模型的救命绳 · 不是"Claude 3 时代遗留"**. 以后 audit 别按"新模型判断力强"删阈值.

## 文章 6 条建议 vs 鲶鱼现状

| Anthropic Now | 鲶鱼现状 | 结论 |
|---|---|---|
| Progressive disclosure | ✅ SOUL 4 层按工具候选注入 (BL-SOUL-SCENARIO P2 · 5/13) | **已领先** |
| Design interfaces | ✅ SOUL_BROWSER 三路径 A/B/C · role='button' 接口设计 | 部分领先 |
| Auto-memory | ⚠ catfish-memory 5 轮/30 min 节流 (时间触发不是内容触发) | 部分对齐 |
| Let Claude use judgement | ⚠ 多模型约束下**不适用** (见上) | 不适用 |
| Simple tool descriptions | ⚠ 实际可挪只 ~22 行 (SOUL 里主要是跨工具策略) | 收益太小 · 未做 |
| Rich references | 🔴 全 markdown spec · 无代码/rubric/test suite | 以后可做 |

## 为什么 "删 80%" 不适用鲶鱼

Anthropic 能删 Claude Code 80% 系统提示 · 因为那里大量是**通用编程指导** (写不写 comment / 要不要文档) · 模型自己能判断.

鲶鱼 SOUL 292 行构成:
- **60% 产品/安全/隐私红线** — 身份 (对外永不说 Hermes) · 五条哲学 · memory 红线 (健康/工资/感情/政治/密码永不存) · 密码 secret_ref · a2a 跨员工 IM 隐私边界 · catfish-policy R9 deny · 不可逆动作红线
  → **跟模型判断力无关** · 是产品定义 · 删了就不是鲶鱼
- **25% 鲶鱼特有工具映射和踩坑** — 工具偏好表 · MCP 全名规则 · 系统操作症状表 · execute_code 架构隔离 · 两套 skill 系统
  → **模型再强也不知道 `catfish_browser_locate` 存在**
- **10% 产品节流设计** — 每 session 1 次确认 / 24h 限流 / evidence ≥3 / sparse 关系
  → **UX 设计不是能力约束**
- **~5% 可能真过约束** — Turn 控制措辞 / 部分硬阈值表述

## 实际改了什么 (C 类 · 只压措辞)

6 段合并短句 · 删重复表达 · 内容零删:

1. **Memory** · target 二分从 3 行 list 合 1 行 · "改后 quote 旧+新" 并入前段
2. **文书 fingerprint** · 3 段合 1 段
3. **同 session 别忘事** · 3 段合 1 段
4. **情绪** · 3 步 numbered list 改 ①②③ 内联 · 2 段合 1 段
5. **多入口** · 4 段合 1 段
6. **工具调用失败 + Debug** · 各 3 段合 1 段

## Verify 通过项

grep 确认 15 个关键内容全在:
`写前必须 search` · `健康/病情/用药` · `personal.{health` · `keychain://` (×3) ·
`catfish-policy R9` · `IN/OUT log` · `已截断` · `find_by_text` · `≤200 字` ·
`抑郁` · `心理咨询热线` · `第 N 次修订` · `复述模式` · `fingerprint_get` · `≥3 turn`

## 踩坑记录 · 注释不能放 SOUL.md

第一版把这份 audit 当 HTML 注释写在 SOUL.md 头部 (22 行) · 然后 verify 发现:

`edge/companion-app/src-tauri/src/commands/identity_bundle.rs:109` `read_file_or_baked()`
**原文全读 · 不 strip HTML 注释** → 22 行注释会塞进 LLM context.

净效果: 正文省 32 行 · 注释加 22 行 → **净省 10 行** · 违背精简目的.

修: audit 挪本文档 · SOUL.md 只留正文.

**教训**: SOUL.md 是 LLM context 不是代码文件 · 任何字符都花 token · 设计意图/audit 记录走 `docs/` 或 git commit message.

## 未做 · 留以后

- **B 类阈值放宽** — 多模型约束下不做 (需要先有 SOUL 级 evals 才敢动)
- **Rich references** — SOUL_BROWSER 的三路径可以给真 test case 而非文字描述 · 需要建维护 examples 库
- **SOUL_BROWSER/SOUL_EXECUTE_CODE 22 行重复** — 收益太小 · 且 execute_code tool desc 在 hermes-agent 里 (改要走 fork patch)
