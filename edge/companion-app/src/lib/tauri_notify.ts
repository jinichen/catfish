/** 「提醒同步到微信」(10/12) —— Rust commands/notify_prefs.rs 的前端封装。 */
import { invoke } from "@tauri-apps/api/core";

export interface PushStatus {
  last_at?: number;
  ok?: boolean;
  error?: string | null;
}

export interface NotifyPrefs {
  wechat_reminders: boolean;
  /** hermes .env 里机器人凭据 + 绑定的微信都有 */
  wechat_bound: boolean;
  last: PushStatus | null;
}

export const notifyPrefsGet = () => invoke<NotifyPrefs>("notify_prefs_get");
export const notifyPrefsSet = (wechatReminders: boolean) =>
  invoke<void>("notify_prefs_set", { wechatReminders });
export const notifyWechatTest = () => invoke<PushStatus & { ok: boolean }>("notify_wechat_test");
