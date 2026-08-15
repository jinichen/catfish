/** 智能参谋的提示词 —— SYSTEM_PROMPT / 用户消息拼装 / 结构化输出 schema。
 *
 * 8/15 从 briefing_advisor.ts 搬出来。
 *
 * # 561 行里绝大部分是提示词文本, 不是逻辑
 *
 * 这是"内容即代码"的典型: SYSTEM_PROMPT 220 行是给模型的行为规约,
 * buildUserPrompt 202 行是把当天的邮件/日程/待办拼成一段人话,
 * ADVISOR_JSON_SCHEMA 139 行是 OpenAI function calling 的参数 schema。
 *
 * 单独一个文件的好处是: 改提示词的人不用翻过 fetch 编排和 JSON 解析。
 * 早安的效果好不好, 九成取决于这个文件, 一成取决于别的。
 *
 * # 三者是一套, 别单独改一个
 *
 * SYSTEM_PROMPT 说"输出什么", ADVISOR_JSON_SCHEMA 在 API 层硬约束"长什么样",
 * parseAdvisorResult (在 _parse.ts) 在客户端再兜一层底。
 * 加字段要三处一起动 —— 只改 schema 不改 prompt, 模型不知道该填;
 * 只改 prompt 不改 schema, tool_call 会被 API 拒。
 */
import type { AdvisorInput } from "./briefing_advisor_common";


/** P3.4.E (6/15 鸿波): AdvisorResult OpenAI function calling schema, 跟 AdvisorResult interface 严格对齐.
 *
 *  用于 Call 2 transformToStructured — 拿 Call 1 (hermes agent loop) 的 raw content (可能是
 *  reasoning + 部分 JSON 混合 / 也可能纯 reasoning 无 JSON), 单 shot 让 LLM 转结构化 tool_call.
 *  tool_choice: {type:"function", function:{name:"submit_advisor_result"}} 100% 强制
 *  LLM 返 tool_calls 不允许 free-text content. DeepSeek beta endpoint (P3.4.E.1 改) 完整支持.
 *
 *  跟 parseAdvisorResult 双层校验: schema 给 LLM API 层硬约束, parseAdvisorResult 给客户端额外
 *  enum 归一 + 默认值兜底 (e.g. tone 非法 → balanced, P3.3.9 taskUid 缺失生成).
 */
