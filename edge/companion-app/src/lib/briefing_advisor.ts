/** BL-ADVISOR-BRIEFING (5/21 Phase 7 第 4+5 步): 智能参谋早安 LLM 调用层.
 *
 * 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §5 (SYSTEM_PROMPT) + §7 (UI 渲染输入)
 *
 * 替代 Phase 6 的 briefing_workplan.ts (已砍). 核心差异:
 *   - prompt 让 LLM 当"参谋"不是"作家" — 不出总结, 出主菜 + 选项 + 草稿调用
 *   - 按 profile.tier 出不同粒度的主菜数 (一线 5-8 / 中层 3-4 / 高层 1-2 + 异常)
 *   - 央国企信号 strong 时主动调 check_compliance + political_sensitivity_scan
 *   - 严格 JSON 输出 (UI 渲染成 ActionCard list)
 *
 * 调用模式 (跟 5/21 学到的 Tauri webview suspend 经验):
 *   - **不挂 AbortSignal** — Tauri 失焦会 abort in-flight fetch
 *   - 走 backendUrl (8642 hermes proxy) + ?catfish_skip_identity=1 query (5/21 CORS 修)
 *   - 调 fetchMergedBriefing 同款 fetchWithAuth
 *
 * 注: 这里只管 LLM 调用 + 解析. 数据采集 (emails / events / todos / profile / ctx)
 * 由 caller (AdvisorView component) 拉好传进来. 单一职责.
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";
import type { Profile } from "./profile";
import type {
  BriefingContext,
  CalendarEvent,
  EmailDigestItem,
  JournalTodo,
} from "./tauri";

// 跟 briefing_workplan / briefing 同款 — 不带 X-Catfish-* header, 走 query param (5/21 CORS 修)
const SERVICE_LLM_HEADERS = { "Content-Type": "application/json" };
const SERVICE_LLM_QUERY = "?catfish_source=companion-advisor&catfish_skip_identity=1&catfish_internal=1";

// P3.4.E (6/15 鸿波): Call 2 transformToStructured 直走 catfish-gateway 8999 (LiteLLM passthrough),
//   bypass hermes 8642 agent loop. 真因: hermes _handle_chat_completions 不读 client tools / tool_choice
//   (api_server.py:1820 把 request 重 framing 成 agent run), 必须直 LiteLLM 才能用 strict function calling.
//   catfish_direct=1 query 让 me.ts isGatewayDirectPath 命中走 OAuth path (跟 profile.ts 同款).
const ADVISOR_DIRECT_QUERY = "?catfish_source=companion-advisor-transform&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

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
const ADVISOR_JSON_SCHEMA = {
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
    // 真**OpenWiki Insight Reports 7 dim** 真**catfish 已有 4 (At a Glance + Action Items +
    // Hot Topics + Events Heatmap), 真**剩 3 维 新加**.
    // 真**0 改 backend** — 真**复用 BriefingContext 数据 (recent_session_briefs +
    // distilled_facts + hermes_memory_recent + email summary)** + LLM prompt 段.
    subconscious: {
      type: "array",
      description: "无意识高频 — 问真多 (>=3 session 涉及) 但 0 deep-dive (<5 message). max 3 item.",
      items: {
        type: "object",
        properties: {
          topic: { type: "string", description: "无意识 topic, e.g. 'OAuth token refresh'" },
          count: { type: "integer", description: "session 涉及次数" },
          evidence: { type: "string", description: "一句话证据 (sessions 真**title 关键词)" },
          reflectPrompt: { type: "string", description: "≤15 字 一句话, 点 → chat 触发 deep-dive" },
        },
        required: ["topic", "count", "evidence", "reflectPrompt"],
      },
    },
    graveyard: {
      type: "array",
      description: "墓地 — distilled_facts/memory 提过 真**skill/工具/项目**, recent_session_briefs 0 reference. max 3 item.",
      items: {
        type: "object",
        properties: {
          name: { type: "string", description: "skill/工具/项目名" },
          lastSeen: { type: "string", description: "最近 reference 真**距今**, e.g. '14 天前'" },
          evidence: { type: "string", description: "一句话 (distilled_facts 真**提到 哪段)" },
        },
        required: ["name", "lastSeen", "evidence"],
      },
    },
    blindSpots: {
      type: "array",
      description: "盲点 — hermes memory 或 distilled_facts 标重要, 真**最近 7 天 0 action / 0 follow-up**. max 3 item.",
      items: {
        type: "object",
        properties: {
          topic: { type: "string" },
          signal: { type: "string", description: "重要信号 (e.g. '邮件标星 / memory 真**重要 / project 真**汇报截止)" },
          evidence: { type: "string", description: "一句话 (具体 邮件/memory/project 引用)" },
          reflectPrompt: { type: "string", description: "≤15 字, 点 → chat 触发" },
        },
        required: ["topic", "signal", "evidence", "reflectPrompt"],
      },
    },
  },
  required: ["tier", "mainTasks", "handledSilently"],
  // 真**subconscious / graveyard / blindSpots 真**optional** — 真**LLM 真**没找到 真**0 item OK**.
} as const;

// ─── 输出 schema (UI ActionCard 渲染输入) ───────────────────────

/** BL-ADVISOR-PROMPT-CONFORMANCE (6/1 鸿波): tone 严格 enum, parseMainTask
 *  把非 enum 归一到 'balanced' + warn. decisions.jsonl 留档一致性靠这个保证. */
export const VALID_TONES = [
  "strict",
  "balanced",
  "friendly",
  "formal",
  "urgent",
  "hold",
] as const;
export type AdvisorTone = typeof VALID_TONES[number];

export interface AdvisorOption {
  /** "A" / "B" / "C" */
  label: string;
  /** enum 严格 (parseMainTask 校验 + 非 enum 归一到 balanced). */
  tone: AdvisorTone;
  /** 这个选项的一句话总结 (给员工选时用) */
  summary: string;
  /** LLM 倾向哪个 — 唯一一条 true. UI 角标"我倾向" */
  aiLean?: boolean;
  /** 已存草稿绝对路径 (调 draft_email_reply / draft_meeting_brief 后填) */
  draftPath?: string;
}

export interface ComplianceFlag {
  type: string;       // "iso_audit_relevant" 等
  severity: "high" | "medium" | "low";
  reason: string;
  matchedKeyword?: string;
  suggestion?: string;
}

export interface PoliticalFlag {
  type: string;
  severity: "high" | "medium" | "low";
  person?: string;
  reason: string;
  matchedKeyword?: string;
  suggestedPhrasings?: string[];
  advisoryOnly?: boolean;  // senior tier + high 时 true, UI 渲染"提醒"非"建议改"
}

export interface MainTask {
  /** 当天主菜 id (LLM 自己排 1, 2, 3...) */
  id: number;
  /** P3.3.9 (6/10): 跨 refresh 稳定 key, 6 字符 [a-z0-9]. LLM 生成 + 上次 cache 注入
   *  让 LLM 同业务复用旧 uid. task_chat / decisions 等持久化用此 key, 不用 title
   *  (title 每次 refresh LLM 会重写). */
  taskUid: string;
  /** 一句话标题 */
  title: string;
  /** "high" / "medium" / "low" — UI 边框颜色 */
  urgency: "high" | "medium" | "low";
  /** 为什么这件事在主菜 (一句话) */
  reason?: string;
  /** 2-3 个口径选项 (高层异常型主菜可以 0 选项, 只列例外) */
  options: AdvisorOption[];
  /** check_compliance 跑的结果 */
  complianceFlags: ComplianceFlag[];
  /** political_sensitivity_scan 跑的结果 */
  politicalFlags: PoliticalFlag[];
  /** 上下文引用 — UI 用来显示 "🔗 历史: ..." */
  contextRefs: string[];
}

export interface HandledSilentlyItem {
  /** "email_archive" / "calendar_accept" / "todo_dedup" */
  type: string;
  /** 多少件 */
  count: number;
  /** 一句话理由 */
  category: string;
}

/** P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection items.
 *
 * 真**reflectPrompt 真**≤15 字 真**点 → useChat send(prompt) 真**触发 deep-dive**.
 */
export interface SubconsciousItem {
  /** 无意识 topic, e.g. "OAuth token refresh" */
  topic: string;
  /** session 涉及次数 */
  count: number;
  /** 一句话证据 — 真**LLM 指 哪些 sessions title 真**关键词** */
  evidence: string;
  /** ≤15 字 reflectPrompt, 点击 → chat 触发 deep-dive */
  reflectPrompt: string;
}
export interface GraveyardItem {
  /** skill/工具/项目名 */
  name: string;
  /** 最近 reference 真**距今** (e.g. "14 天前") */
  lastSeen: string;
  /** 一句话证据 (distilled_facts 真**提到 哪段) */
  evidence: string;
}
export interface BlindSpotItem {
  /** topic */
  topic: string;
  /** 重要信号 (e.g. "邮件标星 / memory 重要 / project 汇报截止") */
  signal: string;
  /** 一句话证据 */
  evidence: string;
  /** ≤15 字 reflectPrompt */
  reflectPrompt: string;
}

