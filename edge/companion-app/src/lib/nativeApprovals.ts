/**
 * 10/14 hermes v0.22: 原生审批 (`event: approval.request`) 的登记表。
 *
 * v0.22 流式对话里, 要员工同意的那一步直接以 `event: approval.request` 发出来, 回执走
 * `POST /v1/runs/{run_id}/approval` (run_id = 这次对话的 completion id)。原来插件 P15 发的
 * `hermes.tool.progress` + `approval_pending` 那套随升级退役。
 *
 * chat.ts 收到原生事件后照旧派发 `catfish:approval-pending` (卡片组件不用改), 只是把 run_id
 * 填在 `approval_session_key` 里, 并在这里登记; 员工点按钮时 toolBridgeChatApproval 查这张表
 * 决定走新接口还是老接口 (RoomLink / 老版本 hermes 仍是老接口)。
 */

const nativeRuns = new Map<string, string | undefined>();

/** 默认审批等待时长 (秒) —— hermes v0.22 `approvals.timeout` 出厂 300。卡片倒计时用。 */
export const NATIVE_APPROVAL_TIMEOUT_S = 300;

export function registerNativeApproval(runId: string, requestId?: string): void {
  nativeRuns.set(runId, requestId);
}

/** 是原生审批就返回 { requestId } (requestId 可能为空), 不是返回 null。 */
export function nativeApprovalFor(key: string): { requestId?: string } | null {
  if (!nativeRuns.has(key)) return null;
  return { requestId: nativeRuns.get(key) };
}

export function forgetNativeApproval(key: string): void {
  nativeRuns.delete(key);
}

export interface PendingApprovalDetail {
  approval_session_key: string;
  command: string;
  description: string;
  pattern_key: string;
  /** 卡片倒计时多少秒后自动收起 */
  timeout_s?: number;
}

/** 原生 `approval.request` 的 data → 卡片认的格式; 没有 run_id 返回 null。 */
export function pendingFromNativeEvent(ev: unknown): PendingApprovalDetail | null {
  if (!ev || typeof ev !== "object") return null;
  const e = ev as Record<string, unknown>;
  const runId = typeof e.run_id === "string" ? e.run_id : "";
  if (!runId) return null;
  const requestId = typeof e.request_id === "string" && e.request_id ? e.request_id : undefined;
  registerNativeApproval(runId, requestId);
  return {
    approval_session_key: runId,
    command: String(e.command ?? ""),
    description: String(e.description ?? ""),
    pattern_key: String(e.pattern_key ?? ""),
    timeout_s: NATIVE_APPROVAL_TIMEOUT_S,
  };
}
