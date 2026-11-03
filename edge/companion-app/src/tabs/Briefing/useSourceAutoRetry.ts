import { useEffect, useRef } from "react";

/** 自动重试间隔与次数：冷启动时本地服务一般 1 分钟内就绪。 */
export const SOURCE_RETRY_DELAY_MS = 20_000;
export const SOURCE_RETRY_MAX = 3;

interface SourceOk {
  ok: boolean;
}

/**
 * 11/3: 早安「没有素材」是因为数据源失败（不是真没数据）时，过一会儿自动再拉。
 *
 * 鸿波 11/3：升级后 / 退出重进，早安页一打开就拉数据，tool-bridge 还没起好、系统日历还慢，
 * 待办和日历各报一次失败，失败卡一直挂到明天 08:40 或手点刷新。现在失败卡出现后每 20 秒
 * 重拉一次，最多 3 次；拿到结果或员工手点刷新后重新计数。三个来源都正常、只是确实没数据时不重试。
 */
export function useSourceAutoRetry(
  phase: string,
  statuses: { emails: SourceOk; events: SourceOk; todos: SourceOk } | null,
  refreshKey: number,
  reload: () => void,
): void {
  const attempts = useRef(0);

  useEffect(() => {
    attempts.current = 0;
  }, [refreshKey]);

  useEffect(() => {
    if (phase === "done" || phase === "cache_hit") {
      attempts.current = 0;
      return;
    }
    if (phase !== "no_data" || !statuses) return;
    const anyFailed = !statuses.emails.ok || !statuses.events.ok || !statuses.todos.ok;
    if (!anyFailed || attempts.current >= SOURCE_RETRY_MAX) return;
    const timer = window.setTimeout(() => {
      attempts.current += 1;
      console.info("[advisor] 数据源失败, 自动重拉", { attempt: attempts.current });
      reload();
    }, SOURCE_RETRY_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [phase, statuses, reload]);
}