export interface AdvisorResult {
  /** 跟 profile.tier 一致 — LLM 输出时回显, 让 UI 知道是按哪个 tier 渲染的 */
  tier: "frontline" | "mid" | "senior";
  /** 主菜 list, 按 tier 决定数量 */
  mainTasks: MainTask[];
  /** 已默认处理的事 (UI 折叠区) */
  handledSilently: HandledSilentlyItem[];
  /** P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 无意识高频 topic, max 3 */
  subconscious?: SubconsciousItem[];
  /** P3.5.32 Phase 10 — 装但 0 回顾 skill/工具/项目, max 3 */
  graveyard?: GraveyardItem[];
  /** P3.5.32 Phase 10 — 标重要但 0 action / 0 follow-up topic, max 3 */
  blindSpots?: BlindSpotItem[];
}

// ─── SYSTEM_PROMPT (设计稿 §5) ────────────────────────────────────

const SYSTEM_PROMPT = `你是 catfish — 中国央国企员工的智能参谋. 严格按以下规则工作:

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

function buildUserPrompt(input: AdvisorInput): string {
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
  if (previousTasks && previousTasks.length > 0) {
    const lines = previousTasks.map((t) => {
      const head = `- ${t.taskUid} | ${t.urgency} | ${t.title}`;
      if (t.chatSummary && t.chatSummary.trim().length > 0) {
        return `${head}\n  └ 员工已跟 AI 聊过: ${t.chatSummary.trim()}`;
      }
      return head;
    });
    parts.push(`# 上次 advisor 输出 (12 小时内)
**同业务必须复用 task_uid, 不要新生成**. 判定标准 = 同项目/同人/同截止/同业务环节.
title 表述差异不算新 task. 详见 SYSTEM_PROMPT § "task_uid 跨 refresh 复用".
**已聊过的 task (含 chat summary), 你这次应该 follow-up 进度 / 帮员工往前推, 不要重推同样建议**.

**P3.3.39 强约束**: chat summary 含 "已确认/是误报/不存在/已结项/已完成/已发起申请等审批"
等**已结案信号**时, 这条 task 必须挪去 handled_silently 或不出现, **绝不能再放 main_tasks**.
详细规则见 SYSTEM_PROMPT § 4.2 BL-ADVISOR-RESOLVED-DROP. 这是 6/12 鸿波明确反馈的 bug.

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

// ─── 入口 fetchBriefingAdvisor ──────────────────────────────────

export interface AdvisorInput {
  profile: Profile;
  emails: EmailDigestItem[];
  events: CalendarEvent[];
  todos: JournalTodo[];
  ctx: BriefingContext;
  urgencyMap: Record<string, string>;
  // sessionGoal 5/26 删 — hermes 0.14 原生 /goal 替代
  model: string;
  /** P3.3.9 (6/10): 上次 advisor 输出 (12h 内 cache), 让 LLM 复用 task_uid.
   *  P3.3.12 (6/10): 加 chatSummary — 员工跟这条 task 已聊过的 100 字 summary,
   *  让 advisor 看到员工已讨论过什么, 不再重推同样建议.
   *  fetchBriefingAdvisor 内部从 advisorCacheGet 拉, 不需要 caller 传. */
  previousTasks?: Array<{
    taskUid: string;
    title: string;
    urgency: string;
    /** P3.3.12: 跟 AI 已聊到哪 (100-150 字). 空字符串 = 没聊过 / summary 失败. */
    chatSummary?: string;
  }>;
}

/** 5/22 cold start 修锁: 同时只允许一个 advisor LLM call 跑.
 *  React StrictMode dev useEffect 双调 → fetchBriefingAdvisor 双跑 → LLM 调 2 次浪费 token + quota.
 *  跟 recomputeProfile 同款 in-flight 锁. */
let _advisorInFlight: Promise<AdvisorResult | null> | null = null;

/** 5/22 鸿波实盘: 公司内网 Qwen 拥堵, TTFT 44s, 多次撞 gateway 300s timeout, UI 干等 5min.
 *  客户端 60s timeout sentinel — fetch 不 abort (避 5/21 Tauri webview suspend bug,
 *  fetch 完成后 cache 还会写, 下次时段触发能用), 但 fetchBriefingAdvisor 早返
 *  TIMEOUT, 调用方走 stale cache fallback. */
export const ADVISOR_TIMEOUT = Symbol("advisor-timeout");
export type AdvisorFetchResult = AdvisorResult | null | typeof ADVISOR_TIMEOUT;
// P3.3.25 (6/11): 60_000 → 180_000.
//   gateway log 看 catfish-private-main (40K prompt + 私有推理) latency 70-100s
//   常态. 60s race timeout 几乎每次都触发, 走 stale fallback, 但 stale 是 null →
//   红条 "LLM 返空或解析失败". 改 180s 给私有模型充分时间, 也保留 fail-safe.
//   仍然不挂 AbortSignal — Tauri webview suspend 时 fetch 自然 pending, race 让
//   UI 不无限等. 后台 fetch 完成会写 cache, 下次时段触发能用.
// P3.4.8 (6/15 鸿波): 180_000 → 300_000.
//   真因 (从 ~/.hermes/logs/agent.log 看): advisor 一次 POST /v1/chat/completions
//   不是单次 LLM call, 是 hermes 跑完整 agent session — 8 次 API call + 多次
//   tool 调用 (read_file / session_search 一次 76s / tool_search / recall_decision_history),
//   总耗时 ~2:40. 180s 客户端 timeout 擦边超出 60-90s, 几乎每次撞超时走 stale fallback.
//   改 300s 给 agent loop 跑完时间. stale fallback 仍保留 (真 5min 还没回就是上游真挂).
const CLIENT_TIMEOUT_MS = 300_000;

