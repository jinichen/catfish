export type ServiceId = "gateway" | "chrome" | "local_search" | "tool_bridge";

export interface ServiceStatus {
  running: boolean;
  pid: number | null;
  /** 业务面是否健康 —— 进程活着不等于服务能用 */
  healthy: boolean;
  /** 监听端口（如果适用） */
  port?: number;
  /** 给 UI 显示的状态描述 */
  message?: string;
}
