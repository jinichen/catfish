/** 用户气泡里只显示员工自己写的话 (10/13 鸿波「显示还有这些内容会干扰视觉」「为什么还是有」)。
 *
 * hermes 的 state.db 里 role=user 的消息不全是员工写的。10/13 盘点本机 state.db, 有这几类:
 *   1. 发给模型的附件材料: chatWire.toWire 拼进去的「=== 附件: x.pdf (PDF · 4 页) ===」+ 完整路径 +
 *      预览正文 (以及 [共 N 个文件。] / === 微信聊天记录: )。接在员工的话后面。
 *   2. 整条都是系统消息, 以"用户"身份存的:
 *      [CONTEXT COMPACTION — REFERENCE ONLY] 压缩摘要 (210 条) · [IMPORTANT: You are running as a
 *      scheduled cron job 定时任务 (183) · [IMPORTANT: The user has invoked the "x" skill 技能 (83) ·
 *      [ASYNC DELEGATION … COMPLETE 后台子任务 (72) · [System: … truncated / cut off / too large
 *      续写 (75) · [Your active task list was preserved 任务清单 (5) · 早安顾问的「# 时间锚点」
 *      (约 800) · 「# 长期记忆 distilled_facts」记忆整理 (92)。
 *   3. 员工的话前面粘了一段系统说明: 「[System note: …]\n\n你再试一次」, 以及
 *      「[Image attached at: /Users/…/img.jpg]」这种图片路径行。
 *
 * 这里只管**显示**: 1 藏起来、附件名做成标签; 2 整条显示成一行灰色系统说明 (可展开看原文);
 * 3 去掉前缀 / 路径行只留员工的话。msg.content 本身不动 —— chatRequest 每次把整段历史
 * toWire 发给模型, 改了模型就看不到附件 / 摘要了。
 */

// 附件材料: 认完整的标题行, 不要求前面有空行 —— 压缩摘要里换行会被吃掉 (10/13 第二张截图)
const ATTACH_HEADER = /\s*=== (?:附件|微信聊天记录): .+? \(.*?\) ===/;
const MANY_FILES = /\s*\[共 \d+ 个文件。/;
const NAME_RE = /=== (?:附件|微信聊天记录): (.+?) \(.*?\) ===/g;

// 整条是系统消息: [开头, 给员工看的一句话]。label 里的 {skill} 用技能名替换。
const SYSTEM_WHOLE: Array<[RegExp, string]> = [
  [/^\[CONTEXT COMPACTION/, "小鲶把前面的对话整理成了摘要"],
  [/^\[IMPORTANT: You are running as a scheduled cron job/, "定时任务触发"],
  [/^\[IMPORTANT: The user has invoked the "([^"]+)" skill/, "按技能「{skill}」执行"],
  [/^\[IMPORTANT: The following skill\(s\) were listed for this job/, "定时任务提示"],
  [/^\[ASYNC DELEGATION/, "后台子任务做完了"],
  [/^\[System: (?:Your|The) previous (?:response|tool call)/, "小鲶接着上次没说完的继续"],
  [/^\[Your active task list was preserved/, "保留了进行中的任务清单"],
  [/^(?:<!-- catfish:advisor[^>]*-->\s*)?# 时间锚点/, "早安顾问的准备材料"],
  [/^现在 \d{1,2}:\d{2}\.\s*\n\s*\n日历:/, "早安顾问的准备材料"],
  [/^以下是员工跟 catfish AI 在某条待办/, "整理待办进展"],
  [/^# 长期记忆 distilled_facts/, "整理长期记忆"],
  // lib/emailDraft.ts 起草邮件回复: 开头可能是知识库背景 / 往来邮件 / 「要回的是这封」, 结尾固定
  [/请直接给回复草稿正文 \(不要带 Subject\/收件人\/签名\)\.?\s*$/, "起草邮件回复"],
  // 整条只有一段 [System note: …] (后面没有员工的话) —— 多是 hermes 重启后恢复会话
  // 只认单独一段 (不跨换行), 员工的话以 "]" 结尾时 (「看 [[wiki]]」) 不会被整条吞掉
  [/^\[System note: [^\n]*gateway shutdown[^\n]*\]\s*$/, "小鲶重启后接着之前的对话"],
  [/^\[System note: [^\n]*\]\s*$/, "系统提示"],
];
// 粘在员工话前面的系统说明 (到 "]" + 空行为止)
const SYSTEM_PREFIX = /^\[System note: [\s\S]*?\]\s*\n\s*\n/;
// 图片路径行 —— 路径对员工没用, 图片本身另有显示
const IMAGE_PATH_LINE = /^\[Image attached at: [^\]\n]*\]\s*$/gm;

export interface UserBubbleText {
  /** 员工自己写的话 */
  text: string;
  /** 被藏起来的附件材料 (原样, 含开头的空白); 没有就是 "" */
  hidden: string;
  /** 附件材料里认出来的文件名, 去重有序 */
  fileNames: string[];
  /** 整条都是系统消息时: 给员工看的一句话; 否则 null */
  system: string | null;
}

export function splitUserBubbleText(content: string | null | undefined): UserBubbleText {
  const raw = content ?? "";
  for (const [re, label] of SYSTEM_WHOLE) {
    const m = re.exec(raw);
    if (m) return { text: "", hidden: "", fileNames: [], system: label.replace("{skill}", m[1] ?? "") };
  }
  let s = raw.replace(SYSTEM_PREFIX, "");
  s = s.replace(IMAGE_PATH_LINE, "").replace(/^\[screenshot\]$/gm, "🖼 截图");
  let cut = -1;
  for (const re of [ATTACH_HEADER, MANY_FILES]) {
    const m = re.exec(s);
    if (m && (cut < 0 || m.index < cut)) cut = m.index;
  }
  const visible = (cut < 0 ? s : s.slice(0, cut)).replace(/\n{3,}/g, "\n\n").trim();
  if (cut < 0) {
    // 只剩前缀说明、没有员工的话 → 当系统消息显示
    if (!visible && raw.trim()) return { text: "", hidden: "", fileNames: [], system: "系统提示" };
    return { text: s === raw ? raw : visible, hidden: "", fileNames: [], system: null };
  }
  const hidden = s.slice(cut);
  const fileNames: string[] = [];
  for (const m of hidden.matchAll(NAME_RE)) {
    if (!fileNames.includes(m[1])) fileNames.push(m[1]);
  }
  return { text: visible, hidden, fileNames, system: null };
}