/** 主入口. 不挂 AbortSignal (Tauri webview suspend 经验, 5/21 学到). */
export async function fetchBriefingAdvisor(input: AdvisorInput): Promise<AdvisorFetchResult> {
  // in-flight 锁: 已在跑就复用 promise
  if (_advisorInFlight) {
    console.log("[advisor] 已在跑, 复用 in-flight promise (StrictMode 双调防御)");
    // 复用 in-flight 也加同款 timeout race — 让连续两次调都同等享受超时保护
    return raceWithTimeout(_advisorInFlight);
  }

  // P3.4.E.6 (6/15 鸿波): 记开始时间, 后台 finally 算耗时判真 timeout / race 内完成.
  //   老 log "可能已 TIMEOUT 走 stale" 永远打 — 不论 race 是否真超时, 误导诊断
  //   方向 (P3.4.3 同款 pattern, 鸿波 6/15 撞到误以为 LLM 慢, 实际 race 内完成).
  const startMs = Date.now();
  const myPromise = _fetchBriefingAdvisorImpl(input);
  _advisorInFlight = myPromise;
  // 注: 不 await 整个 promise (它要 5min), 用 race 让本次调用早返;
  // myPromise 后台跑完后:
  //   - 清锁
  //   - 如果 result 非空, 写 cache (避免 5min 后真返回的成果被丢弃)
  try {
    return await raceWithTimeout(myPromise);
  } finally {
    void myPromise
      .then(async (result) => {
        if (!result) return;
        const elapsedMs = Date.now() - startMs;
        try {
          const { advisorCacheGet, advisorCacheSave } = await import("./advisor_cache");
          // P3.3.12 (6/10): save 前先读老 cache 拿到 taskChatSummaries — 老 cache
          // 里有 _fetchBriefingAdvisorImpl 阶段刚 pre-write 的 summaries, 直接 build
          // 新 cache 会丢. merge 进去.
          const oldCache = await advisorCacheGet().catch(() => null);
          await advisorCacheSave({
            computedAt: new Date().toISOString(),
            result,
            model: input.model,
            taskChatSummaries: oldCache?.taskChatSummaries,
          });
          // P3.4.E.6 (6/15 鸿波): 区分 race 内完成 / race 外完成. 实际多数 cache 写
          //   在 race 内 (主 caller 拿到结果 + cache 同步写), 老文案"可能已 TIMEOUT"
          //   不准, 误导. 真 timeout 时主 caller 已返 ADVISOR_TIMEOUT, 后台 promise
          //   仍在跑, 完成后才打这条 log.
          const elapsedSec = (elapsedMs / 1000).toFixed(1);
          if (elapsedMs < CLIENT_TIMEOUT_MS) {
            console.log(
              `[advisor] 后台完成, 已写 cache (race 内完成 ${elapsedSec}s < timeout ${CLIENT_TIMEOUT_MS / 1000}s, 主 caller 已用上结果)`,
            );
          } else {
            console.log(
              `[advisor] 后台完成, 已写 cache (race 外完成 ${elapsedSec}s >= timeout ${CLIENT_TIMEOUT_MS / 1000}s, 主 caller 已走 stale fallback, cache 下次时段触发用)`,
            );
          }
        } catch (e) {
          console.warn("[advisor] 后台写 cache 挂:", e);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (_advisorInFlight === myPromise) {
          _advisorInFlight = null;
        }
      });
  }
}

// ─── P3.5.4 (6/16 鸿波): BGE-M3 相关性筛选 ────────────────────────────
//
// distilled_facts / hermes memory § / prev_tasks 都按今天输入 (todos+emails+events)
// 算语义相关性, top-K 注入. 砍 prompt 50%+, advisor Call 1 不再 truncated.
//
// 失败 fallback 返原 input (model 缺 / embed 异常都 silent).
// caller 拿到的"过滤后 input" 喂 buildUserPrompt, buildUserPrompt 不需要改 — 它直接读
// ctx.distilledFacts / ctx.hermesMemoryRecent / previousTasks, 我们只是替换这 3 个字段值.

const RELEVANCE_TOP_K_DISTILLED = 3;     // distilled_facts 段保留前 N
const RELEVANCE_TOP_K_MEMORY = 5;        // memory § 保留前 N
const RELEVANCE_TOP_K_PREV_TASKS = 5;    // prev_tasks 保留前 N (LLM 复用 task_uid)

async function applyRelevanceFilter(input: AdvisorInput): Promise<AdvisorInput> {
  // 构造 query: 今天员工真要处理的事 (todos + emails subject + events summary)
  const queryParts: string[] = [];
  if (input.todos.length > 0) {
    queryParts.push(input.todos.map((t) => t.text).join(" "));
  }
  if (input.emails.length > 0) {
    queryParts.push(
      input.emails.map((m) => `${m.sender}: ${m.subject}`).join(" "),
    );
  }
  if (input.events.length > 0) {
    queryParts.push(input.events.map((e) => e.summary).join(" "));
  }
  const query = queryParts.join("\n").trim();

  if (!query) {
    // 今天啥也没 (advisor 实际不会跑到这, 上游已 short-circuit), fallback
    return input;
  }

  // 切段
  const {
    splitDistilledFacts,
    splitMemoryRecent,
    rankRelevance,
    pickTopK,
  } = await import("./advisor_relevance");

  const distilledSegs = splitDistilledFacts(input.ctx.distilledFacts || "");
  const memorySegs = splitMemoryRecent(input.ctx.hermesMemoryRecent || "");
  const prevTaskTexts: string[] = (input.previousTasks ?? []).map((t) => {
    // prev_task embed 输入: title + chatSummary (chat summary 更精准反映"已聊过啥")
    return t.chatSummary ? `${t.title}\n${t.chatSummary}` : t.title;
  });

  // 没东西可筛 → fallback (不调 BGE-M3)
  if (
    distilledSegs.length <= RELEVANCE_TOP_K_DISTILLED &&
    memorySegs.length <= RELEVANCE_TOP_K_MEMORY &&
    prevTaskTexts.length <= RELEVANCE_TOP_K_PREV_TASKS
  ) {
    return input;
  }

  // 并发 3 个 rank
  const [distilledRes, memoryRes, prevRes] = await Promise.all([
    distilledSegs.length > RELEVANCE_TOP_K_DISTILLED
      ? rankRelevance(query, distilledSegs, "distilled")
      : Promise.resolve(null),
    memorySegs.length > RELEVANCE_TOP_K_MEMORY
      ? rankRelevance(query, memorySegs, "memory")
      : Promise.resolve(null),
    prevTaskTexts.length > RELEVANCE_TOP_K_PREV_TASKS
      ? rankRelevance(query, prevTaskTexts, "prev_task")
      : Promise.resolve(null),
  ]);

  // model 全挂 → fallback 全量
  if (
    distilledRes?.modelLoaded === false &&
    memoryRes?.modelLoaded === false &&
    prevRes?.modelLoaded === false
  ) {
    console.warn("[advisor relevance] BGE-M3 model 全挂, fallback 全量注入");
    return input;
  }

  // 拼新 ctx + previousTasks
  let newDistilled = input.ctx.distilledFacts;
  let newMemory = input.ctx.hermesMemoryRecent;
  let newPrevTasks = input.previousTasks;

  if (distilledRes && distilledRes.modelLoaded && distilledRes.ranked.length > 0) {
    const top = pickTopK(distilledSegs, distilledRes.ranked, RELEVANCE_TOP_K_DISTILLED);
    newDistilled = top.join("\n\n");
    const cacheHits = distilledRes.ranked.slice(0, RELEVANCE_TOP_K_DISTILLED).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] distilled: ${distilledSegs.length} → top-${top.length}, ` +
        `${cacheHits} cache 命中, scores: [${distilledRes.ranked.slice(0, 3).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }
  if (memoryRes && memoryRes.modelLoaded && memoryRes.ranked.length > 0) {
    const top = pickTopK(memorySegs, memoryRes.ranked, RELEVANCE_TOP_K_MEMORY);
    newMemory = top.join("\n§\n");
    const cacheHits = memoryRes.ranked.slice(0, RELEVANCE_TOP_K_MEMORY).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] memory: ${memorySegs.length} → top-${top.length}, ` +
        `${cacheHits} cache 命中, scores: [${memoryRes.ranked.slice(0, 5).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }
  if (
    prevRes && prevRes.modelLoaded && prevRes.ranked.length > 0 &&
    input.previousTasks && input.previousTasks.length > RELEVANCE_TOP_K_PREV_TASKS
  ) {
    const topIndices = prevRes.ranked.slice(0, RELEVANCE_TOP_K_PREV_TASKS).map((r) => r.idx);
    newPrevTasks = topIndices
      .map((i) => input.previousTasks?.[i])
      .filter((t): t is NonNullable<typeof t> => t !== undefined);
    const cacheHits = prevRes.ranked.slice(0, RELEVANCE_TOP_K_PREV_TASKS).filter((r) => r.fromCache).length;
    console.log(
      `[advisor relevance] prev_tasks: ${prevTaskTexts.length} → top-${newPrevTasks.length}, ` +
        `${cacheHits} cache 命中, scores: [${prevRes.ranked.slice(0, 5).map((r) => r.score.toFixed(3)).join(",")}]`,
    );
  }

  return {
    ...input,
    ctx: {
      ...input.ctx,
      distilledFacts: newDistilled,
      hermesMemoryRecent: newMemory,
    },
    previousTasks: newPrevTasks,
  };
}

async function raceWithTimeout(
  p: Promise<AdvisorResult | null>,
): Promise<AdvisorFetchResult> {
  // P3.4.3 (6/15 鸿波): timer id 拿出来 race resolve 后 clearTimeout.
  //   老 setTimeout 没 clear — race 已 resolve (LLM 早返 / short-circuit 返 null
  //   见 _fetchBriefingAdvisorImpl:533 "数据全空 不调 LLM") 后, 180s 那个
  //   setTimeout 仍跑回调 console.warn "客户端 180s 超时", 但实际 race 早结束
  //   了, 这条 warning 是误报. 6/15 鸿波早安卡 console 看到这条 warning 误把
  //   "数据全空"诊断方向带偏成"LLM 慢", 排了一通错路.
  //
  //   修法: setTimeout 回调里只 resolve, console.warn 移到 race 外, 看 winner
  //   真等于 ADVISOR_TIMEOUT 才 warn. try/finally clearTimeout 防 race 赢后
  //   timer 残留再 fire (即使 resolve 没用了, console.warn 也别再 spew).
  let timerId: ReturnType<typeof setTimeout> | undefined;
  const timeoutPromise = new Promise<typeof ADVISOR_TIMEOUT>((resolve) => {
    timerId = setTimeout(() => resolve(ADVISOR_TIMEOUT), CLIENT_TIMEOUT_MS);
  });
  try {
    const winner = await Promise.race<AdvisorFetchResult>([p, timeoutPromise]);
    if (winner === ADVISOR_TIMEOUT) {
      console.warn(
        `[advisor] 客户端 ${CLIENT_TIMEOUT_MS / 1000}s 超时, 返 TIMEOUT (fetch 仍在后台跑, ` +
          "完成会写 cache, 下次时段触发能用).",
      );
    }
    return winner;
  } finally {
    if (timerId !== undefined) clearTimeout(timerId);
  }
}

async function _fetchBriefingAdvisorImpl(input: AdvisorInput): Promise<AdvisorResult | null> {
  console.log("[advisor] 调用开始", {
    tier: input.profile.tier,
    centralState: input.profile.centralState,
    confidence: input.profile.confidence,
    emails: input.emails.length,
    events: input.events.length,
    todos: input.todos.length,
    model: input.model,
  });

  // profile 占位 (confidence=0) 跳过 — Phase 7 第 1 步占位时不调 LLM
  if (input.profile.confidence === 0) {
    console.log("[advisor] profile confidence=0 (占位), 不调 LLM");
    return null;
  }

  // 数据全空保护
  if (input.emails.length === 0 && input.events.length === 0 && input.todos.length === 0) {
    console.log("[advisor] 数据全空, 不调 LLM");
    return null;
  }

  // P3.3.9 (6/10): caller 没传 previousTasks 时, 内部从 advisorCacheGet 拉
  //   (含 stale, 让 LLM 复用 uid). 老 cache 没 taskUid 字段就跳过, 不出错.
  // P3.3.12.1 (6/10): summary 只从 cache 读 (不再现场跑 LLM). 拉 summary 是
  //   ensureTaskChatSummariesFresh 的活, 它由 AdvisorView mount 时后台调,
  //   跟 advisor 主 LLM call 解耦 — cache 命中场景也能更新.
  //
  // P3.3.46 BL-ADVISOR-MEMORY-RACE-HARDFIX (6/12 鸿波 "MEMORY 没更新混乱"):
  //   原 P3.3.12.1 设计有 race — AdvisorView effect1 跑 ensureSummary (慢, LLM
  //   5-30s), effect2 跑主 advisor (快). effect2 读 cache 时 effect1 还没写完,
  //   read stale summary → LLM 看到老 MEMORY 推理混乱.
  //   Fix: 这里 await ensureTaskChatSummariesFresh 先把 MEMORY 写新, 再读 cache.
  //   ensureTaskChatSummariesFresh 内部有 in-flight 锁, AdvisorView effect1 跟
  //   这里调的会复用同一 promise, 不双倍烧 token.
  let inputWithPrev = input;
  if (!input.previousTasks) {
    try {
      // P3.3.46: await summary fresh 先 (内部 in-flight 锁防双调)
      await ensureTaskChatSummariesFresh(input.model).catch((e) => {
        console.warn("[advisor] P3.3.46 await ensureSummary 失败 (降级用 stale):", e);
      });
      const { advisorCacheGet } = await import("./advisor_cache");
      const cached = await advisorCacheGet();
      if (cached && cached.result?.mainTasks?.length > 0) {
        const summaries = cached.taskChatSummaries ?? {};
        const enriched = cached.result.mainTasks
          .filter((t) => typeof t.taskUid === "string" && t.taskUid.length > 0)
          .map((t) => ({
            taskUid: t.taskUid,
            title: t.title,
            urgency: t.urgency,
            chatSummary: summaries[t.taskUid]?.summary ?? "",
          }));
        if (enriched.length > 0) {
          inputWithPrev = { ...input, previousTasks: enriched };
          const withSummary = enriched.filter((t) => t.chatSummary).length;
          console.log(
            `[advisor] 注入 ${enriched.length} 条 prev task ` +
              `(${withSummary} 含 chat summary, 全部来自 cache)`,
          );
        }
      }
    } catch (e) {
      console.warn("[advisor] 拉 advisor_cache 失败 (P3.3.9 uid 复用降级):", e);
    }
  }

  // P3.5.4 (6/16 鸿波): BGE-M3 相关性筛选 — 砍 distilled / memory / prev_tasks
  //   按今天输入语义相关性, 不是粗暴 slice. 失败 silent fallback 返原 input.
  //   model 缺 (~/.catfish/models/bge-m3.onnx 没下载) 时也 fallback.
  const filteredInput = await applyRelevanceFilter(inputWithPrev);

  const userPrompt = buildUserPrompt(filteredInput);
  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  console.log("[advisor] 发 fetch:", url, "prompt 长度:", userPrompt.length);

  // P3.4.6 (6/15 鸿波) sanity: hermes MEMORY 近期事项段是否真拼到 prompt 里.
  //   - "✓ 已注入" = Rust briefing_context_fetch 返了 hermes_memory_recent, 内容非空, prompt 拼了 "# 近期事项" 段
  //   - "✗ 未注入" = MEMORY.md 不存在 / 全是空 § 段 / Rust 端没读 / advisor.ts buildUserPrompt 漏拼
  // 完整 prompt dump: 在 DevTools Console 跑 localStorage.setItem("catfish:debug_advisor_prompt", "true") 再刷新.
  console.log(
    "[advisor] P3.4.6 hermes memory 近期事项注入:",
    userPrompt.includes("# 近期事项") ? "✓ 已注入" : "✗ 未注入 (ctx.hermesMemoryRecent 空 / MEMORY.md 不存在 / 全空 § 段)",
  );
  if (typeof localStorage !== "undefined" && localStorage.getItem("catfish:debug_advisor_prompt") === "true") {
    console.log("[advisor] 完整 prompt (P3.4.6 debug 模式, localStorage flag 打开):\n" + userPrompt);
  }

  // 不挂 AbortSignal — Tauri webview 失焦会 suspend, 让 fetch 自己生命周期
  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model: input.model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        // P3.4.C (6/15 鸿波): 3000 → 6000.
        //   真因 (鸿波 6/15 console raw content audit): DeepSeek Flash 仍
        //   "reasoning out loud" — 跑完 tool 后输出 "所有扫描完成。结果汇总: ...
        //   现在输出最终 JSON。{ "tier": "mid", "main_tasks": [{...]" — reasoning
        //   prose ~1500 tokens + JSON ~1500 tokens, max_tokens=3000 边界刚好,
        //   JSON 经常截断 (没闭合 ] }), robustJsonParse 救不了 truncated JSON.
        //   6000 给 reasoning + JSON 都装下. 跟 P3.4.D robustJsonParse 改 brace
        //   balanced match 一起救 truncated 场景.
        max_tokens: 6000,
        temperature: 0.4,
        stream: false,
        // P3.4.10 (6/15 鸿波): OpenAI 协议强制 JSON. P3.4.9 prompt 加强对
        //   DeepSeek Flash 无效 (LLM 仍 "Good — consistent with TODO list.
        //   Let me finalize..." 输出 markdown reasoning). 真因是 reasoning
        //   model 倾向 think out loud, prompt 压不住. response_format 是 API
        //   层面强制.
        //
        //   兼容性: catfish-gateway facts_pipeline.py:165 已有同款用法, 注释
        //   "部分模型支持, 不支持的会忽略" — 加上零风险, 不破坏现有调用.
        //   DeepSeek API / Anthropic Claude / OpenAI gpt-4o-mini 全支持.
        response_format: { type: "json_object" },
      }),
    });

    console.log("[advisor] HTTP status =", resp.status);
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      console.warn("[advisor] LLM 非 2xx:", resp.status, text.slice(0, 200));
      return null;
    }

    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") {
      console.warn("[advisor] LLM 返非字符串 content:", content);
      return null;
    }
    console.log("[advisor] raw content (前 400):", content.slice(0, 400));

    // BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT (6/1 鸿波): gateway 端已加 detect 转 502
    // (双层防御之一). 客户端兜底再判一次, 真 LiteLLM 私有 LLM (catfish-private-main)
    // streaming fail 时返 200 + content="API call failed after 3 retries..." 标准
    // pattern. 即使 gateway 端 detect 漏掉某变体, 这里仍能识别避免"JSON 解析失败"灰区.
    const upstreamErrorPattern = /API call failed|after \d+ retries|during streaming|retries exhausted/i;
    if (content.length < 500 && upstreamErrorPattern.test(content)) {
      console.warn(
        "[advisor] 上游 LLM 错误作 content 返 (HTTP 200 + 错误文本), 真因: 上游 LLM 服务挂. " +
        "原文:", content.slice(0, 200),
      );
      return null;
    }

    // 5/22 cold start 修: 鲁棒 JSON 解析 — LLM 输出常含前后解释文字
    // (e.g. "现在我已经分析完..."), 不只 strip markdown 反引号.
    const parsed = robustJsonParse(content);

    // P3.4.E (6/15 鸿波): robustJsonParse 失败 → 调 Call 2 transformToStructured 100% 转结构化.
    //   真因: P3.4.9/.10/.C 累积修治标 80%, 仍 20% LLM 纯 reasoning 无 JSON / truncated 救不回.
    //   Call 2 single-shot + tool_choice strict 100% 拿到结构化结果 (代价 +1-2s latency).
    //   两层叠加 (Call 1 fast path / Call 2 strict fallback) — 多数 cache hit 走 fast,
    //   少数 reasoning out loud 才触发 Call 2, 平均 latency 影响小.
    let result: AdvisorResult | null;
    if (parsed === null) {
      console.warn(
        "[advisor] robustJsonParse 失败 (LLM 纯 reasoning / truncated), 触发 P3.4.E Call 2 转结构化. 原文前 200:",
        content.slice(0, 200),
      );
      result = await transformToStructured(content, input.model, input.profile.tier);
      if (result === null) {
        console.warn("[advisor] P3.4.E Call 2 也挂, 返 null (UI 显数据诊断卡)");
        return null;
      }
    } else {
      result = parseAdvisorResult(parsed, "strict");
      // P3.4.E: parsed 拿到了但 parseAdvisorResult strict 返 null (e.g. mainTasks 缺失 / tier 非法
      //   / P3.4.E.7 frontline/mid options<2). 走 Call 2 strict schema 救场.
      if (result === null) {
        console.warn(
          "[advisor] parseAdvisorResult strict 失败 (parsed 有但 schema 不匹配 / options<2), 触发 P3.4.E Call 2",
        );
        result = await transformToStructured(content, input.model, input.profile.tier);

        // P3.4.E.7 (6/15 鸿波): 第 3 层 lenient 兜底 — Call 2 也挂时, 用 lenient mode
        //   重新 parse Call 1 原 parsed (LLM 极端不听话场景, schema 都强不动).
        //   至少给员工看 LLM 给的内容, 不让 UI 完全空数据诊断卡.
        //   lenient 接受 options<2, 只 warn 不拒.
        if (result === null) {
          console.warn(
            "[advisor] P3.4.E Call 2 也挂, 尝试 lenient mode 兜底 (接受 options<2 不空 UI)",
          );
          result = parseAdvisorResult(parsed, "lenient");
          if (result === null) {
            console.warn("[advisor] P3.4.E.7 lenient 兜底也挂 (schema 真坏), 返 null UI 显数据诊断卡");
            return null;
          }
        }
      }
    }
    // P3.3.40 BL-ADVISOR-RESOLVED-HARDFILTER (6/12 鸿波): prompt 里加了 §4.2
    // (P3.3.39), 但 LLM 听话率 80-90%, 仍会漏. 这里加 deterministic 客户端
    // 后处理 — 拿 prev task chatSummary + 当前 title, 命中"已结案信号"关键字
    // 强制挪去 handledSilently. 不依赖 LLM, 100% 命中.
    if (result && inputWithPrev.previousTasks && inputWithPrev.previousTasks.length > 0) {
      return filterResolvedTasks(result, inputWithPrev.previousTasks);
    }
    return result;
  } catch (e) {
    console.warn("[advisor] LLM 调用挂:", e);
    return null;
  }
}

