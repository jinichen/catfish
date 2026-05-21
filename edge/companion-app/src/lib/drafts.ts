/** BL-ADVISOR-DRAFTS (5/21 Phase 7 第 2 步): 草稿存储 — TS wrapper.
 *
 * 设计稿 §6.1: ~/.catfish/outputs/<YYYY-MM-DD>/<task-type>-<key>.md
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

export interface DraftRef {
  filename: string;
  absPath: string;
  modifiedAt: string;  // ISO-8601
  bytes: number;
}

/** LLM tool 写草稿. 自动 outputs/<today>/<filename>. 返绝对路径. */
export const draftSave = (filename: string, content: string) =>
  rawInvoke<string>("draft_save", { filename, content });

/** 读草稿全文 (DraftPreview UI 用). */
export const draftRead = (date: string, filename: string) =>
  rawInvoke<string>("draft_read", { date, filename });

/** 列今天所有草稿 (mtime 倒序). */
export const draftListToday = () =>
  rawInvoke<DraftRef[]>("draft_list_today");

/** 调系统默认编辑器打开 (员工自己改/复制后发). */
export const draftOpenInEditor = (absPath: string) =>
  rawInvoke<void>("draft_open_in_editor", { absPath });
