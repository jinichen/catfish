/** BL-RECMODE-DASHBOARD-UI (#75, 5/25): "我的录屏" Tauri 命令的 TS wrapper.
 *
 * 跟 src-tauri/src/commands/recordings.rs 字段对齐.
 * 配套 PrivacyCard 一起兑现"本机数据员工主权"卖点 — 录屏 100% 本机, catfish
 * 不会自动删, 你想看在 Finder 看, 你想删点按钮删.
 */

import { invoke } from "@tauri-apps/api/core";

/** 跟 Rust RecordingMeta + Python list_recordings_with_meta 字段同源.
 * 改一处三处都要改 (Rust / Python / 本 TS). */
export interface RecordingMeta {
  session_id: string;
  /** unix timestamp (秒, float). 0 = 没 meta.json 也无 mtime */
  started_at: number;
  size_bytes: number;
  /** 老 _keep_forever flag — 5/25 BL-RECMODE-NO-AUTO-DELETE 后是 cosmetic
   * (新录屏默认就不删), 但兼容历史勾过的 session */
  kept_forever: boolean;
  /** "namespace/name" 字符串列表, e.g. ["productivity/weekly-report"] */
  skill_drafts: string[];
  /** 绝对路径, 给 Finder show in 用 */
  path: string;
}

export async function listRecordings(): Promise<RecordingMeta[]> {
  return invoke<RecordingMeta[]>("recordings_list");
}

/** 在 Finder 高亮选中该路径 (macOS only). 路径必须在 recordings root 范围内. */
export async function showInFinder(path: string): Promise<void> {
  return invoke("recordings_show_in_finder", { path });
}

/** 删 recordings/<session_id>/. 返释放的字节数. UI 必须先二次确认. */
export async function deleteRecording(sessionId: string): Promise<number> {
  return invoke<number>("recordings_delete", { sessionId });
}
