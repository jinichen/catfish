/**
 * 邮件里的外部图片要不要自动显示 (11/14 鸿波「图片缺省打开, 不要每次都去点」)。
 *
 * 9/18 起外链图一律先拦 (跟踪像素: 一打开发件人就知道你看了), 每封要点「显示图片」。实际用下来几乎每封
 * 营销 / 通知邮件都要点一次, 员工嫌烦。现在默认自动显示, 但两种情况仍然先拦:
 *   · 这封疑似钓鱼 (扫描报中 / 高, 或员工标过这个发件人是钓鱼) —— 钓鱼信的像素是在确认「这个地址有人看」;
 *   · 这封的钓鱼扫描结果还没回来 —— 先等它, 不然图已经加载、像素已经发出去了才知道是钓鱼。
 * 想回到「每封自己点」的, 在「邮件 → 邮箱设置」里关掉。只存这台电脑 (localStorage); 读不到就按默认 (自动显示)。
 */
import type { PhishingScanResult } from "./tauri";

const KEY = "catfish.email.autoLoadImages";
const listeners = new Set<() => void>();

/** 读过 / 设过一次就记在这里: 存储写不进时 (隐私模式、存储坏了) 这次运行里照样按员工刚选的来 */
let cached: boolean | null = null;

export function getAutoLoadImages(): boolean {
  if (cached !== null) return cached;
  try {
    cached = localStorage.getItem(KEY) !== "false";
  } catch {
    cached = true;
  }
  return cached;
}

export function setAutoLoadImages(on: boolean): void {
  cached = on;
  try {
    localStorage.setItem(KEY, on ? "true" : "false");
  } catch {
    /* 存不下就这次用 */
  }
  listeners.forEach((f) => f());
}

export function onAutoLoadImagesChange(f: () => void): () => void {
  listeners.add(f);
  return () => listeners.delete(f);
}

/** 疑似钓鱼: 扫描报中 / 高, 或员工标过是钓鱼; 员工标过「不是钓鱼」的不算 */
export function looksPhishy(p: PhishingScanResult | null): boolean {
  if (!p || p.userMark === "safe") return false;
  return p.userMark === "phishing" || p.highestSeverity === "high" || p.highestSeverity === "medium";
}

/** 这封打开时外部图片自动显示吗。phishingReady: 这封的钓鱼扫描结果 (含员工标注) 拿到了 */
export function autoShowRemote(auto: boolean, phishingReady: boolean, phishing: PhishingScanResult | null): boolean {
  return auto && phishingReady && !looksPhishy(phishing);
}