// ─── P3.3.40 BL-ADVISOR-RESOLVED-HARDFILTER ───────────────────────────
//
// 客户端 deterministic 后处理 — LLM 漏 §4.2 (BL-ADVISOR-RESOLVED-DROP) 时兜底.
// LLM 不听话, 这里硬把 resolved task 移出 mainTasks → handledSilently.
//
// 触发条件 (任一即可):
//   1. prev task chatSummary 含 RESOLVED_KEYWORDS_RE 任一
//   2. 当前 mainTask.title 含 RESOLVED_TITLE_RE 任一
//
// "已发邮件催了" / "等回复" 不算 resolved — 球还在员工手里, 仍放 main_tasks.

/** chat summary 命中即视 task 已结案 — 员工已说"是误报"/"已签字"/"已发起审批" 等. */
const RESOLVED_SUMMARY_PATTERNS: RegExp[] = [
  // "已确认 ... 是误报 / 不存在 / 没有 / 不是 / 已撤销"
  /已确认.*?(误报|不存在|没有|不是|已撤|无风险|已撤销|已结项)/,
  // 误报 直接命中
  /(是误报|属误报|误报修正|查证不存在|不存在.*待办|待办.*?不存在|不存在.*?预警)/,
  // 显式 resolved 语
  /(已结项|已 ?done|已处理完|已交付|已发出|已签字|已完成).*?(交付|审批|签字|发出|结项)?/,
  // 已发起申请 + 审批中 (球已踢出去) — 中间 30 字内可含业务名 (e.g. "已发起加计扣除申请流程")
  /已(发起|提交|递交)[\s\S]{0,30}?(申请|审批|流程|请示|报批|批复)/,
  // 显式作废
  /(不再有效|已作废|已撤回|确认无风险|已撤销)/,
];

