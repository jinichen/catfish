/** 用户气泡里只显示员工自己写的话 (10/13 鸿波「显示还有这些内容会干扰视觉」「为什么还是有」)。
 *
 * # 为什么 role=user 的 content 里会有不是员工写的东西
 *
 * hermes 本来分得很清楚: `content` 给人看, `api_content` 给模型看, 每轮注进去的东西
 * (召回的记忆、技能清单变更、换界面通知、插件上线) 都放 api_content, content 保持干净。
 * **但这条只对纯文字消息成立** —— 消息带了图片 / 附件时 content 是 parts 数组, 没有字符串
 * sidecar 可用, hermes 就把注入物当成一个 text part 接在 content 后面 (upstream #71998),
 * 存进 state.db 时 parts 拍平成一段文字, 于是全进了气泡。
 *
 * 所以这事只在**带图 / 带附件**的消息上出现, 而且每次注入物不一样: 10/13 是附件材料和
 * [System note:], 10/16 上午是 [System: 技能清单变了], 10/16 下午是 <memory-context>。
 * 按名字一个个认, 下次换一种注入物就又漏 (鸿波: "出现一个问题处理一个问题, 没有通用方案?")。
 *
 * # 通用判据: 按位置和形状, 不按名字
 *
 * 注入物有共同的形状, 跟它叫什么无关:
 *   · **一律接在员工的话后面** (hermes 和 chatWire 都是 append), 员工写的永远在最前面
 *   · **整块**, 块之间空行分隔
 *   · 块的开头是机器标记: `<tag>` 围栏 / `[Word …]` 方括号 / `=== 附件: …` 标题行
 * 于是从**末尾**往前扔掉长这样的块, 剩下的就是员工写的 —— 以后再多几种注入物也不用改这里。
 * 员工自己打的「[System: boom] 是什么意思」后面还有话, 不是尾块, 不会被扔。
 *
 * 另外两类单独处理 (位置不同, 不是尾块):
 *   · 整条都是系统消息 (压缩摘要 / 定时任务 / 早安顾问材料…) → 显示成一行灰色说明
 *   · 粘在员工话**前面**的 [System note: …] → 去掉前缀
 *
 * 这里只管**显示**: msg.content 本身不动 —— chatRequest 每次把整段历史 toWire 发给模型,
 * 改了模型就看不到附件 / 记忆了。
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
  // 整条只有每轮通知 (员工这轮没写字, 只发了附件 / 图片)
  [/^\[System: /, "系统提示"],
];
// 粘在员工话前面的系统说明 (到 "]" + 空行为止)
const SYSTEM_PREFIX = /^\[System note: [\s\S]*?\]\s*\n\s*\n/;

// 图片路径行 —— 路径对员工没用, 图片本身另有显示
const IMAGE_PATH_LINE = /^\[Image attached at: [^\]\n]*\]\s*$/gm;

/** 注入块的开头长相 (见文件头「通用判据」):
 *   · XML 围栏独占一行: `<memory-context>`
 *   · 方括号英文标记行: `[System: …]` `[IMPORTANT: …]` —— 要求大写开头 + 英文词 + 冒号/收尾方括号,
 *     员工写的「[图片] 这是什么」「[1] 先报价」不算; 行中间出现的 (「这行日志 [System: boom] 什么意思」) 也不算
 */
const INJECTION_HEAD = /^(?:<[A-Za-z][\w-]*>\s*$|\[[A-Z][A-Za-z][A-Za-z ]{0,30}[:\]])/;

/** 注入块一开始, 后面到结尾就全是注入物 —— 从第一个注入块开头切掉, 剩下的是员工写的。
 *
 *  10/16 第二次踩: 原来按**结尾**找配对的 `</tag>` / `]`。但 sessions.rs::truncate 把超过
 *  4000 字的消息截断再给界面 (这条 9873 字), 收尾标签根本没进来, 配不上对就什么都没切。
 *  按开头切不依赖结尾, 截断与否都成立。 */
function stripInjectedTail(text: string): string {
  const lines = text.split("\n");
  const cut = lines.findIndex((line) => INJECTION_HEAD.test(line.trimStart()));
  return cut < 0 ? text : lines.slice(0, cut).join("\n");
}

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
  // 附件材料要留着 (hidden + 文件名标签), 所以先切它, 再在前半段里扔尾部的机器块
  let cut = -1;
  for (const re of [ATTACH_HEADER, MANY_FILES]) {
    const m = re.exec(s);
    if (m && (cut < 0 || m.index < cut)) cut = m.index;
  }
  const human = stripInjectedTail(cut < 0 ? s : s.slice(0, cut));
  const visible = human.replace(/\n{3,}/g, "\n\n").trim();
  if (cut < 0) {
    // 只剩前缀说明 / 注入物、没有员工的话 → 当系统消息显示
    if (!visible && raw.trim()) return { text: "", hidden: "", fileNames: [], system: "系统提示" };
    return { text: visible, hidden: "", fileNames: [], system: null };
  }
  const hidden = s.slice(cut);
  const fileNames: string[] = [];
  for (const m of hidden.matchAll(NAME_RE)) {
    if (!fileNames.includes(m[1])) fileNames.push(m[1]);
  }
  return { text: visible, hidden, fileNames, system: null };
}
