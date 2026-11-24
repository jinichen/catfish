/** 11/24 鸿波「是不是又卡住了」: 这个对话里上一轮还没跑完时再发消息, 本机服务让它排队, 流上一个字都不来;
 *  界面却一直写「正在等待模型首个回复 · 已 118 秒」, 看着像卡死, 员工就接着发「继续」, 越排越多。
 *  等首个回复期间每 4 秒看一眼这个对话在服务那边的队列 (src-tauri sessions_queue.rs), 前面有人就改口。 */
import { invoke } from "@tauri-apps/api/core";

export interface QueueState {
  started: number;
  queued: number;
}

/** 这条消息前面还有几轮 (自己是最后进队的那条) */
export function turnsAhead(q: QueueState): number {
  return q.queued > 0 ? q.started + q.queued - 1 : 0;
}

export function queueText(ahead: number): string {
  return ahead === 1
    ? "上一条还在处理, 这条排队中 (不用再发「继续」)…"
    : `前面还有 ${ahead} 条在处理, 这条排队中 (不用再发「继续」)…`;
}

export const WAITING_TEXT = "正在等待模型首个回复…";
const POLL_MS = 4000;

/** 返回停表的函数; 首个字到了 / 结束 / 出错时调。 */
export function watchQueue(sessionId: string | null | undefined, setStatus: (s: string | null) => void): () => void {
  if (!sessionId || typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return () => {};
  let stopped = false;
  let shown = false;
  const tick = async () => {
    try {
      const ahead = turnsAhead(await invoke<QueueState>("session_queue_state", { sessionId }));
      if (stopped) return;
      if (ahead > 0) {
        shown = true;
        setStatus(queueText(ahead));
      } else if (shown) {
        shown = false;
        setStatus(WAITING_TEXT); // 轮到自己了
      }
    } catch {
      /* 读不到就保持原来那句 */
    }
  };
  const first = window.setTimeout(() => void tick(), 1500);
  const t = window.setInterval(() => void tick(), POLL_MS);
  return () => {
    stopped = true;
    window.clearTimeout(first);
    window.clearInterval(t);
  };
}
