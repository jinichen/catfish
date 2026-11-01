/** 手机通道 (docs/MOBILE-CLIENT-DESIGN.md) —— Rust commands/mobile_bridge 的前端接口。 */
import { invoke } from "@tauri-apps/api/core";

export interface PairedPhone {
  device: string;
  name: string;
  publicKey: string;
  pairedAt: string;
  /** 最后一次连上来的时间 (老数据没有) */
  lastSeen?: string;
}

export interface PendingPair {
  device: string;
  name: string;
  /** 6 位验证码, 手机上显示的应该一样 */
  code: string;
  /** 这个人的手机已到单位上限: 允许时要选一台旧的替换 */
  overLimit: boolean;
  maxPhones: number | null;
}

export interface MobileStatus {
  enabled: boolean;
  connected: boolean;
  error: string | null;
  /** 单位设置的每人手机上限 (连上中转站后才知道) */
  maxPhones: number | null;
  relayUrl: string;
  mobileUrl: string;
  phones: PairedPhone[];
  pending: PendingPair[];
}

export const MOBILE_PAIR_REQUEST_EVENT = "mobile://pair-request";
export const MOBILE_PAIR_FAILED_EVENT = "mobile://pair-failed";
export const MOBILE_CHANGED_EVENT = "mobile://changed";

export const mobileStatus = () => invoke<MobileStatus>("mobile_status");
export const mobileSetEnabled = (enabled: boolean) => invoke<void>("mobile_set_enabled", { enabled });
/** replace: 到上限时选的那台旧手机 (先撤销它再允许新的) */
export const mobilePairDecide = (device: string, allow: boolean, replace?: string) =>
  invoke<void>("mobile_pair_decide", { device, allow, replace: replace ?? null });

const STALE_DAYS = 30;

/** 30 天没连过 (从没连过的按配对时间算) */
export function isStale(p: PairedPhone, now = Date.now()): boolean {
  const t = Date.parse(p.lastSeen ?? p.pairedAt);
  return Number.isFinite(t) && now - t > STALE_DAYS * 86400_000;
}

/** 替换候选: 最久没用的排前面 */
export function replaceOrder(phones: PairedPhone[]): PairedPhone[] {
  const at = (p: PairedPhone) => Date.parse(p.lastSeen ?? p.pairedAt) || 0;
  return [...phones].sort((a, b) => at(a) - at(b));
}
export const mobileRevoke = (device: string) => invoke<void>("mobile_revoke", { device });

/** 状态一句话 */
export function mobileStatusText(s: MobileStatus): string {
  if (!s.enabled) return "没开: 手机连不上这台电脑";
  if (s.connected) return "已连上中央中转站, 手机可以连这台电脑了";
  if (s.error) return `没连上: ${s.error} (会自动重试)`;
  return "正在连接中央中转站…";
}
