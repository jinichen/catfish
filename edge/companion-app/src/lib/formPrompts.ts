/**
 * 一起填表: 交给小鲶的那句话 (10/17)。走 useUIStore.startEmailChat 那条「工作台一次性请求」路径 ——
 * 它开新会话、走 useChat.send(), 跟员工自己打字发一样。名字带 email 是历史原因, 键只用来去重。
 */
import type { IncomingRecord, OutgoingForm } from "./tauri_forms";

export const prefillChatKey = (formId: string) => `form-prefill:${formId}`;
export const summaryChatKey = (formId: string) => `form-summary:${formId}`;

/** 收件人: 「让小鲶先填」。小鲶只能存草稿, 提交由员工在表单上点 (工具里没有提交动作)。 */
export function prefillPrompt(rec: IncomingRecord): string {
  const fields = rec.form.fields.map((f) => `「${f.label}」`).join("、");
  return [
    `${rec.owner} 请我填一张表《${rec.form.title}》(form_id=${rec.form_id})${rec.form.deadline ? `, ${rec.form.deadline} 截止` : ""}。`,
    rec.form.note ? `发起人的说明: ${rec.form.note}` : "",
    `要填: ${fields}。`,
    "请先用 catfish_form action=get 看字段, 再从我本机的邮件、文件、知识库、任务里找每一项的值;",
    "找不到的留空, 不要编。最后用 catfish_form action=draft 存草稿 (sources 写每项出处)。",
    "不要替我提交, 存好后告诉我哪些填了、哪些空着, 我去「协同」页核对后自己交。",
  ].filter(Boolean).join("\n");
}

/** 发起人: 「让小鲶汇总」。数据都在本机, 小鲶读 responses 来答。 */
export function summaryPrompt(form: OutgoingForm): string {
  return [
    `帮我看一下我发起的表《${form.title}》(form_id=${form.form_id}) 的收集结果:`,
    "先用 catfish_form action=responses 读全部回答, 告诉我谁交了、谁没交、谁不填 (原因),",
    "再按字段把要点汇总一下, 有明显异常或缺项的单独列出来。数字照实际回答说, 不要编。",
  ].join("\n");
}
