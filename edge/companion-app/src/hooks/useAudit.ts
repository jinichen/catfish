/** 拉 audit summary — 30 秒轮询, 跟 useLearning 行为一致. */

import { useEffect, useState } from "react";
import { fetchAuditSummary } from "../lib/tauri";
import type { AuditSummary } from "../types/audit";

const POLL_MS = 30_000;

export function useAudit() {
  const [summary, setSummary] = useState<AuditSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchAuditSummary();
        if (!cancelled) {
          setSummary(s);
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

  return { summary, error };
}
