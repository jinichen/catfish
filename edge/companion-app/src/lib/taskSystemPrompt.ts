/** Task 上下文 system prompt 构造 (P3.3.19 C Phase 3, 6/11).
 *
 * 抽自 BriefingTwoColumnView.tsx (P3.3.10), 给 DetailPane + ChatTab task picker 共享.
 *
 * caller: DetailPane mount 时, sessionCreate({systemPrompt: buildTaskSystemPrompt(task)})
 *   → 写进 sessions.system_prompt 持久化. 不再每次 send 重算 (P3.3.19 C).
 *
 * 跟 BriefingTwoColumnView.tsx 老实现行为完全一致, 只是 export 出来. 任何
 * 改动必须同时考虑 DetailPane + ChatTab 双 caller.
 */

import type { MainTask } from "./briefing_advisor";

export function buildTaskSystemPrompt(task: MainTask): string {
  // P3.5.212 (7/10 鸿波 校正 audit): 走 Companion → Hermes → Gateway 架构,
  // task chat 过 hermes API server, hermes 已把 catfish-memory prefetch
  // (P3.5.211 事实为准军规) layered on top of core system prompt. Companion
  // 塞进 messages 的 buildTaskSystemPrompt 会被 hermes 提取作 ephemeral
  // system 挂在军规后面 (api_server.py:1859-1873 抽 system 逻辑), 加一段
  // P3.5.209/210 事实为准段是**冗余** — hermes 那 48K memory prefetch 已经
  // 有. 撤回 P3.5.209/210, 军规单点在 hermes 保生效, Companion 只塞 task
  // 元数据 (让 LLM 知道当前讨论的是哪条待办).
  const lines: string[] = [
    "你是 catfish, 员工的工作参谋. 现在跟员工讨论一条具体待办.",
    "",
    "## 待办",
    `标题: ${task.title}`,
    `紧急度: ${task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}`,
  ];
  if (task.reason) {
    lines.push(`理由: ${task.reason}`);
  }
  if (task.contextRefs.length > 0) {
    lines.push("", "## 历史上下文");
    task.contextRefs.forEach((r) => lines.push(`- ${r}`));
  }
  if (task.complianceFlags.length > 0) {
    lines.push("", "## 合规提示");
    task.complianceFlags.forEach((f) => {
      lines.push(`- ${f.severity} 合规 (${f.type}): ${f.reason}${f.suggestion ? ` — 建议: ${f.suggestion}` : ""}`);
    });
  }
  if (task.politicalFlags.length > 0) {
    lines.push("", "## 关键关系");
    task.politicalFlags.forEach((f) => {
      lines.push(`- ${f.severity}: ${f.reason}`);
    });
  }
  if (task.options.length > 0) {
    lines.push("", "## 早晨 LLM 给的 3 个口径建议 (参考, 你可以反驳或调整)");
    task.options.forEach((o) => {
      lines.push(`${o.label} (${o.tone}): ${o.summary}${o.aiLean ? " [早晨 AI 倾向]" : ""}`);
    });
  }
  lines.push(
    "",
    "## 你的工作",
    "- 员工现在跟你直接说. 回答她关于这条待办的具体问题.",
    "- 起草内容 / 帮她做决策 / 给具体下一步.",
    "- 如果她说 '我准备做 A' / '已经做完' / '推迟' 之类的, 提醒她用底部按钮记录状态.",
    "- 简洁回答, 不要重复早晨已给过的建议.",
    // P3.5.212: '事实为准' 军规撤到 hermes catfish-memory prefetch (P3.5.211)
    // 单点生效. 这里不再重复.
    "",
    "## 工具使用 (P3.3.10)",
    "- 你能调 tool (catfish_draft_email_reply / catfish_compose_followup_list /",
    "  catfish_check_compliance / catfish_political_sensitivity_scan /",
    "  execute_code / catfish_run_skill 等). 跟工作台 chat 同款.",
    "- 该调就调, 不要装看不到 tool. 起草邮件用 catfish_draft_email_reply, 跑数算用",
    "  execute_code, 写报告/PPT 用 catfish_run_skill.",
    "- 重要 tool (write_file / execute_code / send_email 等) 中央会拦下来弹批准框,",
    "  你只管调, 员工点 '批准' 就放行.",
  );
  return lines.join("\n");
}
