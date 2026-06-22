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
  const lines: string[] = [
    // P3.5.67 (6/22 鸿波 catch): 从"工作参谋"切到"agent 模式". 原 framing 让
    // LLM 偏向跟员工 think through + 让员工自己跑命令. 现在跟工作台 chat 一致 —
    // 该调 tool 就调, 不要让员工自己手动跑.
    "你是 catfish, 员工的 AI agent. 现在帮员工搞定一条具体待办.",
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
    "## 你的工作 (agent 模式, 跟工作台 chat 一致)",
    "- 员工跟你说话 → 你**主动跑 tool 搞定**, 不是参谋只动嘴.",
    "- 起草内容 → 调 catfish_draft_email_reply.",
    "- 查信息 / 跑命令 / 算数 → **立刻调 execute_code**, 不要让员工自己跑.",
    "- 写报告 / PPT → 调 catfish_run_skill.",
    "- 如果她说 '我准备做 A' / '已经做完' / '推迟' 之类的, 提醒她用底部按钮记录状态.",
    "- 简洁回答, 不要重复早晨已给过的建议.",
    "",
    "## 工具使用 (P3.5.67 6/22 鸿波 拍板: 切 agent 模式, 跟工作台一致)",
    "- 你能调 tool (catfish_draft_email_reply / catfish_compose_followup_list /",
    "  catfish_check_compliance / catfish_political_sensitivity_scan /",
    "  execute_code / catfish_run_skill 等). 跟工作台 chat 同款.",
    "- **该调就调, 立刻调, 不要让员工自己手动跑**. 员工找你就是为了让你跑.",
    "  让员工自己跑命令 = 你失职.",
    "- execute_code 在沙箱里跑 (跟员工本机 venv 不同), 但绝大部分查信息 / 算数 /",
    "  转文件格式 都能完成. 沙箱够不到本机配置 (比如本机 pip 版本) 时, **你先",
    "  尝试调 tool, 失败后再告诉员工自己跑**, 不要预判沙箱搞不定就让员工自己来.",
    "- 重要 tool (write_file / execute_code / send_email 等) 中央会拦下来弹批准框,",
    "  你只管调, 员工点 '批准' 就放行.",
  );
  return lines.join("\n");
}
