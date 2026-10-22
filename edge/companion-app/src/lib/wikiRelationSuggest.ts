/**
 * 「关联」关系的类型建议 (10/23 P2)。
 *
 * 知识库 645 条关系里 442 条只写了「关联」—— 说明有关系, 不说明是什么关系: 关系图上
 * 一片灰线, 主聊天拿到的也只是「相关」。一条条人工判太累, 所以让小鲶先给建议,
 * **员工在关系整理工作台里逐条或一键采纳才写入** (工作台「不自动猜测、人确认」的规矩不变)。
 *
 * 读法跟词表一致: 「本条目 rel 对方」(edge/contracts/wiki_relation_vocab.json)。
 * 只有反过来说才成立、词表里又没有反义词时 (如「中电福富 持有 本条目」), 保持「关联」,
 * 原因里写清楚 —— 那条关系应该记在对方条目上, 由员工决定。
 *
 * 走跟 wikiLinkSuggest 同一条路: 强制 tool_choice 的结构化输出 (网关对这类请求自动关深度思考)。
 */
import { config } from "./env";
import { fetchWithAuth } from "./me";
import type { WikiFileInfo } from "./tauri";
import { RELATION_FALLBACK, RELATION_VOCAB } from "../tabs/Wiki/wikiRelationshipTasks";

export interface RelationSuggestion {
  /** 对方条目名 (就是 frontmatter related[].name) */
  name: string;
  /** 词表里的一个; RELATION_FALLBACK = 建议保持「关联」 */
  rel: string;
  reason: string;
}

export interface RelationSuggestResult {
  ok: boolean;
  suggestions: RelationSuggestion[];
  error?: string;
}

const QUERY =
  "?catfish_source=companion-wiki-relation-suggest&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

const RELATION_MEANINGS: Record<string, string> = {
  隶属: "本条目属于对方 (人→部门, 部门→公司, 子项目→项目)",
  包含: "本条目包含对方 (公司→部门, 体系→规则)",
  负责: "本条目负责对方 (人→项目/系统/证书)",
  参与: "本条目参与对方 (人/部门→项目/评估)",
  协作: "本条目与对方协作 (部门↔部门, 公司↔公司)",
  依据: "本条目以对方为依据 (流程→规则/标准, 决策→原则)",
  遵循: "本条目遵循对方 (项目→流程/规范)",
  使用: "本条目使用对方 (项目→系统/工具/数据)",
  持有: "本条目持有对方 (公司→证书/资质)",
  认证: "本条目被对方认证/评估 (公司→评估机构/标准)",
  对标: "本条目与对方对比/对标",
  配套: "本条目与对方配套使用",
  替代: "本条目替代对方 (新版→旧版)",
  前置: "本条目是对方的前置条件 (资质→投标)",
  同类: "本条目与对方同类 (同期项目, 同级证书)",
};

const SYSTEM_PROMPT = `你给个人知识库里只写了「关联」的关系判断具体类型。

读法固定: 「本条目 <关系> 对方」。每种关系的意思:
${RELATION_VOCAB.map((r) => `- ${r}: ${RELATION_MEANINGS[r] ?? ""}`).join("\n")}

规则:
1. 只能从上面的词里选; 拿不准就返回「${RELATION_FALLBACK}」, 不硬凑。
2. 方向要对: 「本条目 <关系> 对方」读出来必须成立。只有反过来才成立时 (比如对方持有本条目),
   用反义词 (隶属↔包含); 没有反义词就返回「${RELATION_FALLBACK}」, 在 reason 里写「应在对方条目记: 对方 X 本条目」。
3. reason 一句话, 说依据 (类型 / 正文里哪句话), 给员工审阅用。
4. 每个给出的对方名字都要返回一条, name 原样照抄。

必须调 suggest_relation_types 返回。`;

