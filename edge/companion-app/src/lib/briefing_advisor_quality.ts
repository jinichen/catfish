/** briefing advisor 输出质量门。
 *
 * 8/24 实盘 Qwen 在 Call 1 偏航调用 browser screenshot 后，只返回
 * “系统提示已接收。你说吧，需要我做什么？”。旧链路把这句话交给 Call 2，
 * Call 2 又按“字段缺失用合理默认值”补成了“等待用户输入具体任务”，最终污染缓存。
 *
 * 这里不判断文风，只判断两件可验证的事：
 *   1. 不是系统初始化/等待输入类占位回复；
 *   2. 结论和每条主菜都能在本轮真实输入里找到业务词锚点。
 *
 * previousTasks 故意不进证据集：旧缓存可能已经被占位任务污染，不能让它反过来
 * 给下一轮占位输出“自证”。
 */
import type { AdvisorInput, AdvisorResult, MainTask } from "./briefing_advisor_common";


const GENERIC_OUTPUT_PATTERNS: readonly RegExp[] = [
  /系统(?:提示|消息)(?:已)?(?:接收|收到)/i,
  /系统初始化(?:已)?完成/i,
  /你说吧/i,
  /需要我做什么/i,
  /无具体(?:任务|问题|内容)/i,
  /等待用户(?:输入|提供|提出)(?:具体)?(?:任务|问题|指令|结论)/i,
  /请(?:输入|提供|提出)(?:一个|你的)?具体(?:任务|问题|指令)/i,
  /advisor\s*原始结论/i,
];

// 二字词很容易偶然碰撞，常见流程词不作为业务证据。
const GENERIC_EVIDENCE_TOKENS = new Set([
  "任务", "问题", "用户", "输入", "系统", "具体", "需要", "当前", "今天",
  "本周", "工作", "处理", "推进", "确认", "反馈", "等待", "事项", "项目",
  "邮件", "日程", "计划", "待办", "建议", "情况", "内容", "相关", "进行",
  "完成", "状态", "业务", "信息", "结果", "已经", "没有", "可以", "一个",
]);

function normalize(text: string): string {
  return text.toLowerCase().normalize("NFKC");
}

function hasGenericPlaceholder(text: string): boolean {
  const normalized = normalize(text).trim();
  if (!normalized) return true;
  return GENERIC_OUTPUT_PATTERNS.some((pattern) => pattern.test(normalized));
}

function addChineseTokens(chunk: string, output: Set<string>): void {
  if (chunk.length === 2 && !GENERIC_EVIDENCE_TOKENS.has(chunk)) {
    output.add(chunk);
    return;
  }
  const width = 3;
  for (let i = 0; i <= chunk.length - width; i += 1) {
    const token = chunk.slice(i, i + width);
    if (!GENERIC_EVIDENCE_TOKENS.has(token)) output.add(token);
  }
}

function evidenceTokens(text: string): Set<string> {
  const normalized = normalize(text);
  const tokens = new Set<string>();

  for (const match of normalized.matchAll(/[a-z0-9][a-z0-9._+-]{2,}/g)) {
    tokens.add(match[0]);
  }
  for (const match of normalized.matchAll(/[\p{Script=Han}]{2,}/gu)) {
    addChineseTokens(match[0], tokens);
  }
  return tokens;
}

function sourceCorpus(input: AdvisorInput): string {
  return JSON.stringify({
    keyPeople: input.profile.keyPeople,
    keyProjects: input.profile.keyProjects,
    emails: input.emails,
    events: input.events,
    todos: input.todos,
    context: input.ctx,
    wikiRelevant: input.wikiRelevant ?? "",
  });
}

function taskCorpus(task: MainTask): string {
  return JSON.stringify({
    title: task.title,
    reason: task.reason ?? "",
    options: task.options.map((option) => `${option.label} ${option.summary}`),
    contextRefs: task.contextRefs,
  });
}

function taskGroundingCorpus(task: MainTask): string {
  // reason/options 容易顺手带入“招投标/项目/反馈”等同领域通用词，不能据此证明
  // 任务本身来自输入。标题和 contextRefs 才是任务身份锚点。
  return JSON.stringify({ title: task.title, contextRefs: task.contextRefs });
}

function hasEvidenceOverlap(candidate: string, input: AdvisorInput): boolean {
  const source = evidenceTokens(sourceCorpus(input));
  if (source.size === 0) return false;
  for (const token of evidenceTokens(candidate)) {
    if (source.has(token)) return true;
  }
  return false;
}

/** Call 1 无 JSON 时，是否值得进入 Call 2 结构化转换。 */
export function isAdvisorTransformSourceUsable(
  rawContent: string,
  input: AdvisorInput,
): boolean {
  if (hasGenericPlaceholder(rawContent)) return false;
  return hasEvidenceOverlap(rawContent, input);
}

/** 最终结果写缓存/显示前的语义校验。 */
export function isAdvisorResultGrounded(
  result: AdvisorResult,
  input: AdvisorInput,
): boolean {
  if (!isAdvisorResultCacheSafe(result)) return false;

  // 有明确 TODO 却一条主菜都没有，不是有效 advisor 结论。
  if (input.todos.length > 0 && result.mainTasks.length === 0) return false;

  for (const task of result.mainTasks) {
    if (!hasEvidenceOverlap(taskGroundingCorpus(task), input)) return false;
  }
  return true;
}

/** 删除无输入依据的单条任务；全部无效时返回 null，避免一条幻觉拖垮有效任务。 */
export function filterAdvisorResultByEvidence(
  result: AdvisorResult,
  input: AdvisorInput,
): AdvisorResult | null {
  const mainTasks = result.mainTasks
    .filter((task) => {
      if (hasGenericPlaceholder(taskCorpus(task))) return false;
      return hasEvidenceOverlap(taskGroundingCorpus(task), input);
    })
    .map((task, index) => ({ ...task, id: index + 1 }));

  if (input.todos.length > 0 && mainTasks.length === 0) return null;
  return mainTasks.length === result.mainTasks.length
    ? result
    : { ...result, mainTasks };
}

/** 旧缓存没有当轮输入可比对，但至少不能包含已知占位任务。 */
export function isAdvisorResultCacheSafe(result: AdvisorResult): boolean {
  return result.mainTasks.every((task) => !hasGenericPlaceholder(taskCorpus(task)));
}

export const __test__ = {
  evidenceTokens,
  hasGenericPlaceholder,
};
