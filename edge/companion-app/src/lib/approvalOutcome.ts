/** 审批的两种"没声音"情况 (10/16 鸿波撞到): 批准失败和超时作废, 员工都看不见。
 *
 * 实况: 15:44 小鲶要批准跑一段脚本 → 没人应 → hermes 等满 300 秒作废 ("The user has NOT
 * consented") → 15:52 有人点了批准, 服务端回 409。员工全程不知道有过这件事, 只在几分钟后
 * 听小鲶说"没批"。两个原因:
 *   1. 点按钮失败 (409 已失效 / 404 这轮结束了) 只 console.warn, 横幅照样收起 —— 看着像批了
 *   2. 等批准只有卡片里那条横幅, 没有系统通知; 人在别的标签页 / 别的 App 就完全不知道
 *      (超时后横幅自己消失, 对话里也不留痕)
 *
 * 这里给三处共用的文案和判断; 弹系统通知的时机由各自组件决定 (卡片 / 工作台)。
 */

/** 按钮点下去失败时给员工看的一句话。409 / 404 是"晚了", 要说清楚得重来。 */
export function approvalErrorMessage(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err ?? "");
  if (/\b409\b/.test(text)) {
    return "这条批准已经超时失效了 (等太久自动拒绝), 让小鲶把刚才那步重做一次。";
  }
  if (/\b404\b/.test(text)) {
    return "这轮对话已经结束了, 这条批准没地方可送; 让小鲶重做一次。";
  }
  if (/\b401\b|\b403\b/.test(text)) {
    return "没权限送出这条批准, 重启鲶鱼 Companion 再试; 还不行就把日志发给 IT。";
  }
  return `批准没送出去: ${text.slice(0, 120)}`;
}

/** 等批准的时长换成人话: 1800 → "30 分钟", 3600 → "1 小时", 90 → "90 秒"。 */
export function approvalWindowText(seconds: number): string {
  const s = Math.max(Math.round(seconds || 0), 0);
  if (s > 0 && s % 3600 === 0) return `${s / 3600} 小时`;
  if (s > 0 && s % 60 === 0) return `${s / 60} 分钟`;
  return `${s} 秒`;
}

/** 等批准超时后留在对话里的一行, 不让它悄无声息地消失。 */
export function approvalTimeoutNote(seconds: number): string {
  return `刚才有一步要你点批准, 等了 ${approvalWindowText(seconds)}没人应, 已经自动拒绝了 —— `
    + "小鲶那一步没做, 后面的也停下了。要做的话点「批准并继续」, 它会重来一次。";
}

/** 系统通知的标题和正文。正文带上要执行什么, 免得员工还要切回来才知道是什么事。 */
export function approvalNotification(command: string, seconds: number): { title: string; body: string } {
  const one = command.replace(/\s+/g, " ").trim();
  return {
    title: "小鲶等你批准",
    body: `${one.length > 90 ? `${one.slice(0, 90)}…` : one || "有一步要你确认"} · ${approvalWindowText(seconds)}内没应会自动拒绝`,
  };
}

/** 「批准并继续」发给小鲶的话 (10/26)。超时那一下 hermes 告诉它「停下等用户」, 这里明确说现在批准了、接着做。 */
export function retryAfterTimeoutPrompt(command: string): string {
  const one = command.trim();
  return `刚才要我批准的那一步等超时了, 现在我批准: 请重做这一步, 然后接着把任务做完。${one ? `\n那一步是: ${one}` : ""}`;
}

/** 点了「批准并继续」之后多久内, 小鲶重做时弹出的**同一条**命令直接算批准 (只批这一次)。 */
export const RETRY_PREAPPROVAL_MS = 10 * 60 * 1000;

export interface Preapproval {
  command: string;
  until: number;
}

/** 新来的批准请求是不是员工刚点「批准并继续」批过的那条: 命令一字不差、还在时限内。 */
export function matchesPreapproval(pre: Preapproval | null, command: string, now: number): boolean {
  return !!pre && now <= pre.until && pre.command.trim() !== "" && pre.command.trim() === command.trim();
}

/** 界面在眼前 (窗口有焦点且没最小化) 就不弹系统通知 —— 横幅已经在那儿了, 再弹一条是噪音。 */
export function shouldNotifyApproval(doc: { hidden?: boolean; hasFocus?: () => boolean } | undefined): boolean {
  if (!doc) return true;
  if (doc.hidden) return true;
  return typeof doc.hasFocus === "function" ? !doc.hasFocus() : false;
}
