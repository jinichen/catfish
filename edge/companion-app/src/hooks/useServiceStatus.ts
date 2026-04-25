/** 周期轮询某个服务的状态，写入 services store。 */

import { useEffect } from "react";
import { useServicesStore } from "../store/services";
import {
  gatewayStatus,
  chromeStatus,
  localSearchStatus,
  toolBridgeStatus,
} from "../lib/tauri";
import type { ServiceId, ServiceStatus } from "../types/service";
import { config } from "../lib/env";

const FETCHERS: Record<ServiceId, () => Promise<ServiceStatus>> = {
  gateway: gatewayStatus,
  chrome: chromeStatus,
  local_search: localSearchStatus,
  tool_bridge: toolBridgeStatus,
};

export function useServiceStatus(id: ServiceId) {
  const setStatus = useServicesStore((s) => s.setStatus);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await FETCHERS[id]();
        if (!cancelled) setStatus(id, s);
      } catch {
        // TODO: 未实现的命令会 throw，先静默；MVP 时改为 setStatus(id, errStatus)
      }
    };
    void tick();
    const t = setInterval(tick, config.pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [id, setStatus]);
}
