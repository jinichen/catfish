// P3.5.125 (6/26 鸿波 catch "catfish 没监控 hermes/chrome hang"): 加 hermes 服务监控.
// hermes 默认 launchd KeepAlive 真**crash 时拉**, 但 hang (GIL deadlock / IO block)
// launchd 不知道. Companion 主动 ping /healthz + 连续 N 次 unhealthy → kill -9
// 触发 launchd 自动重启.
export type ServiceId = "gateway" | "hermes" | "chrome" | "local_search" | "tool_bridge";

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
