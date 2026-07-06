/** wikiLinkSuggest — P3.5.172 Phase C (7/3 鸿波 catch "orphan 体系" 的 AI 增强).
 *
 * 严格背景 (Phase A/B audit):
 *   员工新加"组织架构"顶级体系, body 里明文 `**市场部**` 无 `[[wikilink]]` →
 *   WikiGraph ego/subtree 严格 root cause = orphan (无 out edge, 无 in edge) →
 *   1 节点. 严格不是代码 bug, 是员工 mental model 差异: 员工上传 md 期望"有内容
 *   就有图", 系统严格从 `[[wikilink]]` 建关系.
 *
 * P3.5.172 Phase C fix (鸿波拍板): AI 增强 — 员工点"🔗 扫描关联"→ LLM 扫 body +
 *   现有 wiki title list → 建议 wikilink → 员工确认 → body 末尾加"## 关联概念"
 *   段落. 严格员工主权: 不动老 body 内容, 加新段落员工可看/删/整理.
 *
 * 复用 profile.ts P3.4.E (6/15) 模板:
 *   - 8999 直连 LiteLLM (bypass hermes 8642 agent loop)
 *   - `tool_choice="required"` + tool schema 强制 LLM 返结构化 JSON
 *   - parse `tool_calls[0].function.arguments` primary path
 *   - fallback: content robustJsonParse
 *   - `catfish_direct=1` query 让 me.ts isGatewayDirectPath 命中走 OAuth
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";
import type { WikiFileInfo } from "./tauri";

/** 严格返 LLM 建议单条. */
export interface WikiLinkSuggestion {
  /** 匹配的现有 wiki 节点 title (LLM 从传入 title list 选). */
  title: string;
  /** 匹配置信度 0-1 (>0.7 强, 0.4-0.7 中, <0.4 弱). */
  confidence: number;
  /** body 中出现的**具体片段** (员工看得懂为什么匹配). */
  snippet: string;
  /** LLM 说的"为什么匹配", 员工审阅参考. */
  reason: string;
}

/** 严格返 LLM 完整结果. */
export interface WikiLinkSuggestResult {
  ok: boolean;
  suggestions: WikiLinkSuggestion[];
  error?: string;
}

// 严格复用 profile.ts P3.4.E QUERY 模式 — 走 8999 直连 LiteLLM 支持 strict function calling
const SUGGEST_LLM_QUERY =
  "?catfish_source=companion-wiki-suggest&catfish_skip_identity=1&catfish_internal=1&catfish_direct=1";

const SUGGEST_SYSTEM_PROMPT = `你是 wiki 关系分析助手.

任务: 给定一个 concept 的 body 内容 + 现有 wiki 节点 title 列表, 找出 body 里出现且能对应到现有节点的名字, 建议加 wikilink.

严格规则:
1. 只返**明确匹配**的 — body 里的文本明确指向现有 title. 不猜, 不推理.
2. title 必须**严格来自**传入的"现有 wiki 节点"列表, 不能编造.
3. snippet 严格是 body 中**真实出现**的片段, 20-40 字, 让员工看得懂为什么匹配.
4. confidence: 1.0=完全同名, 0.7-0.9=部分同名+上下文强, 0.4-0.6=可能相关. 低于 0.4 不返.
5. 无匹配返空 suggestions[]. 不硬凑.

必须调 suggest_wikilinks tool 返 JSON. 不允许 free-text content.`;

/** 严格 tool schema — LLM 强制 JSON 输出 (P3.4.E 模式). */
const SUGGEST_TOOL_SCHEMA = {
  type: "object",
  properties: {
    suggestions: {
      type: "array",
      description: "wikilink 建议列表, 无匹配返空数组",
      items: {
        type: "object",
        properties: {
          title: {
            type: "string",
            description: "严格来自传入的现有 wiki 节点 title, 不编造",
          },
          confidence: {
            type: "number",
            minimum: 0,
            maximum: 1,
            description: "匹配置信度",
          },
          snippet: {
            type: "string",
            description: "body 中真实出现的片段",
          },
          reason: {
            type: "string",
            description: "为什么匹配 (员工审阅参考)",
          },
        },
        required: ["title", "confidence", "snippet"],
      },
    },
  },
  required: ["suggestions"],
};

