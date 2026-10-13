/** 用户气泡里只显示员工自己写的话 (10/13 鸿波「显示还有这些内容会干扰视觉」)。
 *
 * 发给模型时 chatWire.toWire 会把附件拼进 user 消息 (「=== 附件: x.pdf (PDF · 4 页) ===」
 * + 完整路径 + 预览正文 + 「用 execute_code 读完整」等给模型看的话)。hermes 把拼好的那一版
 * 存进 state.db, 切回老会话时 sessionMessages.dbMessageToChat 原样读回来 —— 气泡里就成了
 * 一大段内部材料。
 *
 * 这里只管**显示**: 从第一个附件标记处切开, 前面是员工的话, 后面藏起来, 附件名做成小标签。
 * msg.content 本身不动 —— chatRequest 每次把整段历史 toWire 发给模型, 改了模型就看不到附件。
 */

// chatWire.formatFileAttachment / formatManyFiles / wechatImport.formatWeChatAttachment 的开头
const MARKERS = [/\n\n=== 附件: /, /\n\n\[共 \d+ 个文件。/, /\n\n=== 微信聊天记录: /];
const NAME_RE = /^=== (?:附件|微信聊天记录): (.+?) \(.*\) ===$/gm;

export interface UserBubbleText {
  /** 员工自己写的话 */
  text: string;
  /** 被藏起来的附件材料 (原样, 含开头的换行); 没有就是 "" */
  hidden: string;
  /** 附件材料里认出来的文件名, 去重有序 */
  fileNames: string[];
}

export function splitUserBubbleText(content: string | null | undefined): UserBubbleText {
  const s = content ?? "";
  let cut = -1;
  for (const re of MARKERS) {
    const m = re.exec(s);
    if (m && (cut < 0 || m.index < cut)) cut = m.index;
  }
  if (cut < 0) return { text: s, hidden: "", fileNames: [] };
  const hidden = s.slice(cut);
  const fileNames: string[] = [];
  for (const m of hidden.matchAll(NAME_RE)) {
    if (!fileNames.includes(m[1])) fileNames.push(m[1]);
  }
  return { text: s.slice(0, cut).trimEnd(), hidden, fileNames };
}
