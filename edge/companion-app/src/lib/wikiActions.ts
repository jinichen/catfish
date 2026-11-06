import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface WikiActionBinding {
  id: string;
  label: string;
  description: string;
  /** 11/06: 执行交给小鲶 —— 技能路径 / 装在哪 / 能不能直接调 (有 script.py 或 workflow.yaml) */
  skill_path: string;
  skill_dir: string | null;
  runnable: boolean;
  executor: string;
  approval: string;
  available: boolean;
  reason: string | null;
}

export const wikiResolveActions = (actionRefs: string[]) =>
  rawInvoke<WikiActionBinding[]>("wiki_resolve_actions", { actionRefs });

export const wikiListActions = () =>
  rawInvoke<WikiActionBinding[]>("wiki_list_actions");

/** 同一条知识 + 同一个行动, 一次只开一段对话 (startEmailChat 按 key 去重) */
export const wikiActionChatKey = (relPath: string, actionId: string) => `wiki-action:${actionId}:${relPath}`;

/**
 * 11/06 鸿波: 知识页「执行」交给小鲶。原来直接调 catfish_run_skill 且一个参数都不传 ——
 * 周报 / 汇报 / 立项缺必填项一点就报错, EIS 用代码里写死的默认账号, 只有说明的技能根本跑不了。
 * 现在开一段对话: 小鲶读这条知识, 从里面取参数, 缺的问员工; 对外动作照常先确认。
 */
export function wikiActionPrompt(
  action: Pick<WikiActionBinding, "label" | "skill_path" | "skill_dir" | "runnable">,
  entry: { title: string; relPath: string },
): string {
  const how = action.runnable
    ? `用 catfish_run_skill 调技能 ${action.skill_path}; 参数不清楚先传 params={"_help": true} 看要什么。`
    : `这个技能只有操作说明, 没有可以直接调的脚本: 先读 ${action.skill_dir ?? action.skill_path}/SKILL.md, 照着做。`;
  return [
    `用「${action.label}」处理知识库里的《${entry.title}》。`,
    `先用 catfish_wiki_read (rel_path=${entry.relPath}, 读不到就用 name=${entry.title}) 读这条知识和它的关系, 技能要的输入优先从里面取。`,
    how,
    "缺的输入问我, 不要编; 登录、提交、发送这类对外操作照常先问我。做完告诉我结果, 产出了文件就说在哪。",
  ].join("\n");
}

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