const TOOL_SCHEMA = {
  type: "object",
  properties: {
    suggestions: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string", description: "对方条目名, 原样照抄" },
          rel: { type: "string", description: "关系词, 只能从词表里选" },
          reason: { type: "string", description: "一句话依据" },
        },
        required: ["name", "rel", "reason"],
      },
    },
  },
  required: ["suggestions"],
};

function describe(file: WikiFileInfo | undefined, name: string): string {
  if (!file) return `- ${name} (知识库里没找到这个条目)`;
  const kind = file.kind === "entity" ? "实体" : file.kind === "concept" ? "概念" : "记录";
  const tags = file.tags.length > 0 ? ` 标签: ${file.tags.slice(0, 6).join("、")}` : "";
  return `- ${name} (${kind}${file.subtype ? `·${file.subtype}` : ""})${tags}`;
}

export function buildUserPrompt(entry: WikiFileInfo, body: string, names: string[], files: WikiFileInfo[]): string {
  const byName = (name: string) =>
    files.find((f) => f.title === name || f.aliases.includes(name));
  return [
    `# 本条目: ${entry.title} (${entry.kind === "entity" ? "实体" : "概念"}${entry.subtype ? `·${entry.subtype}` : ""})`,
    "",
    body.slice(0, 3000),
    "",
    "# 只写了「关联」的对方",
    ...names.map((n) => describe(byName(n), n)),
  ].join("\n");
}

/** 模型输出 → 只留词表里的类型、只留问到的名字; 每个名字最多一条。 */
export function sanitizeSuggestions(raw: unknown, names: string[]): RelationSuggestion[] {
  const list = (raw as { suggestions?: unknown })?.suggestions;
  if (!Array.isArray(list)) return [];
  const allowed = new Set<string>([...RELATION_VOCAB, RELATION_FALLBACK]);
  const wanted = new Set(names);
  const out = new Map<string, RelationSuggestion>();
  for (const item of list) {
    const s = item as Partial<RelationSuggestion>;
    if (typeof s?.name !== "string" || typeof s.rel !== "string") continue;
    const name = s.name.trim();
    const rel = s.rel.trim();
    if (!wanted.has(name) || !allowed.has(rel) || out.has(name)) continue;
    out.set(name, { name, rel, reason: typeof s.reason === "string" ? s.reason.trim().slice(0, 120) : "" });
  }
  return [...out.values()];
}

export async function suggestRelationTypes(
  entry: WikiFileInfo,
  body: string,
  names: string[],
  files: WikiFileInfo[],
  model: string,
): Promise<RelationSuggestResult> {
  if (names.length === 0) return { ok: true, suggestions: [] };
  try {
    const resp = await fetchWithAuth(`${config.gatewayUrl}/v1/chat/completions${QUERY}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: buildUserPrompt(entry, body, names, files) },
        ],
        max_tokens: 1500,
        temperature: 0.1,
        stream: false,
        tools: [{
          type: "function",
          function: {
            name: "suggest_relation_types",
            description: "给每个只写了「关联」的对方建议一个具体关系类型",
            parameters: TOOL_SCHEMA,
          },
        }],
        tool_choice: { type: "function", function: { name: "suggest_relation_types" } },
      }),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      return { ok: false, suggestions: [], error: `LLM HTTP ${resp.status}: ${text.slice(0, 200)}` };
    }
    const data = await resp.json();
    const args = data?.choices?.[0]?.message?.tool_calls?.[0]?.function?.arguments;
    let parsed: unknown = null;
    try {
      parsed = typeof args === "string" ? JSON.parse(args) : args;
    } catch {
      parsed = null;
    }
    const suggestions = sanitizeSuggestions(parsed, names);
    if (suggestions.length === 0) {
      return { ok: false, suggestions, error: "小鲶没给出可用的建议, 稍后再试或手动选" };
    }
    return { ok: true, suggestions };
  } catch (error) {
    return { ok: false, suggestions: [], error: String(error) };
  }
}