/** title 命中关键字也强 — LLM 把"误报修正"写进 title 仍放 main_tasks, hard drop. */
const RESOLVED_TITLE_PATTERNS: RegExp[] = [
  /误报修正/,
  /(是|属|为)误报/,
  /已结项|已作废|已撤销|已撤回/,
];

function chatSummaryLooksResolved(summary: string): boolean {
  if (!summary || summary.length < 4) return false;
  return RESOLVED_SUMMARY_PATTERNS.some((re) => re.test(summary));
}

function titleLooksResolved(title: string): boolean {
  if (!title) return false;
  return RESOLVED_TITLE_PATTERNS.some((re) => re.test(title));
}

function filterResolvedTasks(
  result: AdvisorResult,
  previousTasks: NonNullable<AdvisorInput["previousTasks"]>,
): AdvisorResult {
  // build uid → summary map
  const summaryByUid = new Map<string, string>();
  for (const pt of previousTasks) {
    if (pt.taskUid && pt.chatSummary) {
      summaryByUid.set(pt.taskUid, pt.chatSummary);
    }
  }

  const keptTasks: MainTask[] = [];
  const droppedTitles: string[] = [];

  for (const mt of result.mainTasks) {
    const prevSummary = summaryByUid.get(mt.taskUid) ?? "";
    const summaryResolved = chatSummaryLooksResolved(prevSummary);
    const titleResolved = titleLooksResolved(mt.title);

    if (summaryResolved || titleResolved) {
      const reason = summaryResolved && titleResolved
        ? "summary+title"
        : summaryResolved
          ? "summary"
          : "title";
      console.log(
        `[advisor BL-ADVISOR-RESOLVED-HARDFILTER] drop ${mt.taskUid} "${mt.title}" ` +
          `(${reason} 命中 resolved 关键字)`,
      );
      droppedTitles.push(mt.title);
      continue;
    }
    keptTasks.push(mt);
  }

  if (droppedTitles.length === 0) {
    return result;
  }

  // 重排 id (LLM 排的 1, 2, 3 留个洞不好看, 重排 1..N)
  const renumberedKept = keptTasks.map((t, i) => ({ ...t, id: i + 1 }));

  // 把 dropped 加进 handledSilently — 让员工在折叠区看得到
  const handledExtra: HandledSilentlyItem[] = droppedTitles.map((title) => ({
    type: "task_resolved",
    count: 1,
    category: `${title} 已结/误报/无风险 (BL-ADVISOR-RESOLVED-HARDFILTER 兜底)`,
  }));

  return {
    ...result,
    mainTasks: renumberedKept,
    handledSilently: [...result.handledSilently, ...handledExtra],
  };
}

// ─── 鲁棒 JSON 解析 (5/22 cold start 修) ──────────────────────────

/** LLM 输出常含前后解释文字, 剥 markdown 反引号 + 找 balanced { ... } JSON 块.
 *  失败返 null, 不抛.
 *
 *  P3.4.C (6/15 鸿波): 改用 brace counting 找第一个 balanced `{...}` 块.
 *  老 indexOf("{") + lastIndexOf("}") 在 LLM 输出 truncated 时挂 — 因为 LLM
 *  reasoning 里也含 `{tool_call}` / `{`uid`}` 等单独 `}`, lastIndexOf 命中那条,
 *  截出来 JSON 不闭合. brace counting 找真 balanced 块, 即使 LLM 后面截断也救
 *  开头那个完整 JSON.
 *
 *  实测鸿波 6/15: LLM 输出 "所有扫描完成。结果汇总: ... 现在输出最终 JSON。
 *  {tier: ..., main_tasks: [...]}" — 前面 reasoning 含 `{...}` (引用 task_uid 等),
 *  lastIndexOf 命中那种, 老算法 parse 挂. brace counting 找第一个真 balanced
 *  跳过 reasoning 引用.
 */
