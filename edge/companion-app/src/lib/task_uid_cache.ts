/** P3.5.91 (6/23 鸿波): 早安 task → canonical taskUid 客户端 cache.
 *
 * 真因 (P3.5.90 audit): LLM advisor 6 字符 taskUid 跨 refresh 复用靠 prev cache
 * 注入 + LLM 推理稳定. LLM 可能给同 title 新 uid → session 关联失联 → 历史消息
 * UI 找不到 (db 里 session 还在, 但跟旧 uid 关联).
 *
 * 修法: client 按 normalized title 缓存 first-seen taskUid 当 canonical. 后续
 * advisor refresh 给同 title 不同 uid → ignore LLM 的, 用 cache 的 canonical uid
 * 做 sessionGetByTaskUid / sessionSetTaskUid.
 *
 * LLM 输出 taskUid 当 suggestion, client final decision.
 */

import { invoke } from "@tauri-apps/api/core";

/** task title normalize 规则:
 *  - 去 emoji (Unicode 范围 U+1F000-U+1FFFF, U+2600-U+27BF 等)
 *  - 去数字 (LLM 每次 refresh 数字可能变, e.g. "邮件 3 封" → "邮件 5 封")
 *  - 去标点 / 空格 / 制表符
 *  - lowercase 英文
 *  - 只保留中文 (CJK Unified Ideographs U+4E00-U+9FFF) + 英文字母
 *
 *  目标: "📧 邮件: 安管员考试退费 (3 封)" → "邮件安管员考试退费封"
 *        "📧 邮件: 安管员考试退费 (5 封)" → "邮件安管员考试退费封"   ← 同 normalized
 */
export function normalizeTaskTitle(title: string): string {
  if (!title) return "";
  return Array.from(title)
    .filter((c) => {
      const cp = c.codePointAt(0) ?? 0;
      // CJK 中文
      if (cp >= 0x4e00 && cp <= 0x9fff) return true;
      // 英文字母
      if ((cp >= 0x0041 && cp <= 0x005a) || (cp >= 0x0061 && cp <= 0x007a)) return true;
      // 数字 / 空格 / emoji / 标点 全砍
      return false;
    })
    .join("")
    .toLowerCase();
}

/** 按 normalized title 查 cache canonical taskUid.
 *  返 null = 第一次见这 title, caller 应该 putTaskUidByTitle 注册 first-seen uid.
 *  返 string = 用这个 canonical uid 而不是 LLM 新给的.
 */
export async function getTaskUidByTitle(
  normalizedTitle: string,
): Promise<string | null> {
  if (!normalizedTitle.trim()) return null;
  try {
    const result = await invoke<string | null>("task_uid_cache_get", {
      normalizedTitle,
    });
    return result;
  } catch (e) {
    console.warn("[task_uid_cache] get 失败 (silent fallback):", e);
    return null;
  }
}

/** 第一次见同 title task 时, 把 LLM 给的 taskUid 注册为 canonical.
 *  已存在 → no-op (保留第一次的, 不被 advisor refresh 覆盖).
 */
export async function putTaskUidByTitle(
  normalizedTitle: string,
  taskUid: string,
): Promise<void> {
  if (!normalizedTitle.trim() || !taskUid.trim()) return;
  try {
    await invoke<void>("task_uid_cache_put", {
      normalizedTitle,
      taskUid,
    });
  } catch (e) {
    console.warn("[task_uid_cache] put 失败 (silent, 不影响 UX):", e);
  }
}

/** 高层 helper: 给 task 算 canonical taskUid.
 *  - 第一次见 → 注册 + 返 LLM 给的 uid
 *  - 已见过 → 返 cache 中 canonical uid (可能跟 LLM 新给的不同)
 */
export async function resolveCanonicalTaskUid(
  title: string,
  llmTaskUid: string,
): Promise<string> {
  const normalized = normalizeTaskTitle(title);
  if (!normalized) {
    // title 全是 emoji/数字, normalize 后空, 退化到 LLM 给的 uid
    return llmTaskUid;
  }
  const cached = await getTaskUidByTitle(normalized);
  if (cached) {
    if (cached !== llmTaskUid) {
      console.info(
        `[task_uid_cache] canonical override: title="${title.slice(0, 30)}..." ` +
          `LLM 给=${llmTaskUid} → 用 cache=${cached}`,
      );
    }
    return cached;
  }
  // 第一次见 → 把 LLM 给的注册为 canonical
  await putTaskUidByTitle(normalized, llmTaskUid);
  return llmTaskUid;
}