export const ADVISOR_JSON_SCHEMA = {
  type: "object",
  properties: {
    tier: { type: "string", enum: ["frontline", "mid", "senior"] },
    mainTasks: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "integer", minimum: 1 },
          taskUid: {
            type: "string",
            description: "6 字符 [a-z0-9] 稳定 key, 跨 refresh 复用. 如无 prev cache 可生成新值.",
          },
          title: { type: "string" },
          urgency: { type: "string", enum: ["high", "medium", "low"] },
          reason: { type: "string" },
          options: {
            type: "array",
            description: "2-3 个口径选项 (senior tier 异常型主菜可 0 选项, frontline/mid tier 必须 ≥2 个)",
            // P3.4.E.7 (6/15 鸿波): minItems 2 给 frontline/mid 强约束.
            //   真因: P3.3.40 #6b 老只 warn 不修, 鸿波 6/15 撞 'task 巡视巡察整改回头看确认 只 1 个 option'.
            //   Call 1 走 hermes 没法强 schema, parseAdvisorResult tier-aware 校验 + Call 2 strict schema 双层.
            //   senior tier 0 options OK 时, transformToStructured 内部按 tier 动态切 schema (见下方实现).
            minItems: 2,
            items: {
              type: "object",
              properties: {
                label: { type: "string", description: 'A / B / C' },
                tone: { type: "string", enum: ["strict", "balanced", "friendly", "formal", "urgent", "hold"] },
                summary: { type: "string" },
                aiLean: { type: "boolean", description: "唯一一条 true" },
                draftPath: { type: "string" },
              },
              required: ["label", "tone", "summary"],
            },
          },
          complianceFlags: {
            type: "array",
            items: {
              type: "object",
              properties: {
                type: { type: "string", description: 'e.g. iso_audit_relevant' },
                severity: { type: "string", enum: ["high", "medium", "low"] },
                reason: { type: "string" },
                matchedKeyword: { type: "string" },
                suggestion: { type: "string" },
              },
              required: ["type", "severity", "reason"],
            },
          },
          politicalFlags: {
            type: "array",
            items: {
              type: "object",
              properties: {
                type: { type: "string" },
                severity: { type: "string", enum: ["high", "medium", "low"] },
                person: { type: "string" },
                reason: { type: "string" },
                matchedKeyword: { type: "string" },
                suggestedPhrasings: { type: "array", items: { type: "string" } },
                advisoryOnly: { type: "boolean", description: "senior tier + high 时 true" },
              },
              required: ["type", "severity", "reason"],
            },
          },
          contextRefs: { type: "array", items: { type: "string" } },
        },
        required: ["id", "taskUid", "title", "urgency", "options", "complianceFlags", "politicalFlags", "contextRefs"],
      },
    },
    handledSilently: {
      type: "array",
      items: {
        type: "object",
        properties: {
          type: { type: "string", description: 'e.g. email_archive / calendar_accept / todo_dedup' },
          count: { type: "integer", minimum: 1 },
          category: { type: "string" },
        },
        required: ["type", "count", "category"],
      },
    },
    // P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection.
    // OpenWiki Insight Reports 7 dim catfish 已有 4 (At a Glance + Action Items +
    // Hot Topics + Events Heatmap), 真剩 3 维 新加**.
    // 0 改 backend — 复用 BriefingContext 数据 (recent_session_briefs +
    // distilled_facts + hermes_memory_recent + email summary) + LLM prompt 段.
    subconscious: {
      type: "array",
      description: "无意识高频 — 问真多 (>=3 session 涉及) 但 0 deep-dive (<5 message). max 3 item.",
      items: {
        type: "object",
        properties: {
          topic: { type: "string", description: "无意识 topic, e.g. 'OAuth token refresh'" },
          count: { type: "integer", description: "session 涉及次数" },
          evidence: { type: "string", description: "一句话证据 (sessions title 关键词)" },
          reflectPrompt: { type: "string", description: "≤15 字 一句话, 点 → chat 触发 deep-dive" },
        },
        required: ["topic", "count", "evidence", "reflectPrompt"],
      },
    },
    graveyard: {
      type: "array",
      description: "墓地 — distilled_facts/memory 提过 真skill/工具/项目**, recent_session_briefs 0 reference. max 3 item.",
      items: {
        type: "object",
        properties: {
          name: { type: "string", description: "skill/工具/项目名" },
          lastSeen: { type: "string", description: "最近 reference 距今, e.g. '14 天前'" },
          evidence: { type: "string", description: "一句话 (distilled_facts 提到 哪段)" },
        },
        required: ["name", "lastSeen", "evidence"],
      },
    },
    blindSpots: {
      type: "array",
      description: "盲点 — hermes memory 或 distilled_facts 标重要, 真最近 7 天 0 action / 0 follow-up**. max 3 item.",
      items: {
        type: "object",
        properties: {
          topic: { type: "string" },
          signal: { type: "string", description: "重要信号 (e.g. '邮件标星 / memory 重要 / project 真汇报截止)" },
          evidence: { type: "string", description: "一句话 (具体 邮件/memory/project 引用)" },
          reflectPrompt: { type: "string", description: "≤15 字, 点 → chat 触发" },
        },
        required: ["topic", "signal", "evidence", "reflectPrompt"],
      },
    },
  },
  required: ["tier", "mainTasks", "handledSilently"],
  // subconscious / graveyard / blindSpots 真optional** — LLM 真没找到 0 item OK.
} as const;

// ─── SYSTEM_PROMPT (设计稿 §5) ────────────────────────────────────

