/** 钓鱼识别 · 员工标注 (11/07, 鸿波「这种识别错误, 能不能直接标注」) —— 纯函数。
 *
 * 按**发件地址**标 (不是按单封): 「不是钓鱼」→ 这个地址的来信不再报; 「这是钓鱼」→ 一律按钓鱼报。
 * 标注存在本机 (Rust phishing_marks.rs, ~/.catfish/phishing_marks.json), 也作为参考交给模型复审。
 * 界面上先按标注立刻改显示 (不等重扫), 后台随后按新标注重扫这个发件人的那几封。
 */
import type { PhishingMarks, PhishingScanResult } from "./tauri_services";

/** "信息安全中心 <FF_NIC@chinatelecom.cn>" → "ff_nic@chinatelecom.cn"; 认不出 = "" */
export function senderAddress(sender: string | null | undefined): string {
  const s = (sender ?? "").trim();
  const m = s.match(/<([^<>]+)>\s*$/);
  const addr = (m ? m[1] : s).trim().toLowerCase();
  return addr.includes("@") && !addr.includes(" ") ? addr : "";
}

/** 这个发件人标过什么 (没标 = null) */
export function markOf(marks: PhishingMarks | null, sender: string | null | undefined) {
  const a = senderAddress(sender);
  return (a && marks?.senders[a]?.verdict) || null;
}

/** 按标注改要显示的结果。标注撤销了、而结果还是标注时改出来的 (后台还没重扫完) → 先当没结果。 */
export function withMark(
  r: PhishingScanResult | null | undefined,
  sender: string | null | undefined,
  marks: PhishingMarks | null,
): PhishingScanResult | null {
  const v = markOf(marks, sender);
  if (!v) return r?.userMark ? null : r ?? null;
  const base: PhishingScanResult = r ?? { messageId: "", scannedAt: "", flags: [], highestSeverity: "none" };
  return v === "safe"
    ? { ...base, highestSeverity: "none", userMark: "safe", llmVerdict: "safe", llmReason: "你标过这个发件人「不是钓鱼」" }
    : { ...base, highestSeverity: "high", userMark: "phishing", llmVerdict: "phishing", llmReason: "你标过这个发件人「是钓鱼」" };
}

/** 列表里同一个发件人的那几封 (标的时候一起重扫) */
export function sameSender<T extends { sender?: string | null }>(list: T[], sender: string | null | undefined): T[] {
  const a = senderAddress(sender);
  return a ? list.filter((it) => senderAddress(it.sender) === a) : [];
}
