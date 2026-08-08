/**
 * 等待期间订阅 hermes 的 agent 进度快照 (8/9 加, 配 P44)。
 *
 * # 为什么单独一个 hook
 *
 * 轮询的**生命周期**是 React 的事 (挂载起、卸载停、enabled 变了要跟着切),
 * 拉数据的**协议**是 `lib/agentActivity.ts` 的事。混在展示组件里, 下次谁想
 * 在别处也显示进度就得把 useEffect 抄一遍。
 *
 * # 不做的事
 *
 * 不判超时、不重试、不做兜底文案 —— 见 `lib/agentActivity.ts` 顶部关于
 * hermes observation-only 契约的说明。这里只负责"把最新一份快照交给调用方"。
 */
import { useEffect, useState } from "react";

import {
  type AgentTurnActivity,
  POLL_INTERVAL_MS,
  pickPrimaryTurn,
  startActivityPolling,
} from "../lib/agentActivity";

export interface UseAgentActivity {
  /** 最该显示的那条 turn; 没有正在跑的 turn 或探不到时为 null。 */
  turn: AgentTurnActivity | null;
  /** 探针够不够得着 hermes。false 时 UI 该退回原来的盲等展示。 */
  reachable: boolean;
}

/**
 * @param enabled 只在真正等待时轮询 —— 不等的时候没必要每 3 秒打一次。
 */
export function useAgentActivity(enabled: boolean): UseAgentActivity {
  const [turn, setTurn] = useState<AgentTurnActivity | null>(null);
  const [reachable, setReachable] = useState(true);

  useEffect(() => {
    if (!enabled) {
      setTurn(null);
      return;
    }
    const stop = startActivityPolling((result) => {
      // 探不到 ≠ 没有正在跑的 turn:
      //   · 探不到 → reachable=false, UI 退回盲等 (别显示假的"没进度")
      //   · 探到了但 turns 为空 → reachable=true, turn=null (agent 还没起来)
      const probeFailed =
        result.reason === "probe_unreachable" ||
        result.reason === "bad_shape" ||
        (result.reason ?? "").startsWith("http_");
      setReachable(!probeFailed);
      setTurn(probeFailed ? null : pickPrimaryTurn(result.turns));
    }, POLL_INTERVAL_MS);
    return stop;
  }, [enabled]);

  return { turn, reachable };
}
