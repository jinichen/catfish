/**
 * 审批卡给哪些按钮 (10/15 鸿波「只要点过始终允许, 下一次就不要再审批了, 除非那种删除等
 * 严重破坏内容的指令」)。
 *
 * - 「始终允许」真生效了 (插件 P59): 点过一次, 以后同类脚本不再问。
 * - 删除 / 结束进程 / 格式化 / 改安全设置这类, 插件把审批说明换成以 EVERY_TIME_MARK 开头的
 *   中文 (plugin_approval_always.py); 这种每次都要问, 「始终允许」点了也不记 —— 卡片就不给这个按钮,
 *   把原因直接写出来。
 * - 「本会话允许」去掉了: hermes 拿这一条消息的 id 当审批会话, 员工下一句话就失效, 跟字面意思不符。
 */

/** 跟 edge/hermes-plugins/catfish-xcatfish-user/plugin_approval_always.py EVERY_TIME_MARK 同一串 */
export const EVERY_TIME_MARK = "⚠️ 每次都要确认";

export function asksEveryTime(description: string | undefined | null): boolean {
  return (description ?? "").startsWith(EVERY_TIME_MARK);
}

/** 「这段代码会删除文件。删除这类操作每次都要你同意…」—— 去掉标记前缀给卡片显示 */
export function everyTimeReason(description: string | undefined | null): string {
  const d = description ?? "";
  if (!asksEveryTime(d)) return "";
  const i = d.indexOf(": ");
  return i >= 0 ? d.slice(i + 2) : d.slice(EVERY_TIME_MARK.length);
}
