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
    emails: input.emails,
    events: input.events,
    todos: input.todos,
    // distilled_facts / hermesMemoryRecent 是历史上下文，不能单独把旧事项
    // 重新升级成当前待办。只有用户明确维护的当前计划与本轮 wiki 结果进入
    // grounding；wikiRelevant 本身由当前邮件/日历/Reminders 查询得到。
    activeContext: {
      workplan: input.ctx.workplan,
      projects: input.ctx.projects,
    },
    wikiRelevant: input.wikiRelevant ?? "",
  });
}

function activeSourceCorpus(input: AdvisorInput): string {
  return JSON.stringify({
    emails: input.emails,
    events: input.events,
    todos: input.todos,
    workplan: input.ctx.workplan,
    projects: input.ctx.projects,
  });
}

function inactiveProfilePeople(input: AdvisorInput): string[] {
  const activeSource = normalize(activeSourceCorpus(input));
  return input.profile.keyPeople
    .map((person) => normalize(person.name).trim())
    .filter((name) => name.length >= 2 && !activeSource.includes(name));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function taskMentionsInactiveProfilePerson(task: MainTask, input: AdvisorInput): boolean {
  const content = normalize(JSON.stringify({
    title: task.title,
    reason: task.reason ?? "",
    options: task.options,
    contextRefs: task.contextRefs,
  }));
  return inactiveProfilePeople(input).some((name) => content.includes(name));
}

function replaceInactiveProfilePeople(text: string, input: AdvisorInput): string {
  return inactiveProfilePeople(input)
    .sort((a, b) => b.length - a.length)
    .reduce(
      (current, name) => current.replace(new RegExp(escapeRegExp(name), "gi"), "相关负责人"),
      text,
    );
}

function sanitizeTaskPeople(task: MainTask, input: AdvisorInput): MainTask {
  return {
    ...task,
    reason: task.reason ? replaceInactiveProfilePeople(task.reason, input) : task.reason,
    options: task.options.map((option) => ({
      ...option,
      summary: replaceInactiveProfilePeople(option.summary, input),
    })),
    contextRefs: task.contextRefs.map((ref) => replaceInactiveProfilePeople(ref, input)),
  };
}

function taskCorpus(task: MainTask): string {
  return JSON.stringify({
    title: task.title,
    reason: task.reason ?? "",
    options: task.options.map((option) => `${option.label} ${option.summary}`),
    contextRefs: task.contextRefs,
  });
}

function hasEvidenceOverlap(candidate: string, input: AdvisorInput): boolean {
  const source = evidenceTokens(sourceCorpus(input));
  if (source.size === 0) return false;
  for (const token of evidenceTokens(candidate)) {
    if (source.has(token)) return true;
  }
  return false;
}

/**
 * 缓存中的旧任务只有在当前数据源仍能证明它存在时，才允许用于复用 taskUid。
 * 不能使用 distilled_facts / MEMORY，因为它们可能保留几个月前已结束的事项。
 */
export function isAdvisorTaskBackedByCurrentInput(
  task: MainTask,
  input: AdvisorInput,
): boolean {
  const activeSource = activeSourceCorpus(input);
  const title = normalize(task.title).trim();
  if (!title) return false;
  const source = normalize(activeSource);
  if (title.length >= 4 && source.includes(title)) return true;

  const titleTokens = evidenceTokens(`${task.title} ${task.contextRefs.join(" ")}`);
  const sourceTokens = evidenceTokens(activeSource);
  let overlap = 0;
  for (const token of titleTokens) {
    if (sourceTokens.has(token)) overlap += 1;
  }
  // 一个业务 token 可以是偶然碰撞；两个以上才足以支持跨 refresh 复用。
  return overlap >= 2;
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

  // 早安只负责当前自然周；历史洞察不能再从 distilled_facts / MEMORY
  // 反向生成任务。旧缓存含这些字段时直接失效，避免历史内容继续展示。
  if (
    (result.subconscious?.length ?? 0) > 0
    || (result.graveyard?.length ?? 0) > 0
    || (result.blindSpots?.length ?? 0) > 0
  ) return false;

  // 有明确 TODO 却一条主菜都没有，不是有效 advisor 结论。
  if (input.todos.length > 0 && result.mainTasks.length === 0) return false;

  for (const task of result.mainTasks) {
    if (
      !isAdvisorTaskBackedByCurrentInput(task, input)
      || taskMentionsInactiveProfilePerson(task, input)
    ) return false;
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
      return isAdvisorTaskBackedByCurrentInput(task, input);
    })
    .map((task) => sanitizeTaskPeople(task, input))
    .map((task, index) => ({ ...task, id: index + 1 }));

  if (
    (input.todos.length > 0 || result.mainTasks.length > 0)
    && mainTasks.length === 0
  ) return null;
  return {
    ...result,
    mainTasks,
    subconscious: [],
    graveyard: [],
    blindSpots: [],
  };
}

/** 旧缓存没有当轮输入可比对，但至少不能包含已知占位任务。 */
export function isAdvisorResultCacheSafe(result: AdvisorResult): boolean {
  return result.mainTasks.every((task) => !hasGenericPlaceholder(taskCorpus(task)));
}

export const __test__ = {
  evidenceTokens,
  hasGenericPlaceholder,
};
