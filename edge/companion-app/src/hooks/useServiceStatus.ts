/** Read-only, non-overlapping polling. Failed probes must never kill working services. */
import { useEffect } from "react";
import { useServicesStore } from "../store/services";
import { gatewayStatus, hermesStatus, chromeStatus, localSearchStatus, toolBridgeStatus } from "../lib/tauri";
import type { ServiceId, ServiceStatus } from "../types/service";
import { config } from "../lib/env";

const FETCHERS: Record<ServiceId, () => Promise<ServiceStatus>> = {
  gateway: gatewayStatus, hermes: hermesStatus, chrome: chromeStatus,
  local_search: localSearchStatus, tool_bridge: toolBridgeStatus,
};

export function useServiceStatus(id: ServiceId) {
  const setStatus = useServicesStore((s) => s.setStatus);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const status = await FETCHERS[id]();
        if (!cancelled) setStatus(id, status);
      } catch (error) {
        if (!cancelled) setStatus(id, {
          running: false, healthy: false, pid: null, probeError: true,
          message: `检测失败，运行状态未知：${String(error)}`,
        });
      } finally {
        if (!cancelled) timer = setTimeout(tick, config.pollIntervalMs);
      }
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, setStatus]);
}
