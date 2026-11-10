/** P3.5.1 (6/15 鸿波 Dream Engine): TS wrapper.
 *
 * 设计 (方案 D):
 *   - 复用 hermes catfish-memory plugin 的 distill 算法 (Rust spawn dream_cli)
 *   - Model 用 companion picker 当前选的 (caller 从 useChatStore.model 取)
 *   - 跑完写共享 cooldown state, plugin auto 24h 内自动 skip — 零冲突
 *   - 进度通过 Tauri event 'dream:progress' (每行 stdout JSON, Rust 透传)
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// ── 状态查询 (UI mount 时调) ─────────────────────────────────────

export interface DreamStatus {
  /** 上次蒸馏 unix epoch 秒. null = 从未跑过 */
  lastRunTs: number | null;
  /** 上次蒸馏 ISO 字符串 (state 文件自带) */
  lastRunIso: string | null;
  /** distilled_facts.md 字节数. 0 = 文件不存在 */
  distilledBytes: number;
  /** employee_journal.md 字节数 (输入). 0 = 文件不存在 */
  journalBytes: number;
  /** 11/10 保险: 日志比上次蒸馏时短了一半以上 (多半被清空), 蒸馏已停. null = 正常 */
  journalShrunk: JournalShrunk | null;
  /** 11/10 保险: 新蒸出来的长期记忆比旧的小六成以上, 没覆盖. null = 正常 */
  distilledRefused: DistilledRefused | null;
}

/** 字段名跟 catfish_memory_distill_guard.py 写进 state 的一致 (snake_case 原样透传) */
export interface JournalShrunk {
  at?: string;
  was_chars?: number;
  now_chars?: number;
  /** 最近一份日志快照的完整路径 (可能为 null: 一份都没有) */
  snapshot?: string | null;
}

export interface DistilledRefused {
  at?: string;
  old_bytes?: number;
  new_bytes?: number;
}

export async function dreamStatus(): Promise<DreamStatus> {
  return invoke<DreamStatus>("dream_distill_status");
}

// ── 蒸馏跑 + event 监听 ─────────────────────────────────────────

/** dream_cli.py stdout 协议 (跟 Rust dream.rs / Python dream_cli.py 对齐). */
export type DreamEvent =
  | { event: "start"; total: number }
  | { event: "chunk"; done: number; total: number }
  | {
      event: "done";
      ok: boolean;
      reason?: string;
      chunks?: number;
      bytes?: number;
      model?: string;
      took_seconds?: number;
    }
  | { event: "error"; msg: string }
  | { event: "stderr"; msg: string };

export interface DreamRunOutput {
  spawnOk: boolean;
  command: string;
  exitCode: number | null;
  error: string | null;
}

/** Tauri event 透传整行 JSON 字符串 — TS 这边 parse. Rust 不解析协议字段
 *  (减少 Rust schema 维护负担, dream_cli.py 改协议 TS 同步就行). */
type DreamPayload = string;

/** 监听 dream:progress 事件. 返 unlisten — caller 在 cleanup 时调.
 *
 *  用法:
 *    const un = await listenDreamProgress((evt) => { ... });
 *    return un;  // useEffect cleanup
 */
export async function listenDreamProgress(
  onEvent: (evt: DreamEvent) => void,
): Promise<UnlistenFn> {
  return listen<DreamPayload>("dream:progress", (msg) => {
    const line = msg.payload;
    if (typeof line !== "string" || !line.trim()) return;
    try {
      const evt = JSON.parse(line) as DreamEvent;
      onEvent(evt);
    } catch (e) {
      console.warn("[dream] 解析 event 失败 (line):", line, e);
    }
  });
}

/** 启动 Dream Engine 蒸馏. await 直到 CLI 退出.
 *
 *  注: 真实进度走 dream:progress event, 不是 invoke 返回值.
 *  invoke 返回 spawn 状态 + exit code (UI 知道 "成功跑完 / 没跑起来").
 *
 *  使用 pattern:
 *    1. await listenDreamProgress(cb) 拿 unlisten
 *    2. dreamRun(model) — 这个 await 等 CLI 退出
 *    3. unlisten()
 */
export async function dreamRun(model: string): Promise<DreamRunOutput> {
  return invoke<DreamRunOutput>("dream_distill_run", { model });
}

// ── 蒸馏结果原因 → 人话 ─────────────────────────────────────────

/** dream_cli done 事件的 reason (catfish_memory_distill_run / catfish_memory_distill) 翻成员工看得懂的话. */
export function reasonText(reason: string | undefined): string {
  switch (reason) {
    case "cooldown": return "24 小时内刚整理过, 这次跳过";
    case "model_empty": return "没选模型, 去聊天页选一个";
    case "empty_journal": return "还没有工作日志, 先跟小鲶聊几次";
    case "no_new_journal": return "上次整理后没有新日志, 不用整理";
    case "reconcile_only": return "没有新日志, 只把「当前状态」重新核对了一遍";
    case "llm_fail": return "模型调用失败, 长期记忆没动, 稍后再试";
    case "journal_shrunk": return "工作日志比上次少了一大半 (可能被清空了), 这次没整理, 长期记忆没动";
    case "distilled_shrunk": return "这次整理出来的比原来少太多, 没覆盖, 原来的长期记忆还在";
    case undefined: case "": return "未知原因";
    default: return reason;
  }
}

// ── 辅助: 字节数 / 时间格式化 ───────────────────────────────────

/** 字节数显示: 1023 B / 4.5 KB / 1.2 MB */
export function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

/** 距上次蒸馏的时间描述. lastRunTs 是 unix epoch 秒 */
export function formatAgo(lastRunTs: number | null): string {
  if (lastRunTs === null || lastRunTs <= 0) return "未蒸馏过";
  const ageSec = Date.now() / 1000 - lastRunTs;
  if (ageSec < 60) return "刚刚";
  const ageMin = ageSec / 60;
  if (ageMin < 60) return `${ageMin.toFixed(0)} 分钟前`;
  const ageHr = ageMin / 60;
  if (ageHr < 24) return `${ageHr.toFixed(1)} 小时前`;
  return `${(ageHr / 24).toFixed(1)} 天前`;
}