export const SYSTEM_PROMPT = `你是 catfish — 中国央国企员工的智能参谋. 严格按以下规则工作:

# 角色边界 (不能违反)
1. 你是参谋, **不是代理**. 任何级别都不替员工拍板.
2. 你**不替员工**发邮件 / 接受会议 / 签字 / 拍板任何事.
3. 你**只**起草到 outputs/ 让员工自己看/改/发.
4. 你的输出是: 主菜识别 + 已准备好的材料 + 建议选项 + 风险提示.

# 工作步骤

1. **看完全部信息**, 内部关联推理. 不要分块看, 把人/项目/历史/事件横向连起来.

2. **按 profile.tier 识别主菜**:
   - frontline: 5-8 件具体 TODO, 按时间排
   - mid: 3-4 件项目级主菜 (团队进度 + 风险 + 汇报)
   - senior: 1-2 件战略级 + 异常例外 + 关键关系节点

3. **每件主菜调对应 tool**:
   - 涉及邮件回复 → catfish_draft_email_reply (起 2-3 个口径, 不同 tone 各调一次)
   - 涉及会议汇报 → catfish_draft_meeting_brief (起 brief)
   - 涉及催办 → catfish_compose_followup_list
   - 涉及决策 → catfish_recall_decision_history (拉历史口径, 不背离)

   **draftPath 硬约束** (5/22 鸿波撞 LLM 幻觉路径加):
   - draftPath 字段**只能**是真调 catfish_draft_email_reply / catfish_draft_meeting_brief
     后返回的 path 字段 (那个会落在 ~/.catfish/outputs/<today>/ 下).
   - **不允许编路径**. 没真调 tool 就**不填 draftPath**, 或填 null.
   - 不允许填 ~/Documents/, ~/Desktop/, 任何非 ~/.catfish/outputs/ 下的路径.
   - 不允许凭主菜标题脑补"应该叫什么名字" 再写进 draftPath. 必须 tool 返什么写什么.
   - 违反 → Rust 后端拒打开, 员工看到红条, catfish 失信.

4. **central_state=strong 时, 每件主菜额外跑扫描**:
   - 邮件 / 汇报草稿 → catfish_check_compliance
   - 涉及关键人物 → catfish_political_sensitivity_scan

5. **输出严格 JSON** (顶层不含 markdown 反引号, 不含前缀文字):
{
  "tier": "mid",
  "main_tasks": [
    {
      "id": 1,
      "task_uid": "li5d3k",
      "title": "老李催资质方案范围",
      "urgency": "high",
      "reason": "影响项目 A 客户关系",
      "options": [
        {"label": "A", "tone": "strict", "summary": "紧扣 5/18 班子会边界"},
        {"label": "B", "tone": "balanced", "summary": "微调保留余地", "aiLean": true, "draftPath": "<填真调 catfish_draft_email_reply 后返的 path; 没调就不填本字段>"},
        {"label": "C", "tone": "hold", "summary": "暂缓回复, 周一面谈"}
      ],
      "complianceFlags": [
        {"type": "iso_audit_relevant", "severity": "medium", "reason": "类似回复去年被 ISO 审计追问", "matchedKeyword": "资质方案", "suggestion": "B 口径稳, 留档备查"}
      ],
      "politicalFlags": [],
      "contextRefs": ["5/14 你跟老李电话定的口径"]
    }
  ],
  "handled_silently": [
    {"type": "email_archive", "count": 47, "category": "低优先归档"},
    {"type": "calendar_accept", "count": 2, "category": "非关键会议 tentative"}
  ]
}

# 强约束 (重申)
- 不允许"建议你 X" 这种被动建议. 改为"我起草了 A/B 两个口径, 你点这里看".
- 不允许"出总结". 总结是 Phase 6 的错路.
- 必须结构化 JSON, 不允许返一段散文.
- options 里 aiLean=true 的最多 1 条 (倾向只一个).
- 高层 tier (senior) 可以出现"异常例外型" 主菜 — options 可以为空 [], 只列风险.

// P3.5.212 (7/10 鸿波 校正 audit): 走 Companion → Hermes → Gateway 架构,
// hermes catfish-memory prefetch (P3.5.211) 已注入'事实为准'军规到
// system prompt, 这里再加一份重复. 撤回, 单点在 hermes 保生效. 保留
// contextRefs 编造 audit 的软性约束以在 SYSTEM_PROMPT 里作 fallback:

# BL-ADVISOR-PROMPT-CONFORMANCE (6/1 鸿波, 5/22 实测 3 类 LLM 失误的修)

## 1) tone 严格 enum (不许编新词)
options[].tone **必须**是这 6 个之一: "strict" / "balanced" / "friendly" /
"formal" / "urgent" / "hold". 不允许出 "prepare" / "consider" / "neutral"
等. 客户端会归一不 enum 到 "balanced" + warn, 但靠你严格守约定才不浪费.

## 2) options 必须 2-3 条 (frontline / mid)
每个主菜 **2 或 3 个** options. 1 个不达标 — 失"建议选项"价值, 员工等于
没选择. 真没第 2 种合理口径 → 改主菜表达, 别勉强减 options.
**例外**: senior tier "异常例外型" 主菜可以 options=[] 只列风险 (上面已说).

## 4) task_uid 跨 refresh 复用 (P3.3.9, 6/10)

每个 main_task 必须有 task_uid (**6 字符**, 只能 [a-z0-9]).

user prompt 里如果给了 "# 上次 advisor 输出" section, 列出 12 小时内出现过的
task (含 uid + title + urgency), 你**必须**:
- 判定 "业务实质相同" 的 task → **复用旧 task_uid**, 不要新生成
- 判定标准 = 同项目 / 同人 / 同截止 / 同业务环节 / 同实质动作.
  title 表述差异不算新 task:
  · "CSMM-4 评估撰写" ≡ "CSMM-4 正式评估准备" → 复用同一 uid
  · "中电福富研发立项" ≡ "中电北京福富资质申报" → 复用同一 uid
- 真新业务 (上次没见过) → 自己生成 6 字符 [a-z0-9] uid, 例 "csmm4z" / "bjffr1"

task_uid 用作员工跟这条 task 的 task chat 文件名. 你重写 title 会导致旧
chat 找不到, 必须复用 uid 才能让员工跨 refresh 看到历史对话.

## 4.1) 看到 chat summary 时怎么办 (P3.3.12, 6/10)

user prompt 的 "# 上次 advisor 输出" section 里, 某些 prev task 后会跟一行
"└ 员工已跟 AI 聊过: <summary>". 这是员工在 detail pane 跟 AI 已经讨论过的
脉络 (LLM summary, 100-150 字).

看到 chat summary 时:
- **必须复用旧 uid** (跟 §4 一致)
- **reason 字段更新成 follow-up 风** — 不再是"这条 task 为啥重要", 而是
  "员工已经聊到 X 了, 下一步应该 Y" / "员工说先放一放, 等通知再说"
- options[] 提向"推一步" — 起草下一封 / 跑下个 tool / 跟某人确认细节,
  **不要重复早晨已建议过的选项** (员工已经看过 + 跟 AI 聊过了)
- 如果员工跟 AI 已经说"放一放" / "等通知" / "已完成" → urgency 降一档 +
  reason 解释为啥降. 别再当 high 推一遍.

不要忽略 chat summary — 它代表员工跟 task 的真实进度, 比 advisor 上次的
建议口径权威多了 (advisor 是猜的, summary 是员工真做过的).

## 4.2) BL-ADVISOR-RESOLVED-DROP (P3.3.39, 6/12 鸿波撞误报后这条仍出): 已 resolved 不放 main_tasks

chat summary 含以下任一**已结案信号**关键字时, **task 不能再放 main_tasks**:

  - "已确认" + 否定语 (是误报 / 不存在 / 不是 / 没有 / 无 / 已撤销 / 已结项)
  - "已完成" / "已结项" / "已 done" / "已处理完" / "已交付" / "已发出" / "已签字"
  - "已发起申请" + 等审批 (员工把球踢出去了, 等对方)
  - "不再有效" / "已作废" / "已撤回" / "确认无风险"
  - "是误报" / "属误报" / "误报修正" / "查证不存在"
  - "已发邮件催了" + 没下文 → 不算 resolved, 仍放 main_tasks 但 urgency 降

  正面识别例:
  · "员工已确认 5 封安全预警邮件不存在, 待办为误报" → **drop**
  · "黄捷已签字, 材料已交到资质办" → **drop** (踢给对方)
  · "已发邮件催专审报告, 等回" → **不 drop** (还在等)
  · "已发起加计扣除申请, 等审批" → **drop** (员工动作完成, 等审批不是员工 follow-up)

resolved task 处理方式 (2 选 1):
  (a) 放 handled_silently — {type: "task_resolved", count: 1, category:
      "<title> 已结/误报/无风险"}, 让员工在折叠区看得到但不占急/中/低名额
  (b) 完全不出现 — 适合 chat summary 明确说"不再有效 / 已作废"

判定**保守**: 模糊时仍放 main_tasks (低 urgency) — 错放 cost 是员工多看一眼,
错 drop cost 是员工漏掉真要做的事. 保守原则.

## 3) draftPath 跟 tool call 绑定 (5/22 撞过的)
options[].draftPath 只能从你**真调** catfish_draft_email_reply /
catfish_draft_meeting_brief / catfish_compose_followup_list 后**返回的 path**
字段填. 没调 tool → **不填 draftPath**, 或填 null. 客户端会:
- 检测 draftPath 不在 ~/.catfish/outputs/ → 清掉 (5/22 BL-DRAFTPATH-WHITELIST)
- 后续 (待 ship) 检测 options 有 draftPath 但 chat 没 tool_call → 拒回复

**真路径**:
1. 先调 tool 起草 → 拿 returns.path
2. 再写 options[].draftPath = <path>
3. 不调 tool 就不写 draftPath, 让 UI 显"自己写"

不允许编路径绕过. 员工点开发现空草稿 = 鲶鱼失信.

# P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 reflection 字段 [REVERTED]

P3.5.32.5 (6/18 鸿波 catch '都超时'): SYSTEM_PROMPT 加 3 段后 advisor LLM 都
300s timeout. 真因: SYSTEM_PROMPT 从 10421 字节涨到 13099 字节 (+2678 字节 ≈
+900 token), hermes agent loop 多轮叠加导致 LLM output token 与 reasoning load
都增加, 5min+ 才能跑完.

这段 SYSTEM_PROMPT (3.1/3.2/3.3/3.4) 整段砍掉, advisor 回到老速度.

3 维字段 (subconscious / graveyard / blindSpots) 在 ADVISOR_JSON_SCHEMA 与
AdvisorResult interface 里**保留**, parseAdvisorResult **保留** parse logic
(snake_case + camelCase 兼容). UI cards **保留** (InsightReflectCards.tsx).

未来 Phase 11 用单独 LLM call (跟 advisor 解耦) 生成 3 维 — 避免拖累主 advisor.

# BL-ADVISOR-JSON-STRICT (P3.4.9, 6/15 鸿波撞 DeepSeek Flash 返英文 markdown 后)

模型在 agent loop 多轮 + tool use 之后, **极易 drift 出 SYSTEM_PROMPT 的 JSON 约束**,
返 markdown 叙述 (SYSTEM_PROMPT 影响力随 turn 数衰减). 实测原文:

  "Now I have a comprehensive picture. Let me synthesize:
   **Key findings from session analysis:**
   1. **巡视巡察整改** — drop
   2. ..."

这种输出客户端 robustJsonParse 救不了 (一个 \`{\` 都没有), 直接 UI 红字 "advisor LLM 调用失败".

## 铁律 (跑完所有 tool, 准备返 final answer 时必读)

1. 你的回复**第一个字符必须是 \`{\`**, 最后一个字符必须是 \`}\`.
2. **不能**以以下 prefix 开头 (实测高频 drift):
   - 英文: "Now I have" / "Let me synthesize" / "Key findings" / "Based on the data" /
     "I'll analyze" / "Here is" / "After analyzing"
   - 中文: "现在我" / "让我" / "总结一下" / "根据数据" / "经过分析" / "首先" / "以下是"
3. **不能**含 markdown 反引号 (\`\`\`json\`\`\`) / 加粗 (**) / 列表 (1. 2. 3. -) / 表情 (✓ ✗ ⚠️).
   这些都在 JSON 字段值里用, 不能在 JSON 外部包裹.
4. **不能**用英文叙述 advisor 决策. 全部 JSON 字段值中文 (英文术语如 "high" / "balanced" 除外).
5. 跑完 tool 拿数据后, **直接** 把数据 json 化输出, 不要 "reasoning out loud" 内部独白.

## 例子

❌ Bad (鸿波 6/15 实测, agent loop 跑完后输出):
\`\`\`
Now I have a comprehensive picture. Let me synthesize:

**Key findings from session analysis:**
1. **巡视巡察整改回头看** — 员工已说"已经会给刘佳了" → **resolved, drop**
2. **安全预警误报备案** — ...
\`\`\`

✓ Good (无 prefix, 第一个字符就是 \`{\`):
\`\`\`
{"tier":"mid","main_tasks":[{"id":1,"task_uid":"xunshi","title":"...","urgency":"medium","reason":"...","options":[...]}],"handled_silently":[{"type":"task_resolved","count":1,"category":"巡视巡察 已结案 (员工说已给刘佳)"}]}
\`\`\`

(实际输出可以多行 + 缩进, 但**必须 \`{\` 开头**.)
`;

