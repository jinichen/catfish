// Service diagnostics are read-only; a failed probe must not trigger kill/restart.
export type ServiceId = "gateway" | "hermes" | "chrome" | "local_search" | "tool_bridge";

export interface ServiceStatus {
  running: boolean;
  pid: number | null;
  /** 业务面是否健康 —— 进程活着不等于服务能用 */
  healthy: boolean;
  /** IPC failed; the process state could not be established. */
  probeError?: boolean;
  /** 监听端口（如果适用） */
  port?: number;
  /** 给 UI 显示的状态描述 */
  message?: string;
}
