/** 周期轮询某个服务的状态，写入 services store。
 *
 * P3.5.125 (6/26 鸿波 catch "catfish 对 hermes/chrome hang 无监控"):
 * 加 hang watchdog — 连续 3 次 running=true 但 healthy=false → 调 KILLERS
 * 自动 kill 触发 launchd 拉 (hermes) / chromeLaunch 拉 (chrome). :
 * 鸿波 GLM 测试时 hermes stream 卡死: 真:** : : : :
 * 3 次 unhealthy ≈ 9s (轮询 3s × 3), : : : : : :
 * : 真:** : :: : : : : : : : : : : : : :
 */

import { useEffect, useRef } from "react";
import { useServicesStore } from "../store/services";
import {
  gatewayStatus,
  hermesStatus,
  hermesKill,
  chromeStatus,
  chromeLaunch,
  chromeKill,
  localSearchStatus,
  toolBridgeStatus,
} from "../lib/tauri";
import type { ServiceId, ServiceStatus } from "../types/service";
import { config } from "../lib/env";

const FETCHERS: Record<ServiceId, () => Promise<ServiceStatus>> = {
  gateway: gatewayStatus,
  hermes: hermesStatus,
  chrome: chromeStatus,
  local_search: localSearchStatus,
  tool_bridge: toolBridgeStatus,
};

// P3.5.125 : : kill+restart : 只 hermes / chrome 自动重启.
// gateway / local_search / tool_bridge 不自动 — : : : : : : :
// (gateway 是 Companion spawn 的, 真:** : : : : ::)
const KILLERS: Partial<Record<ServiceId, () => Promise<void>>> = {
  hermes: hermesKill, // kill -9 → launchd KeepAlive 自动拉
  chrome: async () => {
    await chromeKill().catch(() => {});
    await chromeLaunch().catch(() => {});
  },
};

const UNHEALTHY_THRESHOLD = 3; // 连续 3 次 unhealthy 触发 auto-restart

export function useServiceStatus(id: ServiceId) {
  const setStatus = useServicesStore((s) => s.setStatus);
  const unhealthyCount = useRef(0);
  const restartInProgress = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await FETCHERS[id]();
        if (cancelled) return;
        setStatus(id, s);

        // P3.5.125 watchdog: running=true 但 healthy=false → 累计 unhealthy 计数.
        // : : 真真真: : : : : :真真真:** : : : : : : :
        if (s.running && !s.healthy) {
          unhealthyCount.current += 1;
          console.warn(
            `[useServiceStatus] ${id} unhealthy ${unhealthyCount.current}/${UNHEALTHY_THRESHOLD}: ${s.message ?? "(no msg)"}`,
          );
          if (
            unhealthyCount.current >= UNHEALTHY_THRESHOLD &&
            !restartInProgress.current &&
            KILLERS[id]
          ) {
            restartInProgress.current = true;
            console.warn(
              `[useServiceStatus] ${id} hang 检测命中, 自动重启...`,
            );
            try {
              await KILLERS[id]!();
              console.info(`[useServiceStatus] ${id} kill 已发, 等待重启`);
            } catch (e) {
              console.error(`[useServiceStatus] ${id} 自动重启失败:`, e);
            } finally {
              // : 真:** 等 10s 让 launchd / chromeLaunch 拉**, :
              // : : : : : : : : : : : : : : : : : : :
              setTimeout(() => {
                restartInProgress.current = false;
                unhealthyCount.current = 0;
              }, 10_000);
            }
          }
        } else if (s.healthy) {
          // : : : : : : : : : : : : : : : : : : :
          if (unhealthyCount.current > 0) {
            console.info(`[useServiceStatus] ${id} 已恢复 healthy, 重置 watchdog`);
            unhealthyCount.current = 0;
          }
        }
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
