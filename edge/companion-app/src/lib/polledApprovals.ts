/** 11/24 鸿波「为什么没有弹出授权卡片？」
 *
 * 批准请求 (`approval.request`) 只顺着「发起这一轮的那条流」推给窗口。这一轮不是从窗口发起的 ——
 * 卡住的对话解开后排队消息接着跑、鲶鱼中途重启、流断了 —— 请求就没人接, 等 30 分钟作废。
 *
 * 打开对话时、以及等回复期间, 问一下插件 P63 (GET /api/catfish/approvals/pending, 只读): 它列出
 *   - 流里的 (有 run_id): 登记成原生审批, 回执照旧走 POST /v1/runs/{run_id}/approval;
 *   - 只在 hermes 批准队列里的 (没 run_id, 有 session_key): 回执走 P15.2
 *     POST /v1/sessions/{session_key}/approval, 带 request_id 只批这一条。
 * 都放进同一个批准横幅, 跟流里来的长得一样。 */
import { config } from "./env";
import { fetchViaProxy } from "./http_proxy";
import { currentApprovalTimeoutS, hasNativeRequest, registerNativeApproval, type PendingApprovalDetail } from "./nativeApprovals";

const QUEUE_PREFIX = "queue:";

export interface PendingRow {
  run_id?: string;
  session_key?: string;
  session_id?: string;
  request_id?: string;
  execution_generation?: number;
  command?: string;
  description?: string;
}

/** 队列里的那种: key = queue:<session_key>|<request_id> */
export function queueKey(sessionKey: string, requestId: string): string {
  return `${QUEUE_PREFIX}${sessionKey}|${requestId}`;
}

export function parseQueueKey(key: string): { sessionKey: string; requestId: string } | null {
  if (!key.startsWith(QUEUE_PREFIX)) return null;
  const rest = key.slice(QUEUE_PREFIX.length);
  const i = rest.lastIndexOf("|");
  return i > 0 ? { sessionKey: rest.slice(0, i), requestId: rest.slice(i + 1) } : null;
}

export function isQueueKey(key: string): boolean {
  return key.startsWith(QUEUE_PREFIX);
}

/** 这一行在横幅里用的 key */
export function rowKey(r: PendingRow): string | null {
  if (r.run_id) return r.run_id;
  if (r.session_key && r.request_id) return queueKey(r.session_key, r.request_id);
  return null;
}

/** 11/24 鸿波「为什么批准后又再来一次？」: 员工答过的 request_id —— 服务那边的记录还没翻过来时,
 *  轮询会把它又列出来; 答过的一律不再弹 (最多记 200 条)。 */
const answered = new Set<string>();
export function markAnswered(requestId: string | undefined): void {
  if (!requestId) return;
  answered.add(requestId);
  if (answered.size > 200) answered.delete(answered.values().next().value as string);
}

/** 这个对话里要新放进横幅的 (已经顺着流来的、横幅里已有的、答过的不重复) */
export function newDetails(sessionId: string, rows: PendingRow[], existingKeys: Set<string>): PendingApprovalDetail[] {
  const out: PendingApprovalDetail[] = [];
  for (const r of rows) {
    if (r.session_id !== sessionId) continue;
    const key = rowKey(r);
    if (!key || existingKeys.has(key) || (r.request_id && answered.has(r.request_id))) continue;
    if (!r.run_id && r.request_id && hasNativeRequest(r.request_id)) continue;
    if (r.run_id) registerNativeApproval(r.run_id, r.request_id, r.execution_generation);
    out.push({
      approval_session_key: key,
      command: r.command ?? "",
      description: r.description ?? "",
      pattern_key: "",
      timeout_s: currentApprovalTimeoutS(),
      session_id: sessionId,
    });
  }
  return out;
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (config.useHermes && config.hermesAuthHeader) h["Authorization"] = config.hermesAuthHeader;
  return h;
}

export async function fetchPendingApprovals(): Promise<PendingRow[]> {
  const resp = await fetchViaProxy(`${config.backendUrl}/api/catfish/approvals/pending`, { method: "GET", headers: authHeaders() });
  if (!resp.ok) throw new Error(`pending approvals HTTP ${resp.status}`);
  const body = (await resp.json()) as { approvals?: PendingRow[] };
  return Array.isArray(body.approvals) ? body.approvals : [];
}

/** 队列里那种的回执: 只批这一条 */
export async function answerQueuedApproval(key: string, choice: string): Promise<{ resolved?: number; choice?: string }> {
  const k = parseQueueKey(key);
  if (!k) throw new Error("不是队列里的批准");
  markAnswered(k.requestId);
  const resp = await fetchViaProxy(`${config.backendUrl}/v1/sessions/${encodeURIComponent(k.sessionKey)}/approval`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ choice, request_id: k.requestId }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`chat_approval HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  const r = (await resp.json()) as { resolved?: number; choice?: string };
  // resolved=0: 那一条已经不在等了 (超时 / 别处批了) —— 跟原生 409 一样当作「作废了」
  if (!r.resolved) throw new Error("chat_approval HTTP 409: 这一条已经不在等批准了");
  return r;
}
