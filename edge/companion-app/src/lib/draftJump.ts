/** 小鲶在对话里存了草稿 → 回合结束后切到邮件页草稿箱, 选中那封 (9/27, 9/29 重做)。
 *
 * 9/27 第一版靠推断: 从 hermes 的流里认 `catfish_email_create_draft` 工具完成 →
 * 回合结束再连一次服务器列草稿箱 → 按 Date 头找一封"刚存的"。9/29 鸿波:
 * "Windows 版落草稿箱后依旧没有跳转到草稿箱, 还需要人工选择邮件 → 草稿箱"。
 * 那条链上每一环都可能悄悄断, 断了只是不跳、不报错:
 *   · 小鲶不一定走那个工具 —— 邮件 skill 教的是在终端里跑 catfish-email,
 *     终端里直接 `catfish-email draft` 一样能存, 工具名认不出来
 *   · 回合结束现连服务器列草稿箱, 慢 / 连不上就放弃
 *   · 按 Date 头比时间, 时区或时钟一点偏差就判成"不是刚存的"
 *
 * 现在不猜了: 邮件组件每次存草稿成功都记下是哪一封、什么时候
 * (catfish_email/last_draft.py), 不管是谁调的。回合结束读这一笔 —— 存在这一
 * 回合开始之后, 就跳过去选中它。存失败了就没有这一笔, 自然不跳。
 */
import { emailLastDraft } from "./tauri";
import { useUIStore } from "../store/ui";

/** Python 的 time.time() 和 Date.now() 是同一台机器的钟; 留一点给浮点和换算 */
const CLOCK_SLACK_MS = 1000;

/** 回合结束时调 (sendMessage 的 finally)。员工不在对话页就不打扰。 */
export async function jumpToFreshDraft(roundStartedAt: number): Promise<void> {
  if (useUIStore.getState().activeTab !== "chat") return;
  try {
    const last = await emailLastDraft();
    if (last?.id && last.created_at * 1000 >= roundStartedAt - CLOCK_SLACK_MS) {
      useUIStore.getState().openEmailDraft(last.id);
    }
  } catch (e) {
    console.warn("[draftJump] 读不到最近存的草稿, 不跳转:", e);
  }
}