/** 严格 P3.4.E robustJsonParse — 严格 fallback 处理 DeepSeek 边缘场景 truncate. */
function robustJsonParse<T = unknown>(raw: string): T | null {
  if (!raw || typeof raw !== "string") return null;
  const trimmed = raw.trim();
  try {
    return JSON.parse(trimmed);
  } catch {
    // fallback: 尝试提取 { ... } 块
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start >= 0 && end > start) {
      try {
        return JSON.parse(trimmed.slice(start, end + 1));
      } catch {
        return null;
      }
    }
    return null;
  }
}

/** 严格主入口 — 调 LLM 扫 body 找 wikilink 建议.
 *
 * @param currentTitle 当前 concept 的 title (LLM 参考不重复自己)
 * @param body 当前 concept 的 body 内容
 * @param allFiles wiki 全部 file list (WikiFileInfo[]) — 从 useWikiStore.files 拿
 * @param model 严格 caller 传 — 员工选真 chat_model (类 profile.ts P3.4.E)
 * @returns 严格 result: { ok, suggestions[], error? }
 */
export async function suggestWikilinks(
  currentTitle: string,
  body: string,
  allFiles: WikiFileInfo[],
  model: string,
): Promise<WikiLinkSuggestResult> {
  // 严格过滤: 排除当前 concept 自己, 排除 query kind (query 不适合 wikilink 关联)
  const candidateTitles = allFiles
    .filter((f) => f.title !== currentTitle)
    .filter((f) => f.kind === "entity" || f.kind === "concept")
    .map((f) => f.title);

  if (candidateTitles.length === 0) {
    return { ok: true, suggestions: [] };
  }

  if (!body || body.trim().length < 10) {
    return {
      ok: false,
      suggestions: [],
      error: "body 太短 (<10 字), 无法分析",
    };
  }

  // 严格构 user prompt — 现有 title list + 当前 body
  const userPrompt = [
    `# 现有 wiki 节点 (可以建 wikilink 的 title 列表, 共 ${candidateTitles.length} 个)`,
    "",
    candidateTitles.map((t) => `- ${t}`).join("\n"),
    "",
    `# concept "${currentTitle}" 的 body`,
    "",
    body.slice(0, 8000), // 严格限 8k body 防 prompt 过长
    body.length > 8000 ? "\n\n(body 被截断到前 8000 字, 后续内容未分析)" : "",
    "",
    "# 任务",
    "找出 body 里出现且能对应「现有 wiki 节点」列表里名字的匹配. 只返**明确匹配**, 不猜.",
    "调 suggest_wikilinks tool 返 JSON.",
  ].join("\n");

  const url = `${config.gatewayUrl}/v1/chat/completions${SUGGEST_LLM_QUERY}`;

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: SUGGEST_SYSTEM_PROMPT },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 2500, // 严格给足空间 (最多 20 条 suggestion × 100 tokens/条)
        temperature: 0.2, // 严格低温度 — 员工要 deterministic 匹配, 不要 creative
        stream: false,
        response_format: { type: "json_object" },
        tools: [
          {
            type: "function",
            function: {
              name: "suggest_wikilinks",
              description:
                "严格返 wikilink 建议列表. 只返明确匹配, 不猜, 不编造 title.",
              parameters: SUGGEST_TOOL_SCHEMA,
            },
          },
        ],
        tool_choice: {
          type: "function",
          function: { name: "suggest_wikilinks" },
        },
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text().catch(() => "");
      return {
        ok: false,
        suggestions: [],
        error: `LLM HTTP ${resp.status}: ${errText.slice(0, 200)}`,
      };
    }

    const data = await resp.json();
    const msg = data?.choices?.[0]?.message;

    // Primary path: tool_calls[0].function.arguments (P3.4.E 严格 JSON schema 校验过)
    const toolCalls = msg?.tool_calls;
    if (Array.isArray(toolCalls) && toolCalls.length > 0) {
      const args = toolCalls[0]?.function?.arguments;
      if (typeof args === "string" && args.trim()) {
        const parsed = robustJsonParse<{ suggestions?: WikiLinkSuggestion[] }>(
          args,
        );
        if (parsed && Array.isArray(parsed.suggestions)) {
          // 严格 sanitize — 过滤 title 必须在 candidateTitles 里 (LLM 编造防御)
          const validTitles = new Set(candidateTitles);
          const clean = parsed.suggestions
            .filter(
              (s): s is WikiLinkSuggestion =>
                !!s &&
                typeof s.title === "string" &&
                validTitles.has(s.title) &&
                typeof s.confidence === "number" &&
                s.confidence >= 0.4,
            )
            .sort((a, b) => b.confidence - a.confidence); // 严格 confidence 降序
          return { ok: true, suggestions: clean };
        }
      }
    }

    // Fallback: LLM 没遵守 tool_choice (DeepSeek 边缘场景), 回退 content parse
    const content = msg?.content;
    if (typeof content === "string" && content.trim()) {
      const parsed = robustJsonParse<{ suggestions?: WikiLinkSuggestion[] }>(
        content,
      );
      if (parsed && Array.isArray(parsed.suggestions)) {
        const validTitles = new Set(candidateTitles);
        const clean = parsed.suggestions
          .filter(
            (s): s is WikiLinkSuggestion =>
              !!s &&
              typeof s.title === "string" &&
              validTitles.has(s.title) &&
              typeof s.confidence === "number" &&
              s.confidence >= 0.4,
          )
          .sort((a, b) => b.confidence - a.confidence);
        return { ok: true, suggestions: clean };
      }
    }

    return {
      ok: false,
      suggestions: [],
      error: "LLM 返回无法解析 (tool_calls + content 均失败)",
    };
  } catch (e) {
    return {
      ok: false,
      suggestions: [],
      error: (e as Error).message || String(e),
    };
  }
}

