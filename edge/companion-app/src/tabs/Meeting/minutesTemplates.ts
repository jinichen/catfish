/** 会议纪要模版的示例和说明 (10/3)。占位符跟 tool-bridge meeting_minutes_template.py 一致。 */
import { DEFAULT_TEMPLATE_ID, type MinutesTemplate } from "../../lib/tauri_meeting";

export const EXAMPLE_TEMPLATE = `# {{会议标题}}

- 时间: {{日期}}
- 参会人: {{参会人}} (共 {{参会人数}} 人, 时长 {{时长}})

## 一、会议议题
(列出本次讨论的议题, 每条一句)

## 二、讨论情况
(按议题写各方主要意见, 注明是谁说的)

## 三、会议决定
(逐条写会上明确的决定)

## 四、下一步工作
| 事项 | 负责人 | 完成时限 |
|---|---|---|
(每个待办一行)
`;

export const PLACEHOLDER_HELP =
  "怎么写: 标题、表格照写; 括号里写这一节要写什么, 小鲶照着填, 括号本身不会出现在纪要里。\n" +
  "可用占位符 (生成时自动换成真值): {{会议标题}} {{日期}} {{参会人}} {{参会人数}} {{时长}}\n" +
  "「勾选的待办加入任务库」不受模版影响, 照常可用。";

const LAST_KEY = "catfish.meeting.minutesTemplate";

/** 上次选的模版 (每个员工本机记住); 模版被删了就回到缺省。 */
export function lastTemplateId(templates: MinutesTemplate[]): string {
  let id: string | null = null;
  try {
    id = window.localStorage.getItem(LAST_KEY);
  } catch {
    // 存储不可用就用缺省
  }
  return id && templates.some((t) => t.id === id) ? id : DEFAULT_TEMPLATE_ID;
}

export function rememberTemplateId(id: string): void {
  try {
    window.localStorage.setItem(LAST_KEY, id);
  } catch {
    // 记不住不影响生成
  }
}
