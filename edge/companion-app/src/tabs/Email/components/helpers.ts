/** EmailTab pure helpers — 抽自 EmailTab.tsx (5/20 拆分).
 *
 * 跟 BriefingCard helpers 一样, 无 React 依赖, 单测友好.
 */

export function _extractSenderName(sender: string): string {
  if (!sender) return "(未知)";
  const m = sender.match(/^([^<]+?)\s*<.+>$/);
  if (m) return m[1].trim().replace(/^"|"$/g, "");
  if (sender.includes("@")) return sender.split("@")[0];
  return sender;
}

/** "张三 <zhang@x.com>" → "zhang@x.com"; "bob@example.com" → "bob@example.com" */
export function _replyAddress(sender: string): string {
  if (!sender) return "";
  const m = sender.match(/<([^>]+)>/);
  if (m) return m[1].trim();
  return sender.trim();
}

export function _formatShortDate(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const dayDiff = Math.floor((now.getTime() - d.getTime()) / (24 * 3600 * 1000));
    if (dayDiff === 0) {
      return d.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    if (dayDiff === 1) return "昨天";
    if (dayDiff < 7) return `${dayDiff}天前`;
    return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch {
    return "";
  }
}
