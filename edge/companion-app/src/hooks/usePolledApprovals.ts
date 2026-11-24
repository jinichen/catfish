/** 打开对话时问一次「有没有在等批准」; 等回复期间每 15 秒再问 (lib/polledApprovals.ts)。
 *  已经不在等的 (批了 / 作废了 / 别处批了) 从横幅里收掉。只读一张表, 不碰正在跑的那一轮。 */
import { useEffect } from "react";
import { fetchPendingApprovals, newDetails, rowKey } from "../lib/polledApprovals";
import { receiveApproval, useApprovalsStore } from "../store/approvals";

const POLL_MS = 15_000;
/** 轮询放进横幅的 key —— 只收掉这些; 流里来的由流自己管 */
const polled = new Set<string>();

export async function syncPolledApprovals(sessionId: string): Promise<void> {
  const rows = await fetchPendingApprovals();
  const s = useApprovalsStore.getState();
  const keys = new Set(s.items.map((i) => i.key));
  for (const d of newDetails(sessionId, rows, keys)) {
    polled.add(d.approval_session_key);
    receiveApproval(d);
  }
  const live = new Set(rows.map(rowKey).filter(Boolean) as string[]);
  const gone = s.items.filter((i) => polled.has(i.key) && i.sessionId === sessionId && !live.has(i.key));
  if (gone.length > 0) {
    gone.forEach((i) => polled.delete(i.key));
    useApprovalsStore.setState((st) => ({ ...st, items: st.items.filter((i) => !gone.includes(i)) }));
  }
}

export function usePolledApprovals(sessionId: string | null, waiting: boolean): void {
  useEffect(() => {
    if (!sessionId || typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return;
    let stopped = false;
    const tick = () => {
      if (!stopped) void syncPolledApprovals(sessionId).catch(() => { /* 服务没起来: 下次再问 */ });
    };
    tick();
    if (!waiting) return () => { stopped = true; };
    const t = window.setInterval(tick, POLL_MS);
    return () => { stopped = true; window.clearInterval(t); };
  }, [sessionId, waiting]);
}

/** 测试用 */
export function _resetPolled(): void {
  polled.clear();
}
