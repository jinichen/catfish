import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface WikiActionBinding {
  id: string;
  label: string;
  description: string;
  executor: string;
  approval: string;
  available: boolean;
  reason: string | null;
}

export interface WikiActionRun {
  action_id: string;
  label: string;
  result: {
    ok: boolean;
    tool: string;
    result: unknown;
    error: string | null;
  };
}

export const wikiResolveActions = (actionRefs: string[]) =>
  rawInvoke<WikiActionBinding[]>("wiki_resolve_actions", { actionRefs });

export const wikiListActions = () =>
  rawInvoke<WikiActionBinding[]>("wiki_list_actions");

export const wikiExecuteAction = (actionId: string, confirmed: boolean) =>
  rawInvoke<WikiActionRun>("wiki_execute_action", { actionId, confirmed });

function unquoteYaml(value: string): string {
  const trimmed = value.trim();
  if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      return JSON.parse(trimmed) as string;
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  return trimmed.replace(/^['"]|['"]$/g, "");
}

function splitInlineList(value: string): string[] {
  const body = value.trim().replace(/^\[/, "").replace(/\]$/, "");
  const output: string[] = [];
  let token = "";
  let quote: string | null = null;
  for (const char of body) {
    if ((char === '"' || char === "'") && (quote === null || quote === char)) {
      quote = quote === null ? char : null;
    }
    if (char === "," && quote === null) {
      if (token.trim()) output.push(unquoteYaml(token));
      token = "";
    } else {
      token += char;
    }
  }
  if (token.trim()) output.push(unquoteYaml(token));
  return output.filter(Boolean);
}

/** Read only action_refs from a standard Wiki frontmatter block. */
export function parseWikiActionRefs(frontmatter: string): string[] {
  const line = frontmatter.split("\n").find((item) => /^\s*action_refs\s*:/.test(item));
  if (!line) return [];
  const value = line.replace(/^\s*action_refs\s*:\s*/, "").trim();
  if (!value.startsWith("[")) return value ? [unquoteYaml(value)] : [];
  return [...new Set(splitInlineList(value))];
}

/** Update only action_refs, preserving all unrelated Wiki frontmatter. */
export function writeWikiActionRefs(content: string, actionRefs: string[]): string {
  if (!content.startsWith("---\n")) throw new Error("该条目缺少标准 frontmatter");
  const end = content.indexOf("\n---", 4);
  if (end < 0) throw new Error("该条目的 frontmatter 不完整");
  const refs = [...new Set(actionRefs.map((ref) => ref.trim()).filter(Boolean))];
  if (refs.some((ref) => !/^[A-Za-z0-9._:-]{1,120}$/.test(ref))) {
    throw new Error("action_id 只允许字母、数字、. : _ -，长度不超过 120");
  }
  const serialized = refs.map((ref) => JSON.stringify(ref)).join(", ");
  const lines = content.slice(4, end).split("\n");
  let found = false;
  const nextLines = lines.map((line) => {
    if (/^\s*action_refs\s*:/.test(line)) {
      found = true;
      return `action_refs: [${serialized}]`;
    }
    return line;
  });
  if (!found) nextLines.push(`action_refs: [${serialized}]`);
  return `---\n${nextLines.join("\n")}\n---${content.slice(end + 4)}`;
}
