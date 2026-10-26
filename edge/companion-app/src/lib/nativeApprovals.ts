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

/** 10/15 hermes v0.22.1: 回执必须**恰好**是 {choice, request_id, execution_generation} 三个字段
 *  (api_server_authority_runs.respond_run: 多一个少一个都 invalid_params), generation 取事件里的。
 *  v0.22.0 只按名字读 choice / request_id, 多带 generation 不影响 —— 所以现在就带上, 升级不用再改。 */
interface NativeRun {
  requestId?: string;
  generation?: number;
}

const nativeRuns = new Map<string, NativeRun>();

/** 审批等待时长 (秒) 的起始值 —— Companion 给 hermes 配的 `approvals.timeout` (10/26 起 1800,
 *  见 src-tauri services/hermes_policy_config.rs)。hermes 的批准事件里不带这个数, 横幅打开时
 *  读一次实际配置 (setApprovalTimeoutS), 运维调过也对得上。 */
export const NATIVE_APPROVAL_TIMEOUT_S = 1800;

let approvalTimeoutS = NATIVE_APPROVAL_TIMEOUT_S;

export function currentApprovalTimeoutS(): number {
  return approvalTimeoutS;
}

export function setApprovalTimeoutS(seconds: number): void {
  if (Number.isFinite(seconds) && seconds > 0) approvalTimeoutS = Math.round(seconds);
}

export function registerNativeApproval(runId: string, requestId?: string, generation?: number): void {
  nativeRuns.set(runId, { requestId, generation });
}

/** 是原生审批就返回 { requestId, generation } (都可能为空), 不是返回 null。 */
export function nativeApprovalFor(key: string): NativeRun | null {
  return nativeRuns.get(key) ?? null;
}

/** POST /v1/runs/{run_id}/approval 的 body。有 generation 时三个字段一个不多 (v0.22.1 严格校验)。 */
export function nativeApprovalBody(native: NativeRun, choice: string): Record<string, unknown> {
  if (native.requestId && typeof native.generation === "number") {
    return { choice, request_id: native.requestId, execution_generation: native.generation };
  }
  return native.requestId ? { choice, request_id: native.requestId } : { choice };
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
  const generation = Number.isInteger(e.execution_generation) ? (e.execution_generation as number) : undefined;
  registerNativeApproval(runId, requestId, generation);
  return {
    approval_session_key: runId,
    command: String(e.command ?? ""),
    description: String(e.description ?? ""),
    pattern_key: String(e.pattern_key ?? ""),
    timeout_s: approvalTimeoutS,
  };
}
