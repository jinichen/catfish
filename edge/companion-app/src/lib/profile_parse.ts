/**
 * 把 LLM 吐出来的东西解成 Profile。
 *
 * 2026-08-15 从 lib/profile.ts 切出来。纯搬迁, 逻辑一行未改。
 *
 * ⚠ robustJsonParse 在 src/lib 下**现在有三份**: 这里、briefing_advisor.ts:1454、
 *   wikiLinkSuggest.ts:98 (那份还是 export 的)。三份都已经带上 brace counting
 *   (6/15 P3.4.C 修的那个 truncated JSON 洞), 眼下没漂出 bug —— 但下次只修一处
 *   就会漂。合成一份是改逻辑, 不在这次纯搬迁里做, 已另记。
 */

import type {
  Formality,
  KeyPerson,
  KeyProject,
  Personality,
  Profile,
  Structure,
  Verbosity,
} from "./profile";

/** 鲁棒 JSON 解析 — LLM 输出常含前后解释文字 (e.g. "现在我已经分析完..."),
 *  剥 markdown 反引号 + brace-balanced match 找第一个完整 `{...}` 块.
 *  失败返 null, 不抛.
 *
 *  P3.4.C (6/15 鸿波): 改算法 indexOf+lastIndexOf → brace counting balanced.
 *  跟 briefing_advisor.ts robustJsonParse 同款修, 救 LLM truncated JSON.
 *  老算法在 LLM 输出 truncated 时挂 (lastIndexOf("}") 命中 reasoning 引用里的
 *  `}` 不是 JSON 闭合, 截出来不合法).
 */
export function robustJsonParse(content: string): unknown | null {
  if (typeof content !== "string") return null;
  const s = content
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/```$/, "")
    .trim();
  // 1. 直接 parse 试一次
  try {
    return JSON.parse(s);
  } catch {
    /* 落空走 brace counting */
  }
  // 2. brace counting — 找第一个 balanced `{...}` 跳过 reasoning 里的 `{}` 引用
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
        try {
          return JSON.parse(s.slice(first, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;  // LLM 输出真截断, JSON 未闭合
}

export function parseProfileFromLLM(raw: unknown): Profile | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;

  const tier = obj.tier;
  if (tier !== "frontline" && tier !== "mid" && tier !== "senior") {
    console.warn("[profile] tier 非法:", tier);
    return null;
  }
  const centralStateRaw = obj.centralState ?? obj.central_state;
  const centralState: "none" | "weak" | "strong" =
    centralStateRaw === "strong" ? "strong" : centralStateRaw === "weak" ? "weak" : "none";

  const style = typeof obj.style === "string" ? obj.style : "未识别";
  const confidence =
    typeof obj.confidence === "number" ? Math.max(0, Math.min(1, obj.confidence)) : 0.5;

  const keyPeople: KeyPerson[] = [];
  const rawPpl = obj.keyPeople ?? obj.key_people;
  if (Array.isArray(rawPpl)) {
    for (const p of rawPpl.slice(0, 10)) {
      if (!p || typeof p !== "object") continue;
      const pp = p as Record<string, unknown>;
      const name = typeof pp.name === "string" ? pp.name : null;
      const relation = typeof pp.relation === "string" ? pp.relation : null;
      if (!name || !relation) continue;
      keyPeople.push({
        name,
        relation,
        project: typeof pp.project === "string" ? pp.project : undefined,
        lastContact:
          typeof pp.lastContact === "string"
            ? pp.lastContact
            : typeof pp.last_contact === "string"
            ? pp.last_contact
            : undefined,
      });
    }
  }

  const keyProjects: KeyProject[] = [];
  const rawProj = obj.keyProjects ?? obj.key_projects;
  if (Array.isArray(rawProj)) {
    for (const p of rawProj.slice(0, 5)) {
      if (!p || typeof p !== "object") continue;
      const pp = p as Record<string, unknown>;
      const name = typeof pp.name === "string" ? pp.name : null;
      const status = typeof pp.status === "string" ? pp.status : null;
      if (!name || !status) continue;
      keyProjects.push({
        name,
        status,
        client: typeof pp.client === "string" ? pp.client : undefined,
        deadline: typeof pp.deadline === "string" ? pp.deadline : undefined,
      });
    }
  }

  const evidence: string[] = [];
  if (Array.isArray(obj.evidence)) {
    for (const e of obj.evidence) {
      if (typeof e === "string") evidence.push(e);
    }
  }

  // 5/21 cold start: personality 字段 (Optional)
  let personality: Personality | undefined;
  const rawPers = obj.personality;
  if (rawPers && typeof rawPers === "object") {
    const pp = rawPers as Record<string, unknown>;
    const vRaw = String(pp.verbosity ?? "balanced");
    const sRaw = String(pp.structure ?? "balanced");
    const fRaw = String(pp.formality ?? "balanced");
    const verbosity: Verbosity =
      vRaw === "concise" ? "concise" : vRaw === "verbose" ? "verbose" : "balanced";
    const structure: Structure =
      sRaw === "list_heavy" ? "list_heavy" : sRaw === "prose_heavy" ? "prose_heavy" : "balanced";
    const formality: Formality =
      fRaw === "formal" ? "formal" : fRaw === "casual" ? "casual" : "balanced";
    const signatureWords: string[] = [];
    if (Array.isArray(pp.signatureWords ?? pp.signature_words)) {
      for (const w of (pp.signatureWords ?? pp.signature_words) as unknown[]) {
        if (typeof w === "string") signatureWords.push(w);
      }
    }
    const sampleSentences: string[] = [];
    if (Array.isArray(pp.sampleSentences ?? pp.sample_sentences)) {
      for (const s of (pp.sampleSentences ?? pp.sample_sentences) as unknown[]) {
        if (typeof s === "string") sampleSentences.push(s);
      }
    }
    const sourceCount =
      typeof pp.sourceCount === "number"
        ? pp.sourceCount
        : typeof pp.source_count === "number"
        ? pp.source_count
        : 0;
    personality = {
      verbosity,
      structure,
      formality,
      signatureWords: signatureWords.slice(0, 10),
      sampleSentences: sampleSentences.slice(0, 3),
      sourceCount,
    };
  }

  return {
    tier,
    centralState,
    style,
    keyPeople,
    keyProjects,
    confidence,
    evidence: evidence.length > 0 ? evidence : ["LLM 推断, 无显式 evidence 字段"],
    personality,
    updatedAt: new Date().toISOString(),
    nextRecomputeAt: "",  // caller 填
  };
}
