/**
 * 横向协同 (RoomLink) 待审批的读取端 + 拍板动作 (9/10, 配插件 P49)。
 *
 * # 场景
 *
 * 员工 A 的小鲶让员工 B 的小鲶干活。B 这台机器上, hermes 插件 P49 把两类
 * 事截住等 B 点头:
 *
 *   approvals  B 的 bot 要跑某个工具 (execute_code 之类) —— 跟员工自己对话时
 *              弹的审批按钮是同一回事, 只是发起方是 A 而不是 B 本人
 *   outputs    B 的 bot 跑完了, 回复要发回给 A —— 这是 B 的本地数据出端,
 *              P49.2 把 run 挂在 hermes 里等 B 看一眼
 *
 * 两类的拍板走两个不同的端点, 因为它们在 hermes 里是两套机制:
 *   approvals → P15.2 现成的 POST /v1/sessions/{run_id}/approval
 *               (room run 的 approval_session_key 就是 run_id)
 *   outputs   → P49 新加的 POST /api/catfish/room-link/outputs/{run_id}
 *
 * # 跟 agentActivity.ts 同一套纪律
 *
 * 轮询永不抛、失败退回 {available:false}、skipReauth (后台探针不许把员工拽去
 * 登录页 —— 8/9 那次 3 秒一弹的坑)。路径带 /api/catfish/ 前缀, me.ts 的
 * HERMES_OWNED_API_PREFIXES 已经认它走 hermes 8642, 不用改路由表。
 *
 * 拍板动作**会抛** —— 那是员工点了按钮, 失败得让他知道, 不能静默吞掉。
 */
import { config } from "./env";
import { fetchWithAuth } from "./me";
import { toolBridgeChatApproval } from "./tauri";

/** 跟插件 `plugin_room_link.py:ROUTE_LIST / ROUTE_OUTPUT_ITEM` 必须一致。 */
const PENDING_PATH = "/api/catfish/room-link/pending";
const outputPath = (runId: string) =>
  `/api/catfish/room-link/outputs/${encodeURIComponent(runId)}`;

/** 轮询间隔。出站审批在 hermes 侧挂 10 分钟, 3 秒一看足够。 */
export const POLL_INTERVAL_MS = 3_000;
const PROBE_TIMEOUT_MS = 4_000;

/**
 * 一条待批的工具调用。字段是 hermes approval_data 的原样 (command 已在插件侧
 * 经 _redact_approval_command 脱敏), 全部可选 —— 少一个不该让 UI 崩。
 */
export interface RoomLinkApproval {
  run_id: string;
  command?: string;
  description?: string;
  pattern_keys?: string[];
  smart_denied?: boolean;
}

/** 一条待批的出站回复。 */
export interface RoomLinkOutput {
  run_id: string;
  final_response: string;
}

export interface RoomLinkPending {
  available: boolean;
  reason?: string;
  approvals: RoomLinkApproval[];
  outputs: RoomLinkOutput[];
}

export const PROBE_UNREACHABLE = "probe_unreachable";

const EMPTY: RoomLinkPending = { available: false, approvals: [], outputs: [] };

/** 拉一次待审批快照。**永不抛**。 */
export async function fetchRoomLinkPending(): Promise<RoomLinkPending> {
  const url = `${config.backendUrl}${PENDING_PATH}`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS);
  try {
    const resp = await fetchWithAuth(
      url,
      { method: "GET", signal: ctl.signal },
      { skipReauth: true },
    );
    if (!resp.ok) {
      return { ...EMPTY, reason: `http_${resp.status}` };
    }
    const data = (await resp.json()) as Partial<RoomLinkPending>;
    if (
      !data ||
      typeof data !== "object" ||
      !Array.isArray(data.approvals) ||
      !Array.isArray(data.outputs)
    ) {
      return { ...EMPTY, reason: "bad_shape" };
    }
    return {
      available: true,
      approvals: data.approvals.filter((a) => a && typeof a.run_id === "string"),
      outputs: data.outputs.filter(
        (o) => o && typeof o.run_id === "string" && typeof o.final_response === "string",
      ),
    };
  } catch {
    return { ...EMPTY, reason: PROBE_UNREACHABLE };
  } finally {
    clearTimeout(timer);
  }
}

/** 有没有东西要给 B 看。Card 用它决定显不显示。 */
export function hasPending(r: RoomLinkPending): boolean {
  return r.available && (r.approvals.length > 0 || r.outputs.length > 0);
}

/**
 * B 对出站回复拍板。approve → hermes 里挂着的 run 醒来, output 原样给 A;
 * deny → run 以 failed 结束, A 收到 "outbound declined by target"。
 *
 * 404 = 已经超时被 run 自己收尾了, 或 run_id 对不上 —— 调用方刷新列表即可,
 * 不算错误。其它非 2xx 抛。
 */
export async function resolveRoomLinkOutput(
  runId: string,
  choice: "approve" | "deny",
): Promise<{ ok: boolean; gone: boolean }> {
  const url = `${config.backendUrl}${outputPath(runId)}`;
  const resp = await fetchWithAuth(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ choice }),
  });
  if (resp.status === 404) return { ok: false, gone: true };
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`room-link output ${choice} HTTP ${resp.status}: ${text.slice(0, 200)}`);
  }
  return { ok: true, gone: false };
}

/**
 * B 对工具调用拍板。直接复用 P15.2 那条路 —— room run 的 approval_session_key
 * 就是 run_id (api_server_runs.py:613), 一行不用改。
 *
 * 只给 once / deny 两档: session / always 会让 A 后续的 run 免审, 那是把
 * P49.1 特意收回来的 approve 权限又送出去。
 */
export async function resolveRoomLinkApproval(
  runId: string,
  choice: "once" | "deny",
): Promise<void> {
  await toolBridgeChatApproval(runId, choice);
}

/** 起轮询, 返停止函数。setTimeout 链, 慢响应不堆积。 */
export function startRoomLinkPolling(
  onUpdate: (r: RoomLinkPending) => void,
  intervalMs: number = POLL_INTERVAL_MS,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    if (stopped) return;
    const r = await fetchRoomLinkPending();
    if (stopped) return;
    onUpdate(r);
    timer = setTimeout(tick, intervalMs);
  };

  void tick();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
