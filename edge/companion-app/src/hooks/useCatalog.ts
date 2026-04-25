/** 拉 gateway /v1/catalog —— 给仪表盘的可用模型列表用。
 *
 * 定期轮询：catalog 状态可能变化（VPN 断/Clash 起停影响 is_reachable），
 * 但变化频率比 service status 低，间隔取 15 秒（service status 是 3 秒）。
 */

import { useEffect, useState } from "react";
import { fetchCatalog } from "../lib/tauri";
import type { CatalogResponse } from "../types/catalog";

const POLL_INTERVAL_MS = 15_000;

export function useCatalog() {
  const [catalog, setCatalog] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const c = await fetchCatalog();
        if (!cancelled) {
          setCatalog(c);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    };
    void tick(); // 立刻 fetch 一次
    const t = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  return { catalog, error };
}
