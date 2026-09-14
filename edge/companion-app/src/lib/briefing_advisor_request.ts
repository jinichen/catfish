/** Advisor 两阶段 LLM 请求合同。
 *
 * 这里存的是调用语义，不是模型属性：同一个模型在 agent 分析和结构化转换中
 * 使用不同 temperature。模型 context/max-output/供应商强制参数仍由 Gateway
 * 的中央模型配置约束。
 */
import type { AdvisorInput } from "./briefing_advisor_common";
import { ADVISOR_JSON_SCHEMA } from "./briefing_advisor_prompts";
import { withFactEvidence } from "./factEvidence";

const ADVISOR_MAX_TOKENS = 6000;
const AGENT_TEMPERATURE = 0.4;
const TRANSFORM_TEMPERATURE = 0.1;
const TRANSFORM_TOOL_NAME = "submit_advisor_result";

const TRIAGE_PROMPT = `你是早安任务整理助手。本轮只核对当前来源、去重并按紧急程度排序，不执行任何行动。
只输出 JSON：{ "tier":"frontline|mid|senior", "mainTasks":[], "handledSilently":[] }。
tier 使用员工档案值。frontline 最多8项，mid最多4项，senior最多2项；没有有据任务就返回空数组。
每项字段：id（从1开始）、taskUid（复用已有，否则6位小写字母数字）、title、urgency（high/medium/low）、reason、contextRefs（具体来源ID与时间）、options、complianceFlags:[]、politicalFlags:[]。
每项 reason 简述责任、时间和来源，不展开推理。frontline/mid 给2个简短建议选项，senior可为空。
选项格式：{ "label":"A或B", "tone":"balanced或hold", "summary":"一句建议", "aiLean":true或false }。
不生成草稿、draftPath，不运行或声称完成合规/敏感扫描，不声称归档、发送或执行；handledSilently必须为空。
草稿与扫描由用户进入具体任务后按需请求。已完成/忽略事项不得重新变成待办，手动状态优先；历史仅帮助复用ID，不得新增无当前来源任务。`;

/** Initial triage deliberately bypasses the Hermes tool loop. */
export function advisorTriageUrl(gatewayUrl: string): string {
  return `${gatewayUrl.replace(/\/$/, "")}/v1/chat/completions?catfish_source=companion-advisor&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1`;
}

export function buildAdvisorAgentRequest(model: string, userPrompt: string) {
  return {
    model,
    messages: [
      { role: "system", content: withFactEvidence(TRIAGE_PROMPT) },
      { role: "user", content: userPrompt },
    ],
    max_tokens: ADVISOR_MAX_TOKENS,
    temperature: AGENT_TEMPERATURE,
    stream: false,
    response_format: { type: "json_object" },
  };
}

type AdvisorTier = AdvisorInput["profile"]["tier"];

function schemaForTier(tier: AdvisorTier): Record<string, any> {
  const schema = JSON.parse(JSON.stringify(ADVISOR_JSON_SCHEMA)) as Record<string, any>;
  if (tier !== "senior") return schema;

  const options = schema?.properties?.mainTasks?.items?.properties?.options;
  if (options && typeof options === "object") {
    delete options.minItems;
    options.description = "senior tier 异常型主菜可 0 个 options，只列例外和风险";
  }
  return schema;
}

function transformSystemPrompt(tier: AdvisorTier): string {
  const tierDirective = tier === "senior"
    ? "员工是 senior tier — 异常例外型主菜可 0 个 options，只列例外和风险。"
    : `员工是 ${tier} tier — 每个 mainTask 必须有 2-3 个 options，不达标会被拒绝。`;
  return (
    "你是 catfish advisor 结构化转换器。收到 advisor 的最终推理结论后，" +
    `必须调用 ${TRANSFORM_TOOL_NAME} 提交结构化 AdvisorResult。` +
    "不要返回自由文本，不得新增原文没有的任务、项目、人名、截止时间或理由。" +
    "原文没有具体业务事项时 mainTasks 必须为空，禁止生成等待用户输入类占位任务。" +
    "handledSilently 缺失时使用空数组；taskUid 有则复用，没有则生成 6 字符 [a-z0-9]。" +
    tierDirective
  );
}

export function buildAdvisorTransformRequest(args: {
  model: string;
  rawContent: string;
  tier: AdvisorTier;
}) {
  const trimmed = args.rawContent.length > 12000
    ? `${args.rawContent.slice(0, 12000)}\n\n[已截断]`
    : args.rawContent;
  const schema = schemaForTier(args.tier);

  return {
    model: args.model,
    messages: [
      { role: "system", content: withFactEvidence(transformSystemPrompt(args.tier)) },
      { role: "user", content: `# advisor 原始结论（转结构化）\n\n${trimmed}` },
    ],
    max_tokens: ADVISOR_MAX_TOKENS,
    temperature: TRANSFORM_TEMPERATURE,
    stream: false,
    tools: [
      {
        type: "function",
        function: {
          name: TRANSFORM_TOOL_NAME,
          description: "提交结构化 advisor 结果。必须调用，不允许自由文本。",
          parameters: schema,
        },
      },
    ],
    tool_choice: {
      type: "function",
      function: { name: TRANSFORM_TOOL_NAME },
    },
  };
}
