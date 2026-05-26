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

/** BL-X (5/26): 跨日期扫 outputs/, 过去 N 小时改的文件, mtime 倒序.
 *
 * 给 chat timeout toast 用 — 5/26 audit 砍掉 gateway recent_outputs.list_recent,
 * Companion (员工 mac 本地) 自己扫. timeout 时展示 "鲶鱼写过这些文件" 让员工
 * 不至于以为白干.
 */
export const recentOutputsList = (hours: number) =>
  rawInvoke<DraftRef[]>("recent_outputs_list", { hours });

/** BL-X (5/26): 拼一段给 chat error message 用的"过去 24h 鲶鱼已写文件" 脚注.
 *
 * 失败 / 空 → 返空字符串 (caller 不挂任何内容). 失败不抛 — 这是兜底信息, 不该
 * 因脚注失败把主错误隐藏掉.
 *
 * 跟老 gateway recent_outputs.format_recent_block 输出格式一致:
 *   📁 过去 24h 鲶鱼已写文件 (员工本机, 中央 0 字节):
 *     - <filename> (<相对时间>)
 *     - ...
 */
export async function formatRecentOutputsFootnote(hours: number): Promise<string> {
  try {
    const list = await recentOutputsList(hours);
    if (!list.length) return "";
    const now = Date.now();
    const lines = list.slice(0, 10).map((d) => {
      const ageMin = Math.max(
        0,
        Math.round((now - new Date(d.modifiedAt).getTime()) / 60000),
      );
      const rel =
        ageMin < 60
          ? `${ageMin} 分钟前`
          : ageMin < 1440
          ? `${Math.round(ageMin / 60)} 小时前`
          : `${Math.round(ageMin / 1440)} 天前`;
      return `  - ${d.filename} (${rel})`;
    });
    const more = list.length > 10 ? `\n  ... 还有 ${list.length - 10} 个` : "";
    return (
      `\n\n📁 过去 ${hours}h 鲶鱼已写文件 (员工本机, 中央 0 字节):\n` +
      lines.join("\n") +
      more
    );
  } catch {
    return "";
  }
}
