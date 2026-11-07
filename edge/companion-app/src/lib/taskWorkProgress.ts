/**
 * 11/07 「接下就让小鲶开工」做到哪了, 写成一句话 (群里任务卡那一栏、「协同事项」列表都用)。
 * 参照 hermes 群聊把成员 AI 的实时进度单独显示 (不写进群记录): 原来做完之前只显示「在做」。
 */
import type { TaskWorkProgress } from "./tauri_colleague_tasks";
import { TOOL_ZH } from "./agentActivityI18n";

/** 鲶鱼自己的工具 (hermes 那张表里没有): 去掉 mcp 前缀和 catfish_ 后查这里 */
const CATFISH_TOOL_ZH: Readonly<Record<string, string>> = {
  email_search: "搜邮件", email_read: "读邮件", email_list: "看邮件", email_create_draft: "起草邮件",
  local_search: "搜本机文件", wiki_search: "查知识库", wiki_read: "读知识库", wiki_list: "看知识库",
  list_tasks: "看任务", create_task: "记任务", search_sessions: "翻以前的对话", form: "存结果",
  browser_goto: "打开网页", browser_click: "点网页", browser_fill: "填网页", browser_screenshot: "看网页",
  calendar_list: "看日程", run_skill: "跑技能", style_fingerprint_get: "看文风",
};

export function toolName(raw: string): string {
  const name = raw.trim();
  if (!name) return "";
  if (TOOL_ZH[name]) return TOOL_ZH[name];
  const bare = name.replace(/^mcp_+.*?_+(?=catfish_)/i, "").replace(/^mcp_+catfish_local_search_+/i, "").replace(/^catfish_/, "");
  return CATFISH_TOOL_ZH[bare] ?? TOOL_ZH[bare] ?? `用 ${bare}`;
}

export function workText(work: TaskWorkProgress | null | undefined): string {
  if (!work) return "小鲶准备开工";
  if (!work.running) return "小鲶做完了, 在存结果";
  if (!work.steps) return "小鲶在看任务";
  const d = work.detail;
  const detail = d ? `「${d.length > 20 ? `${d.slice(0, 20)}…` : d}」` : "";
  return `小鲶在做 · 第 ${work.steps} 步 ${toolName(work.tool)}${detail}`;
}