function robustJsonParse(content: string): unknown | null {
  if (typeof content !== "string") return null;
  const s = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/```$/, "")
    .trim();
  // 1. 先全文 parse 试试 (LLM 听话只输出 JSON 时走这条)
  try {
    return JSON.parse(s);
  } catch {
    /* 落空走 brace counting */
  }

  // 2. brace counting — 从第一个 `{` 起, count 字符串字面量内的 brace 不算,
  //    找到 balanced `}` 立即返. 救 LLM truncated 输出.
  const first = s.indexOf("{");
  if (first < 0) return null;
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = first; i < s.length; i++) {
    const c = s[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (c === "\\" && inString) {
      escape = true;
      continue;
    }
    if (c === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        // 找到 balanced — 截出 [first, i+1) parse
        try {
          return JSON.parse(s.slice(first, i + 1));
        } catch {
          // 这个 balanced 块本身不是合法 JSON (e.g. trailing comma) — 再往下找
          // 下一个 balanced 块. 但代价大, 简化: 直接返 null 让调用方降级.
          return null;
        }
      }
    }
  }
  // 跑完没遇到 depth=0 → LLM 输出真截断, JSON 未闭合. 返 null.
  return null;
}

// ─── P3.4.E (6/15 鸿波): Call 2 transformToStructured — strict tool_choice 100% JSON ──
//
// 设计:
//   Call 1 (现 _fetchBriefingAdvisorImpl 的 hermes agent loop): LLM 跑业务 tool
//     (catfish_check_compliance / political_sensitivity_scan 等), 拿 raw final content.
//     大多场景 content 已含合法 JSON, robustJsonParse 直接救场, 不进 Call 2.
//   Call 2 (本函数): 只在 robustJsonParse 失败 (LLM 纯 reasoning 无 JSON / truncated 救不回)
//     才触发. 单 shot 直走 catfish-gateway 8999 + tool_choice 强制 LLM 返结构化 tool_call.
//     prompt 给 LLM Call 1 的 raw final content, 让它 "把这个推理结论转成 advisor JSON".
//
// 为什么不 Call 1 就强 tool_choice:
//   Call 1 走 hermes 8642 agent loop, hermes 不读 client tools / tool_choice (api_server.py:1820).
//   就算 hermes 透传, advisor first turn 必调业务 tool (catfish_check_compliance 等), tool_choice
//   强制 submit_advisor_result 会跳过业务 tool 调用, 失去 agent loop 业务能力.
//   Two-call architecture 保留 agent loop 业务能力 + 加 100% 结构化保证. 代价 +1-2s latency.
//
// 兜底: Call 2 自己挂 → 返 null, caller 走老 robustJsonParse 失败路径 (返 null UI 显示数据诊断卡).
async function transformToStructured(
  rawContent: string,
  model: string,
  tier: "frontline" | "mid" | "senior",
): Promise<AdvisorResult | null> {
  const url = `${config.gatewayUrl}/v1/chat/completions${ADVISOR_DIRECT_QUERY}`;
  const trimmed = rawContent.length > 12000 ? rawContent.slice(0, 12000) + "\n\n[已截 ...]" : rawContent;

  // P3.4.E.7 (6/15 鸿波): senior tier 异常型主菜 0 options 合法, frontline/mid 必须 ≥2.
  //   ADVISOR_JSON_SCHEMA 默认 minItems=2 给 frontline/mid 强约束. senior 时动态 deep-clone
  //   去掉 minItems 让 senior 0 options 合法.
  let schemaForCall: typeof ADVISOR_JSON_SCHEMA | Record<string, unknown> = ADVISOR_JSON_SCHEMA;
  if (tier === "senior") {
    // 浅 deep-clone (JSON 不含函数 / 循环引用, 安全)
    const cloned = JSON.parse(JSON.stringify(ADVISOR_JSON_SCHEMA));
    // 路径: properties.mainTasks.items.properties.options.minItems
    const opt = cloned?.properties?.mainTasks?.items?.properties?.options;
    if (opt && typeof opt === "object") {
      delete opt.minItems;
      opt.description = "senior tier 异常型主菜可 0 个 options, 只列例外 + 风险";
    }
    schemaForCall = cloned;
  }

  const tierDirective =
    tier === "senior"
      ? "员工是 senior tier — 异常例外型主菜可 0 个 options, 只列例外 + 风险."
      : `员工是 ${tier} tier — 每个 mainTask 必须 2-3 个 options (口径/语气选项), 不达标 schema 会拒.`;

  const sys =
    "你是 catfish advisor 结构化转换器. 收到 advisor 的最终推理结论 (可能含 reasoning prose + 部分 JSON 混合), 必须调 submit_advisor_result tool 提交结构化 AdvisorResult. " +
    "不要返 free-text content, 不要解释, 直接调 tool. 字段缺失就用合理默认值 (mainTasks 至少含已识别的, handledSilently 缺就 []). taskUid 如原文有就复用, 没有就生成 6 字符 [a-z0-9]. " +
    tierDirective;
  const userPrompt = `# advisor 原始结论 (转结构化)\n\n${trimmed}`;

  console.log(
    "[advisor] P3.4.E Call 2 transformToStructured 触发, tier:",
    tier,
    "rawContent 长:",
    rawContent.length,
  );

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: sys },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 6000,  // 跟 Call 1 同, transform 不可能比 Call 1 输出大
        temperature: 0.1,  // 转换任务用低温, 不要 LLM 重新发挥
        stream: false,
        tools: [
          {
            type: "function",
            function: {
              name: "submit_advisor_result",
              description: "提交结构化 advisor 结果. 必须调这个 tool, 不允许 free-text content.",
              parameters: schemaForCall,
            },
          },
        ],
        tool_choice: { type: "function", function: { name: "submit_advisor_result" } },
      }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      console.warn("[advisor] P3.4.E Call 2 非 2xx:", resp.status, text.slice(0, 200));
      return null;
    }
    const data = await resp.json();
    const msg = data?.choices?.[0]?.message;

    const toolCalls = msg?.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const args = toolCalls[0]?.function?.arguments;
      if (typeof args === "string" && args.trim()) {
        try {
          const parsed = JSON.parse(args);
          console.log("[advisor] P3.4.E Call 2 tool_call 路径成功, args 长:", args.length);
          return parseAdvisorResult(parsed);
        } catch (e) {
          console.warn("[advisor] P3.4.E Call 2 args JSON.parse 挂:", e);
          const fallback = robustJsonParse(args);
          if (fallback) return parseAdvisorResult(fallback);
        }
      }
    }
    // LLM 仍没遵 tool_choice (DeepSeek 边缘 fallback) — 试 content
    const content = msg?.content;
    if (typeof content === "string") {
      const parsed = robustJsonParse(content);
      if (parsed) {
        console.log("[advisor] P3.4.E Call 2 tool_call 未命中, content fallback 成功");
        return parseAdvisorResult(parsed);
      }
    }
    console.warn("[advisor] P3.4.E Call 2 没拿到结构化结果, msg:", msg);
    return null;
  } catch (e) {
    console.warn("[advisor] P3.4.E Call 2 调用挂:", e);
    return null;
  }
}

// ─── 解析 LLM JSON 输出 ────────────────────────────────────────

/** P3.4.E.7 (6/15 鸿波): mode 参数 — strict frontline/mid options<2 拒, lenient 兜底接受.
 *
 *  设计:
 *   - Call 1 (hermes agent loop) parse: strict — 让 options 不足触发 Call 2 strict schema 重做
 *   - Call 2 (transformToStructured) parse: strict — Call 2 已用 minItems schema 强约束, 应该过
 *   - 第 3 层兜底: Call 1 + Call 2 都挂 (LLM 极端不听话), _fetchBriefingAdvisorImpl 用 lenient
 *     重新 parse Call 1 原 content — 至少给员工看 LLM 给的内容, 不让 UI 完全空数据诊断卡.
 */
function parseAdvisorResult(raw: unknown, mode: "strict" | "lenient" = "strict"): AdvisorResult | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const tier = obj.tier;
  if (tier !== "frontline" && tier !== "mid" && tier !== "senior") {
    console.warn("[advisor] tier 非法:", tier);
    return null;
  }

  const rawTasks = obj.main_tasks ?? obj.mainTasks;
  if (!Array.isArray(rawTasks)) {
    console.warn("[advisor] main_tasks 不是数组:", rawTasks);
    return null;
  }

  const mainTasks: MainTask[] = [];
  for (const t of rawTasks) {
    if (!t || typeof t !== "object") continue;
    const task = parseMainTask(t as Record<string, unknown>);
    if (task) mainTasks.push(task);
  }

  // handledSilently / handled_silently 兼容 snake/camel
  const rawHandled = obj.handled_silently ?? obj.handledSilently ?? [];
  const handledSilently: HandledSilentlyItem[] = [];
  if (Array.isArray(rawHandled)) {
    for (const h of rawHandled) {
      if (!h || typeof h !== "object") continue;
      const it = h as Record<string, unknown>;
      const type = typeof it.type === "string" ? it.type : null;
      const count = typeof it.count === "number" ? it.count : null;
      if (!type || count === null) continue;
      handledSilently.push({
        type,
        count,
        category: typeof it.category === "string" ? it.category : "",
      });
    }
  }

  // P3.4.E.7 (6/15 鸿波): tier-aware options 数量强校验 — 不达标 return null 强逼走 Call 2.
  //   真因: BL-ADVISOR-PROMPT-CONFORMANCE #6b (parseMainTask line 1512) 老只 warn 不修,
  //   鸿波 6/15 撞 'task 巡视巡察整改回头看确认 只 1 个 option'. SYSTEM_PROMPT 强约束
  //   "2-3 个 options" LLM 仍漏 — Call 1 走 hermes 没法用 strict schema.
  //   修法: parseAdvisorResult 加 tier-aware 校验 → return null → _fetchBriefingAdvisorImpl
  //   走 Call 2 fallback (transformToStructured + ADVISOR_JSON_SCHEMA options.minItems=2
  //   strict schema) → DeepSeek beta 拒不符合 schema 的 args, 100% 强制 ≥2 option.
  //   senior tier "异常例外型主菜" 允许 0 options (SYSTEM_PROMPT §3 已说), 不校验.
  if (mode === "strict" && (tier === "frontline" || tier === "mid")) {
    for (const task of mainTasks) {
      if (task.options.length < 2) {
        console.warn(
          `[advisor] P3.4.E.7 strict 校验失败: task '${task.title}' options=${task.options.length} < 2 ` +
            `(tier=${tier}), return null 触发 P3.4.E Call 2 strict schema 重做`,
        );
        return null;
      }
    }
  }
  // lenient mode: 接受 options<2 (Call 1+Call 2 都 strict 挂时兜底), 但仍 warn.
  if (mode === "lenient" && (tier === "frontline" || tier === "mid")) {
    for (const task of mainTasks) {
      if (task.options.length < 2) {
        console.warn(
          `[advisor] P3.4.E.7 lenient 模式接受 options<2: task '${task.title}' options=${task.options.length} ` +
            `(tier=${tier}, LLM 不听话兜底, UI 显示但只 1 个选项)`,
        );
      }
    }
  }

  // P3.5.32 Phase 10 (6/18 鸿波 OpenWiki 借鉴) — 3 维 self-aware reflection parse.
  // 真**optional** — LLM 没返 / 返空 / parse 错 → 0 item, UI 自动不渲染 (length 0 早返).
  // 真**snake_case + camelCase 双兼容** (LLM 易 drift, parseMainTask 同款).
  const subconscious: SubconsciousItem[] = [];
  const subRaw =
    (obj as Record<string, unknown>).subconscious
    ?? (obj as Record<string, unknown>).sub_conscious;
  if (Array.isArray(subRaw)) {
    for (const item of subRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const topic = typeof o.topic === "string" ? o.topic.trim() : "";
      const count =
        typeof o.count === "number" ? o.count : Number(o.count) || 0;
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      const reflectPrompt =
        typeof o.reflectPrompt === "string"
          ? o.reflectPrompt.trim()
          : typeof o.reflect_prompt === "string"
            ? o.reflect_prompt.trim()
            : "";
      if (!topic || count < 1 || !evidence) continue;
      subconscious.push({ topic, count, evidence, reflectPrompt });
    }
  }

  const graveyard: GraveyardItem[] = [];
  const graveRaw = (obj as Record<string, unknown>).graveyard;
  if (Array.isArray(graveRaw)) {
    for (const item of graveRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const name = typeof o.name === "string" ? o.name.trim() : "";
      const lastSeen =
        typeof o.lastSeen === "string"
          ? o.lastSeen.trim()
          : typeof o.last_seen === "string"
            ? o.last_seen.trim()
            : "";
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      if (!name || !lastSeen) continue;
      graveyard.push({ name, lastSeen, evidence });
    }
  }

  const blindSpots: BlindSpotItem[] = [];
  const blindRaw =
    (obj as Record<string, unknown>).blindSpots
    ?? (obj as Record<string, unknown>).blind_spots;
  if (Array.isArray(blindRaw)) {
    for (const item of blindRaw.slice(0, 3)) {
      if (!item || typeof item !== "object") continue;
      const o = item as Record<string, unknown>;
      const topic = typeof o.topic === "string" ? o.topic.trim() : "";
      const signal = typeof o.signal === "string" ? o.signal.trim() : "";
      const evidence = typeof o.evidence === "string" ? o.evidence.trim() : "";
      const reflectPrompt =
        typeof o.reflectPrompt === "string"
          ? o.reflectPrompt.trim()
          : typeof o.reflect_prompt === "string"
            ? o.reflect_prompt.trim()
            : "";
      if (!topic || !signal || !evidence) continue;
      blindSpots.push({ topic, signal, evidence, reflectPrompt });
    }
  }

  // P3.5.32.2 (6/18 鸿波 debug): 显式 log 3 维 parse 结果, 方便鸿波 devtools console
  // 看 LLM 到底返了什么. 鸿波本机 6/18 上午看不到 cards, 真因可能是:
  //   (a) cache 老数据 (computedAt 早于 P3.5.32 ship) → 点"刷新"触发 fresh LLM
  //   (b) LLM Call 1 没 emit 3 字段 (hermes 弱 schema, LLM 自由发挥, 可能漏)
  //   (c) 数据真不够 (7 天 sessions < 5) → LLM 返空 array, cards 0 渲染
  console.log("[advisor] P3.5.32 3 维 parse:", {
    subconscious: subconscious.length,
    graveyard: graveyard.length,
    blindSpots: blindSpots.length,
    rawHasSubconscious:
      "subconscious" in (obj as Record<string, unknown>)
      || "sub_conscious" in (obj as Record<string, unknown>),
    rawHasGraveyard: "graveyard" in (obj as Record<string, unknown>),
    rawHasBlindSpots:
      "blindSpots" in (obj as Record<string, unknown>)
      || "blind_spots" in (obj as Record<string, unknown>),
  });
  return {
    tier,
    mainTasks,
    handledSilently,
    ...(subconscious.length > 0 ? { subconscious } : {}),
    ...(graveyard.length > 0 ? { graveyard } : {}),
    ...(blindSpots.length > 0 ? { blindSpots } : {}),
  };
}

