/** 拉"鲶鱼今天学到的"统计 —— 30 秒轮询。 */

import { useEffect, useState } from "react";
import { fetchTodayLearningStats } from "../lib/tauri";
import type { TodayLearningStats } from "../types/learning";

const POLL_MS = 30_000;

export function useLearning() {
  const [stats, setStats] = useState<TodayLearningStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchTodayLearningStats();
        if (!cancelled) {
          setStats(s);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    void tick();
    const t = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  return { stats, error };
}
