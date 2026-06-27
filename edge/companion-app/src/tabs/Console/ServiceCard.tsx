/** 单服务卡片 —— 状态点 + 名称 + PID/端口 + 启停按钮 */

import StatusDot from "../../components/StatusDot";
import { useServiceStatus } from "../../hooks/useServiceStatus";
import { useServicesStore } from "../../store/services";
import type { ServiceId } from "../../types/service";
import {
  gatewayStart,
  gatewayStop,
  chromeLaunch,
  chromeKill,
  localSearchStart,
  localSearchStop,
  toolBridgeStart,
  toolBridgeStop,
} from "../../lib/tauri";

interface Props {
  id: ServiceId;
  name: string;
}

// P3.5.125 (6/26 鸿波 catch "hermes hang 无监控"): hermes 不在 catfish 端 spawn
// (员工 brew install + launchd 起的), 真**:** 真**start/stop 真**:** 真**:** noop**
// (Companion 真**只通过 hermes_kill 触发 launchd 重启**, 不 spawn).
const STARTERS: Record<ServiceId, () => Promise<void>> = {
  gateway: gatewayStart,
  hermes: async () => {
    throw new Error("hermes 由 launchd 管, 不在 Companion 启动. 检查 brew services list");
  },
  chrome: chromeLaunch,
  local_search: localSearchStart,
  tool_bridge: toolBridgeStart,
};
const STOPPERS: Record<ServiceId, () => Promise<void>> = {
  gateway: gatewayStop,
  hermes: async () => {
    // hermes_kill 真**:** kill -9 → launchd 拉. 真**:** 真**真**真**真**stop**: 真**:**
    const { hermesKill } = await import("../../lib/tauri");
    await hermesKill();
  },
  chrome: chromeKill,
  local_search: localSearchStop,
  tool_bridge: toolBridgeStop,
};

export default function ServiceCard({ id, name }: Props) {
  useServiceStatus(id);
  const status = useServicesStore((s) => s.statuses[id]);

  const dotKind = !status
    ? "idle"
    : !status.running
      ? "err"
      : status.healthy
        ? "ok"
        : "warn";

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <strong>{name}</strong>
        <StatusDot status={dotKind} />
      </div>
      <div
        style={{
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-3)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {status
          ? `PID ${status.pid ?? "—"} · ${status.message ?? "—"}`
          : "(初始化中)"}
      </div>
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        <button onClick={() => void STARTERS[id]()}>启动</button>
        <button onClick={() => void STOPPERS[id]()}>停止</button>
      </div>
    </div>
  );
}