/** P3.3.9 (6/10): 生成 6 字符 [a-z0-9] uid. LLM 没返 task_uid 时 fallback 用. */
function generateTaskUid(): string {
  return Math.random().toString(36).slice(2, 8).padEnd(6, "0");
}

/** P3.3.12.1 (6/10): ensure 每个 mainTask 的 chat summary 是最新的 (jsonl size 没变就跳).
 *
 *  独立于 advisor 主 LLM call — 这样 cache 命中场景 (主 advisor 没跑) 仍能更新
 *  summary, 下次 advisor 真要跑时直接读 cache 即可.
 *
 *  AdvisorView mount 后异步调一次 (cache 命中也调), 不阻塞 UI.
 *  refreshKey 变 (员工点刷新) 也调.
 *
 *  实测烧 token 风险: 每个 jsonl size 变 task 跑一次 LLM. 4 task 全变 ≈ 4 次 LLM call,
 *  跟主 advisor 一次 call 比是几分之一. 失败不阻塞, summary 留旧的就旧的.
 *
 *  in-flight 锁: 跟主 advisor 同款 — React StrictMode dev useEffect 双调会让本函数
 *  瞬间双跑, 两次都读老 cache 各跑 4 次 LLM, 浪费 8 次 token. 锁复用 promise. */
export async function ensureTaskChatSummariesFresh(model: string): Promise<void> {
  if (_summaryEnsureInFlight) {
    console.log("[advisor summary] ensure 已在跑, 复用 in-flight promise");
    return _summaryEnsureInFlight;
  }
  const p = _ensureTaskChatSummariesFreshImpl(model);
  _summaryEnsureInFlight = p;
  try {
    await p;
  } finally {
    if (_summaryEnsureInFlight === p) _summaryEnsureInFlight = null;
  }
}

let _summaryEnsureInFlight: Promise<void> | null = null;

async function _ensureTaskChatSummariesFreshImpl(model: string): Promise<void> {
  try {
    const { advisorCacheGet, advisorCacheSave } = await import("./advisor_cache");
    const { taskChatGet, taskChatSize } = await import("./task_chat");
    const { sessionGetByTaskUid, getSession } = await import("./tauri");
    const { loadSessionMessagesAsChat } = await import("./sessionMessages");
    const cached = await advisorCacheGet();
    if (!cached || !Array.isArray(cached.result?.mainTasks)) {
      console.log("[advisor summary] ensure 跳过 — 没 cache 或没 mainTasks");
      return;
    }
    const mainTasks = cached.result.mainTasks.filter(
      (t: { taskUid?: string }) =>
        typeof t.taskUid === "string" && t.taskUid.length > 0,
    );
    if (mainTasks.length === 0) {
      console.log("[advisor summary] ensure 跳过 — mainTasks 全没 taskUid");
      return;
    }

    const cachedSummaries = (cached.taskChatSummaries ?? {}) as Record<
      string,
      { summary: string; jsonlSize: number; messageCount?: number; computedAt: string }
    >;
    const newSummaries = { ...cachedSummaries };
    let llmCalls = 0;
    let cacheHits = 0;
    let emptyTasks = 0;
    let fromStateDb = 0;
    let fromJsonl = 0;

    await Promise.all(
      mainTasks.map(async (t: { taskUid: string; title: string }) => {
        try {
          // P3.3.19 C Phase 5 (6/11): 优先拉 state.db. fallback 老 jsonl.
          //   1. sessionGetByTaskUid(taskUid) → 有 sessionId 就用 state.db
          //   2. 没有 sessionId (advisor 早过 task chat 但还没 DetailPane mount 触发 sessionCreate)
          //      → 退回 jsonl (Phase 4 migration 不一定已跑完)
          const sid = await sessionGetByTaskUid(t.taskUid).catch(() => null);

          let messages: Array<{ role: string; content: string; ts: string }> = [];
          let currentCount = 0;

          if (sid) {
            // state.db 路径
            try {
              const detail = await getSession(sid);
              currentCount = detail.meta?.messageCount ?? detail.messages.length;
              if (currentCount === 0) {
                emptyTasks++;
                return;
              }
              // 转 ChatMessage 后再降级成 summarizeTaskChat 接受的 shape
              const chatMessages = loadSessionMessagesAsChat(detail);
              messages = chatMessages.map((m) => ({
                role: m.role,
                content: m.content,
                ts: m.ts,
              }));
              fromStateDb++;
            } catch (e) {
              console.warn(`[advisor summary] state.db 拉 ${sid} 失败, fallback jsonl:`, e);
              sid && (await null); // noop, fall through
            }
          }

          if (!sid || messages.length === 0) {
            // jsonl fallback (Phase 4 migration 未跑完 / 没 session 时)
            const size = await taskChatSize(t.taskUid).catch(() => 0);
            if (size === 0) {
              emptyTasks++;
              return;
            }
            const jsonlMsgs = await taskChatGet(t.taskUid).catch(() => []);
            if (jsonlMsgs.length === 0) {
              emptyTasks++;
              return;
            }
            messages = jsonlMsgs.map((m) => ({
              role: m.role,
              content: m.content,
              ts: m.ts,
            }));
            currentCount = size; // 老 hash 用 size
            fromJsonl++;
          }

          // cache 失效判断: 优先 messageCount 对比 (state.db), 没 messageCount 则 jsonlSize
          const hit = cachedSummaries[t.taskUid];
          const cacheValid = hit && hit.summary && (
            (typeof hit.messageCount === "number" && hit.messageCount === currentCount) ||
            (typeof hit.messageCount !== "number" && hit.jsonlSize === currentCount)
          );
          if (cacheValid) {
            cacheHits++;
            return;
          }

          llmCalls++;
          const summary = await summarizeTaskChat(t.title, t.taskUid, messages, model);
          if (summary) {
            newSummaries[t.taskUid] = {
              summary,
              jsonlSize: currentCount,  // backward compat 留同字段, value 取 messageCount/size
              messageCount: currentCount,
              computedAt: new Date().toISOString(),
            };
          }
        } catch (e) {
          console.warn(`[advisor summary] ensure ${t.taskUid} 失败:`, e);
        }
      }),
    );

    console.log(
      `[advisor summary] ensure 完成 — ${mainTasks.length} task ` +
        `(${emptyTasks} 没聊过, ${cacheHits} cache 命中, ${llmCalls} 调 LLM; ` +
        `源: ${fromStateDb} state.db / ${fromJsonl} jsonl)`,
    );

    if (llmCalls > 0) {
      await advisorCacheSave({
        ...cached,
        taskChatSummaries: newSummaries,
      });
      console.log("[advisor summary] 已写 cache (含新 summary)");
    }
  } catch (e) {
    console.warn("[advisor summary] ensure 顶层异常:", e);
  }
}