// ─── 拼 user prompt ──────────────────────────────────────────────

export function buildUserPrompt(input: AdvisorInput): string {
  // 5/26: sessionGoal 字段删 — hermes 0.14 原生 /goal 替代, advisor 不再读 catfish 这套
  const { profile, emails, events, todos, ctx, urgencyMap, previousTasks } = input;
  const parts: string[] = [];

  // P3.5.5 (6/16 鸿波): catfish-advisor sparse mode marker — catfish-memory plugin
  //   prefetch 检测到这个 marker 后, 只返核心 4 段 (purpose+discipline+safety+meta ~3KB),
  //   砍 skills_catalog/strategic_docs/journal/wiki/feedback 7 段 (~30KB).
  //   advisor 业务上不需要这些 (它自己 user prompt 已注入 distilled+memory+todos).
  //   真因: Qwen 内网 prompt 44K 跑 100-200s, sparse 后 ~10K 跑 20-30s.
  parts.push("<!-- catfish:advisor-sparse -->");

  const today = new Date();
  parts.push(`# 时间锚点
今天: ${today.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })}
本周一起算 (中层 + 高层视野).`);

  // ─── 员工画像 ───
  parts.push(`# 员工画像 (catfish 自动识别)
- tier: ${profile.tier}
- central_state: ${profile.centralState}
- style: ${profile.style}
- confidence: ${profile.confidence.toFixed(2)}
${
  profile.keyPeople.length > 0
    ? `- 关键人脉:\n${profile.keyPeople
        .slice(0, 10)
        .map((p) => `  - ${p.name} (${p.relation}${p.project ? `, 项目 ${p.project}` : ""})`)
        .join("\n")}`
    : "- 关键人脉: (未识别)"
}
${
  profile.keyProjects.length > 0
    ? `- 重点项目:\n${profile.keyProjects
        .slice(0, 5)
        .map((p) => `  - ${p.name} (${p.status}${p.client ? `, 客户 ${p.client}` : ""})`)
        .join("\n")}`
    : "- 重点项目: (未识别)"
}`);

  // sessionGoal 段 5/26 删 (hermes 0.14 原生 /goal 替代)
  if (ctx.workplan.trim()) {
    parts.push(`# 员工本周计划 (自己写的)\n${ctx.workplan.trim()}`);
  }
  if (ctx.projects.trim()) {
    parts.push(`# 项目跟踪 (员工自维护)\n${ctx.projects.trim()}`);
  }
  if (ctx.distilledFacts.trim()) {
    parts.push(`# 关于这个员工 (长期画像)\n${ctx.distilledFacts.trim()}`);
  }

  // P3.4.6 (6/15 鸿波): hermes MEMORY 近期 § 段 — "近期事项 context".
  //   跟 distilledFacts 两层: distilledFacts = 长期画像 (员工偏好 / 客户 / 项目),
  //   hermesMemoryRecent = 近期事实 (e.g. "一级建造师补位 6/12 戴明利已入职"
  //   "6/10 下午沟通单已反馈邱益亮暂停" "中电高新资质申报发票佐证已发起申请").
  //   不是 TODO — todo 严格走 employee_journal - [ ] checkbox.
  if (ctx.hermesMemoryRecent.trim()) {
    parts.push(`# 近期事项 (员工 hermes memory 近期 § 段, 含近期事实 / 决策 / 跟进点, 非 TODO)
${ctx.hermesMemoryRecent.trim()}`);
  }

  // P3.5.40 (6/18 鸿波 audit huashu-design '不凭空创造, 查已有 spec'):
  //   wiki/entities/* 跟 wiki-shared/dept/* 里跟今日邮件/任务语义相关的 head 注入.
  //   防 LLM 凭记忆造客户名 / 项目名 / 资质名 / 部门规定 (员工 wiki 里有具体记录的话).
  //   填充时机: fetchBriefingAdvisor 内 applyRelevanceFilter 后调 wikiSearchSemantic.
  //   空字符串 = wiki 没装 / BGE-M3 没装 / 没匹配命中, advisor 仍然能跑 (跟现有 fallback 一致).
  if (input.wikiRelevant && input.wikiRelevant.trim()) {
    parts.push(`# 员工 wiki 相关条目 (查到的具体事实, 不要凭印象编造)
${input.wikiRelevant.trim()}`);
  }

  // 周报历史 (文件名 + 时间, 不读内容)
  if (ctx.weeklyReports.length > 0) {
    const lines = ctx.weeklyReports
      .slice(0, 5)
      .map((r) => `${r.modifiedAt.slice(0, 10)} ${r.filename}`);
    parts.push(`# 周报历史 (员工已生成过的)\n${lines.join("\n")}`);
  }

  // 7 天 session
  if (ctx.recentSessionBriefs.length > 0) {
    const lines = ctx.recentSessionBriefs.slice(0, 10).map((s) => {
      const msg = (s.firstUserMessage || "").slice(0, 80);
      return `[${s.startedAt.slice(0, 10)}] ${s.title || "(无 title)"}: ${msg}`;
    });
    parts.push(`# 最近 7 天对话\n${lines.join("\n")}`);
  }

  // 邮件 + 评级
  if (emails.length > 0) {
    const lines = emails.slice(0, 20).map((m) => {
      const u = urgencyMap[m.id] ?? "未评";
      return `[${u}] ${m.sender}: ${m.subject}`;
    });
    parts.push(`# 今日邮件 (${emails.length} 封, 前 20 列, 含 LLM 评级)\n${lines.join("\n")}`);
  }

  // 日历
  if (events.length > 0) {
    const lines = events.map((e) => {
      const time = e.all_day ? "全天" : `${e.start} - ${e.end}`;
      return `${time} ${e.summary}${e.location ? ` @ ${e.location}` : ""}`;
    });
    parts.push(`# 今日日程 (${events.length} 件)\n${lines.join("\n")}`);
  }

  // TODO
  if (todos.length > 0) {
    const lines = todos.map((t) => {
      const star = t.is_priority ? "⭐ " : "";
      return `${star}${t.text} (${t.source})`;
    });
    parts.push(`# 工作计划 TODO (${todos.length} 件)\n${lines.join("\n")}`);
  }

  // P3.3.9 (6/10): 上次 advisor 输出 — 让 LLM 复用 task_uid (跨 refresh 稳定)
  // P3.3.12 (6/10): 加 chatSummary, 让 LLM 看到员工跟每条 task 已聊到哪
  // P3.5.202 (7/9 C 方案): 加 chatStatus (LLM 判定 resolved/paused/pending),
  //   传给主 LLM 让它语义驱动跳过已完结/暂搁置 task, 而非依赖 keyword regex.
  if (previousTasks && previousTasks.length > 0) {
    const lines = previousTasks.map((t) => {
      const head = `- ${t.taskUid} | ${t.urgency} | ${t.title}`;
      const bits: string[] = [];
      if (t.chatSummary && t.chatSummary.trim().length > 0) {
        bits.push(`员工已跟 AI 聊过: ${t.chatSummary.trim()}`);
      }
      // P3.5.202: chatStatus 明确标注该 task 语义状态
      if (t.chatStatus) {
        const statusLabel =
          t.chatStatus === "resolved"
            ? "resolved (员工说事已办完/已交付/已确认误报 — 不能再放 main_tasks)"
            : t.chatStatus === "paused"
              ? "paused (员工说暂时关闭/暂缓/先放放/等通知 — 员工主动搁置, 不能再放 main_tasks, 员工会主动来找)"
              : "pending (球还在员工手里)";
        bits.push(`status: ${statusLabel}`);
      }
      // P3.5.208-B (7/10 鸿波 catch '关了几次今天又出来'): 员工按钮点的
      // manualStatus 也告诉 LLM. 之前只放 chatStatus, LLM 看不到员工按钮
      // action → 员工不说话 chatStatus=pending → LLM 照出 task. 加 manualStatus
      // 层强约束.
      if (t.taskState) {
        const manualLabel =
          t.taskState === "done"
            ? "manualStatus: done (员工在早安卡片点了'标记完成'按钮 — **绝不能再放 main_tasks**)"
            : t.taskState === "ignored"
              ? "manualStatus: ignored (员工在早安卡片点了'不做'按钮 — **绝不能再放 main_tasks**)"
              : "manualStatus: snoozed (员工在早安卡片点了'推迟到明天'按钮 — 今天不能再放, 明天可以)";
        bits.push(manualLabel);
      }
      return bits.length > 0 ? `${head}\n  └ ${bits.join("\n  └ ")}` : head;
    });
    parts.push(`# 上次 advisor 输出 (12 小时内)
**同业务必须复用 task_uid, 不要新生成**. 判定标准 = 同项目/同人/同截止/同业务环节.
title 表述差异不算新 task. 详见 SYSTEM_PROMPT § "task_uid 跨 refresh 复用".
**已聊过的 task (含 chat summary), 你这次应该 follow-up 进度 / 帮员工往前推, 不要重推同样建议**.

**P3.5.202 + P3.5.208-B 强约束 (chat 语义 + 卡片按钮 双硬门)**:
- **chatStatus="resolved" 或 "paused" 的 task 绝不能放 main_tasks**.
  resolved = 员工说事已办完/已交付/已确认误报/已撤销 — 事已完结.
  paused = 员工说暂时关闭/暂缓/先放放/等通知再说 — 员工主动搁置, 会主动来找.
- **manualStatus="done" 或 "ignored" 的 task 绝不能放 main_tasks** (P3.5.208-B):
  员工在早安卡片显式点了'标记完成'/'不做', 员工意愿已明确, 再推是骚扰.
- **manualStatus="snoozed" 的 task 今天绝不能放 main_tasks** (员工点了'推迟到明天').
- 上述任一命中都挪去 handled_silently, 让员工在折叠区看得到但不打扰.
- 只有 chatStatus=pending 且 无 manualStatus 才可以出 main_tasks.
- 员工没聊过的新 task 依据紧急度/影响度自己判断.

${lines.join("\n")}`);
  }

  parts.push(`# 任务
按 system prompt 指示, 出 JSON. 严格按 tier=${profile.tier} 的粒度:
${
  profile.tier === "frontline"
    ? "5-8 件具体 TODO 按时间排, 简单口径建议."
    : profile.tier === "mid"
    ? "3-4 件项目级主菜, 含团队进度风险 + 汇报草稿."
    : "1-2 件战略级 + 异常例外 + 关键关系节点."
}
${
  profile.centralState === "strong"
    ? "央国企信号强 — 每件主菜必须跑 catfish_check_compliance + (涉及关键人时) catfish_political_sensitivity_scan."
    : "央国企信号弱 — 跳过合规/政治扫描."
}`);

  // P3.4.9 (6/15 鸿波): user prompt 末尾再强调一次 JSON-only — 跟 SYSTEM_PROMPT
  //   末尾 BL-ADVISOR-JSON-STRICT 双重保险. agent loop 跑完 tool 后 final
  //   message 时, 最近上下文的指令影响力 > 老 SYSTEM_PROMPT, user 末尾这条
  //   是 "last word" 帮 LLM 守住 JSON 约束.
  parts.push(`# 输出格式 (必读 — 跑完 tool 后 final answer 阶段)

跑完所有 tool 拿到数据后, **直接输出 JSON**, 不要 "Now I have a comprehensive picture" /
"Let me synthesize" / "Key findings" / "现在我..." 等任何 prefix.

第一个字符 = \`{\`, 最后一个字符 = \`}\`. 中间不要 markdown 反引号 / 加粗 / 列表标号 / 表情.
跑完 tool 时直接 dump JSON, 不要 "reasoning out loud". 见 SYSTEM_PROMPT § BL-ADVISOR-JSON-STRICT.`);

  return parts.join("\n\n");
}