/** 严格应用建议 — 把选中的 title 加到 body 末尾 "## 关联概念" 段落.
 *
 * 严格员工主权 (P3.5.172 决策): 不动老 body 内容, 只在末尾加新段落. 员工可以
 * 之后手动整理/删/移动.
 *
 * 严格逻辑:
 *   - 若老 body 已有 "## 关联概念" 段落 → 合并 (去重后追加新 titles)
 *   - 若无 → 末尾加新段落 `\n\n## 关联概念\n\n- [[title1]]\n- [[title2]]\n`
 *
 * @returns 严格新 body (调用方 wikiUpdateFile 写回)
 */
export function applyWikilinkSuggestions(
  currentBody: string,
  selectedTitles: string[],
): string {
  if (selectedTitles.length === 0) return currentBody;

  // 严格去重
  const uniqueTitles = Array.from(new Set(selectedTitles));

  // 严格检测老 body 是否已有 "## 关联概念" 段落
  const HEADER = "## 关联概念";
  const headerIdx = currentBody.indexOf(HEADER);

  if (headerIdx < 0) {
    // 严格无此段落 → 末尾加新段
    const lines = uniqueTitles.map((t) => `- [[${t}]]`).join("\n");
    // 严格保 body 末尾干净: 若已有 trailing newline 用一个, 否则加两个
    const trailing = currentBody.endsWith("\n\n")
      ? ""
      : currentBody.endsWith("\n")
        ? "\n"
        : "\n\n";
    return `${currentBody}${trailing}${HEADER}\n\n${lines}\n`;
  }

  // 严格有此段落 → 严格合并
  // 找到段落末尾 (下一个 `## ` header 或文件末尾)
  const restAfterHeader = currentBody.slice(headerIdx + HEADER.length);
  const nextHeaderMatch = restAfterHeader.match(/\n##\s+/);
  const sectionEnd =
    nextHeaderMatch && typeof nextHeaderMatch.index === "number"
      ? headerIdx + HEADER.length + nextHeaderMatch.index
      : currentBody.length;

  const beforeSection = currentBody.slice(0, headerIdx);
  const section = currentBody.slice(headerIdx, sectionEnd);
  const afterSection = currentBody.slice(sectionEnd);

  // 严格解析老 section 里已有 wikilink
  const existingLinks = new Set<string>();
  const linkPattern = /\[\[([^\]]+)\]\]/g;
  let m: RegExpExecArray | null;
  while ((m = linkPattern.exec(section)) !== null) {
    existingLinks.add(m[1].trim());
  }

  // 严格只加**新**的 title (老 section 里没有的)
  const newTitles = uniqueTitles.filter((t) => !existingLinks.has(t));
  if (newTitles.length === 0) {
    // 全部已存在 → body 不变
    return currentBody;
  }

  const newLines = newTitles.map((t) => `- [[${t}]]`).join("\n");
  // 严格保 section 末尾干净
  const sectionTrimmed = section.replace(/\s+$/, "");
  const merged = `${sectionTrimmed}\n${newLines}\n`;

  return `${beforeSection}${merged}${afterSection}`;
}