/** P3.3.12 (6/10): 调 LLM 出 task chat summary. 100-150 字, 客观描述员工
 *  已经决定/已经做/已经说要做什么, 不含 AI 建议内容.
 *
 *  截最后 30 条 + 每条限 300 字 → 控制 prompt 长度防 OOM.
 *  max_tokens 300 (~150 中文字), temperature 0.3 (准确为主).
 *  失败 (网络/LLM 非 2xx/parse 错) 返 "" (不阻塞 advisor 主流程). */
async function summarizeTaskChat(
  taskTitle: string,
  taskUid: string,
  messages: Array<{ role: string; content: string; ts: string }>,
  model: string,
): Promise<string> {
  if (messages.length === 0) return "";

  const lastN = messages.slice(-30);
  const dump = lastN
    .map((m) => {
      const role = m.role === "user" ? "员工" : m.role === "assistant" ? "AI" : m.role;
      const text = m.content.length > 300 ? m.content.slice(0, 300) + "…" : m.content;
      return `${role}: ${text}`;
    })
    .join("\n");

  const prompt = `以下是员工跟 catfish AI 在某条待办 "${taskTitle}" 上的最近对话.

用 100-150 字总结员工**已经决定/已经做/已经说要做什么**, 客观描述员工当前进度/状态/卡点/决策.
**不要**包括 AI 的建议或猜测, 只总结员工本人说过/做过/确认过的事.
输出纯文本一段, 不带 markdown.

${dump}

总结:`;

  try {
    const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: prompt }],
        max_tokens: 300,
        temperature: 0.3,
        stream: false,
      }),
    });
    if (!resp.ok) {
      console.warn(`[advisor summary] ${taskUid} 非 2xx:`, resp.status);
      return "";
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string") return "";
    // 截 400 字防 LLM 不守 100-150 字约束
    return content.trim().slice(0, 400);
  } catch (e) {
    console.warn(`[advisor summary] ${taskUid} 异常:`, e);
    return "";
  }
}

function parseMainTask(t: Record<string, unknown>): MainTask | null {
  const title = t.title;
  if (typeof title !== "string" || !title.trim()) return null;
  const id = typeof t.id === "number" ? t.id : 0;

  // P3.3.9 (6/10): task_uid 读, 兼容 task_uid / taskUid; 没返 fallback 生成新.
  const rawUid =
    typeof t.task_uid === "string" && t.task_uid.length >= 4
      ? t.task_uid
      : typeof t.taskUid === "string" && t.taskUid.length >= 4
      ? t.taskUid
      : null;
  const taskUid = rawUid
    ? rawUid.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 12) || generateTaskUid()
    : generateTaskUid();
  if (!rawUid) {
    console.warn(`[advisor] task '${title}' LLM 没返 task_uid, fallback 新生成 '${taskUid}' (跨 refresh chat 历史会丢)`);
  }

  const urgencyRaw = typeof t.urgency === "string" ? t.urgency.toLowerCase() : "medium";
  const urgency: "high" | "medium" | "low" =
    urgencyRaw === "high" ? "high" : urgencyRaw === "low" ? "low" : "medium";

  const reason = typeof t.reason === "string" ? t.reason : undefined;

  const options: AdvisorOption[] = [];
  if (Array.isArray(t.options)) {
    for (const o of t.options) {
      if (!o || typeof o !== "object") continue;
      const op = o as Record<string, unknown>;
      const label = typeof op.label === "string" ? op.label : null;
      const rawTone = typeof op.tone === "string" ? op.tone.toLowerCase() : null;
      const summary = typeof op.summary === "string" ? op.summary : "";
      if (!label || !rawTone) continue;
      // BL-ADVISOR-PROMPT-CONFORMANCE #6a (6/1): tone enum 归一化.
      // 5/22 实测 LLM 偶尔出 "prepare" / "consider" 等非 enum tone. UI 不依赖
      // tone 选颜色 (只当 string 传 decisionRecord), 但 decisions.jsonl 留档
      // 时统计困难 — 改归一到 'balanced' + warn. SYSTEM_PROMPT 已说 enum,
      // 这里是兜底 fallback (不影响主线).
      const tone: AdvisorTone = (VALID_TONES as readonly string[]).includes(rawTone)
        ? (rawTone as AdvisorTone)
        : "balanced";
      if (tone !== rawTone) {
        console.warn(
          `[advisor] LLM 返非 enum tone='${op.tone}' (task: ${title}), 归一到 'balanced'`,
        );
      }
      // 5/22 鸿波 BL-DRAFTPATH-WHITELIST: 防 LLM 幻觉路径.
      // LLM 偶尔不听 SYSTEM_PROMPT, 编一个 /Users/.../Documents/xxx.md 进来.
      // 客户端二次过滤: 只接 ~/.catfish/outputs/ 下的 path, 其它当 null.
      // 这样 UI 显"无草稿, 自己写" 而不是点了报"草稿打开失败".
      const rawDraft =
        typeof op.draftPath === "string"
          ? op.draftPath
          : typeof op.draft_path === "string"
          ? op.draft_path
          : undefined;
      const safeDraft =
        rawDraft && rawDraft.includes("/.catfish/outputs/") ? rawDraft : undefined;
      if (rawDraft && !safeDraft) {
        console.warn(
          "[advisor] LLM 幻觉 draftPath, 不在 ~/.catfish/outputs/ 下, 已清:",
          rawDraft,
        );
      }
      options.push({
        label,
        tone,
        summary,
        aiLean: op.aiLean === true || op.ai_lean === true,
        draftPath: safeDraft,
      });
    }
  }

  // BL-ADVISOR-PROMPT-CONFORMANCE #6b (6/1): options 数量校验 (warn only, 不
  // 拒). senior tier 允许 0 options ("异常例外型" 主菜只列风险, SYSTEM_PROMPT
  // 已说). 但 frontline/mid tier 出 1 options 是 LLM 没按 "2-3 个建议选项"
  // 出, 失"建议选项"价值. warn 不修, 真改靠 SYSTEM_PROMPT 强约束 (#6c).
  if (options.length === 1) {
    console.warn(
      `[advisor] task '${title}' 只 1 个 option, LLM 没按 SYSTEM_PROMPT 出 2-3 个`,
    );
  }

  const complianceFlags: ComplianceFlag[] = [];
  const rawCompFlags = t.complianceFlags ?? t.compliance_flags;
  if (Array.isArray(rawCompFlags)) {
    for (const f of rawCompFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      complianceFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestion: typeof fl.suggestion === "string" ? fl.suggestion : undefined,
      });
    }
  }

  const politicalFlags: PoliticalFlag[] = [];
  const rawPoliFlags = t.politicalFlags ?? t.political_flags;
  if (Array.isArray(rawPoliFlags)) {
    for (const f of rawPoliFlags) {
      if (!f || typeof f !== "object") continue;
      const fl = f as Record<string, unknown>;
      const type = typeof fl.type === "string" ? fl.type : null;
      const severityRaw = typeof fl.severity === "string" ? fl.severity : "medium";
      const reason_ = typeof fl.reason === "string" ? fl.reason : "";
      if (!type) continue;
      const suggestedPhrasings = Array.isArray(
        fl.suggestedPhrasings ?? fl.suggested_phrasings,
      )
        ? ((fl.suggestedPhrasings ?? fl.suggested_phrasings) as unknown[]).filter(
            (s): s is string => typeof s === "string",
          )
        : undefined;
      politicalFlags.push({
        type,
        severity: severityRaw === "high" ? "high" : severityRaw === "low" ? "low" : "medium",
        person: typeof fl.person === "string" ? fl.person : undefined,
        reason: reason_,
        matchedKeyword:
          typeof fl.matchedKeyword === "string"
            ? fl.matchedKeyword
            : typeof fl.matched_keyword === "string"
            ? fl.matched_keyword
            : undefined,
        suggestedPhrasings,
        advisoryOnly: fl.advisoryOnly === true || fl.advisory_only === true,
      });
    }
  }

  const contextRefs: string[] = [];
  const rawRefs = t.contextRefs ?? t.context_refs;
  if (Array.isArray(rawRefs)) {
    for (const r of rawRefs) {
      if (typeof r === "string") contextRefs.push(r);
    }
  }

  return {
    id,
    taskUid,
    title,
    urgency,
    reason,
    options,
    complianceFlags,
    politicalFlags,
    contextRefs,
  };
}


// ─── 测试 export — P3.3.40 ────────────────────────────────────────
//
// 单测拿这套出来跑, 生产代码不用.
export const __test__ = {
  filterResolvedTasks,
  chatSummaryLooksResolved,
  titleLooksResolved,
  RESOLVED_SUMMARY_PATTERNS,
  RESOLVED_TITLE_PATTERNS,
};
