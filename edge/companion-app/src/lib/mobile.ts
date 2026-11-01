/** 手机通道 (docs/MOBILE-CLIENT-DESIGN.md) —— Rust commands/mobile_bridge 的前端接口。 */
import { invoke } from "@tauri-apps/api/core";

export interface PairedPhone {
  device: string;
  name: string;
  publicKey: string;
  pairedAt: string;
}

export interface PendingPair {
  device: string;
  name: string;
  /** 6 位验证码, 手机上显示的应该一样 */
  code: string;
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
export const mobilePairDecide = (device: string, allow: boolean) =>
  invoke<void>("mobile_pair_decide", { device, allow });
export const mobileRevoke = (device: string) => invoke<void>("mobile_revoke", { device });

/** 状态一句话 */
export function mobileStatusText(s: MobileStatus): string {
  if (!s.enabled) return "没开: 手机连不上这台电脑";
  if (s.connected) return "已连上中央中转站, 手机可以连这台电脑了";
  if (s.error) return `没连上: ${s.error} (会自动重试)`;
  return "正在连接中央中转站…";
}
