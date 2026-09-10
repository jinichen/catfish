/**
 * 横向协同的单例状态 (P50, 9/10): 邮筒唯一轮询者 + 我发出去的请求的生命周期。
 *
 * # 为什么必须单例
 *
 * central 邮筒取走即清空。B 侧「同事的请求」卡和 A 侧「请同事帮忙」卡如果各自
 * 轮询, 一封信被谁取走全看运气 —— 另一张卡永远等不到。所以邮筒只在这里轮询
 * 一次, 按 kind 分拣: request 进 inbox (B 侧卡片消费), grant 去匹配我发出的
 * 请求 (A 侧卡片消费)。有订阅者才轮询, 最后一个订阅者走了就停。
 *
 * # 一条外发请求的生命周期
 *
 *   waiting_grant  已投邮筒, 等对方点头 (10 分钟没回 = 对方没理 / 邮筒过期)
 *   dispatching    收到 grant, 组 dispatch, 直连对方 8642 派工
 *   running        对方的小鲶在干; status=waiting_for_approval 是对方本人在批工具
 *   done / failed  终态。failed 的 error 里 outbound_declined = 对方看了回复没放行
 *
 * 状态只在内存 —— Companion 关了就没了, 跟邮筒「不留副本」同一取舍。
 */
import { useSyncExternalStore } from "react";

import {
  buildDispatch,
  dispatchToPeer,
  fetchLocalIdentity,
  isTerminal,
  newRoomLinkRequest,
  pollPeerRun,
  postMailbox,
  type PeerRunStatus,
  type RoomLinkGrant,
} from "./roomLinkHandshake";
import {
  approveInboxRequest,
  startMailboxPolling,
  type InboxGrant,
  type InboxRequest,
} from "./roomLinkInbox";

export type OutgoingPhase = "waiting_grant" | "dispatching" | "running" | "done" | "failed";

export interface OutgoingRequest {
  room_id: string;
  to: string;
  text: string;
  created_at: number;
  phase: OutgoingPhase;
  run_id?: string;
  run_status?: PeerRunStatus;
  output?: string;
  error?: string;
}

export interface RoomLinkState {
  inbox: InboxRequest[];
  outgoing: OutgoingRequest[];
}

/** 等 grant 的上限 = 邮筒 TTL。过了对方就算同意, 信也已经被中央清掉了。 */
export const GRANT_WAIT_TIMEOUT_MS = 10 * 60 * 1000;
export const RUN_POLL_INTERVAL_MS = 3_000;

let state: RoomLinkState = { inbox: [], outgoing: [] };
const listeners = new Set<() => void>();
let stopPolling: (() => void) | null = null;
const runPollers = new Map<string, ReturnType<typeof setTimeout>>();

function emit(next: RoomLinkState) {
  state = next;
  for (const l of listeners) l();
}

function patchOutgoing(roomId: string, patch: Partial<OutgoingRequest>) {
  emit({
    ...state,
    outgoing: state.outgoing.map((o) => (o.room_id === roomId ? { ...o, ...patch } : o)),
  });
}

// ─── 邮筒分拣 ──────────────────────────────

function onRequests(items: InboxRequest[]) {
  const seen = new Set(state.inbox.map((x) => x.id));
  const fresh = items.filter((x) => !seen.has(x.id));
  if (fresh.length) emit({ ...state, inbox: [...state.inbox, ...fresh] });
}

function onGrants(items: InboxGrant[]) {
  for (const g of items) {
    const mine = state.outgoing.find(
      (o) => o.room_id === g.grant.room_id && o.phase === "waiting_grant",
    );
    // 不是我等的 (已超时 / 已放弃 / 冒充的 room_id) → 丢
    if (!mine || mine.to !== g.from) continue;
    void dispatch(mine, g.grant);
  }
}

async function dispatch(req: OutgoingRequest, grant: RoomLinkGrant) {
  patchOutgoing(req.room_id, { phase: "dispatching" });
  try {
    const d = await buildDispatch(grant, req.text);
    const runId = await dispatchToPeer(grant, d);
    patchOutgoing(req.room_id, { phase: "running", run_id: runId, run_status: "started" });
    scheduleRunPoll(req.room_id, grant, runId);
  } catch (e) {
    patchOutgoing(req.room_id, { phase: "failed", error: e instanceof Error ? e.message : String(e) });
  }
}

function scheduleRunPoll(roomId: string, grant: RoomLinkGrant, runId: string) {
  const tick = async () => {
    runPollers.delete(roomId);
    try {
      const r = await pollPeerRun(grant, runId);
      if (isTerminal(r.status)) {
        patchOutgoing(roomId, {
          phase: r.status === "completed" ? "done" : "failed",
          run_status: r.status,
          output: r.output,
          error: r.error ?? (r.status === "completed" ? undefined : r.status),
        });
        return;
      }
      patchOutgoing(roomId, { run_status: r.status });
    } catch {
      /* 网络抖动: 下一轮再试, 不改状态 */
    }
    if (state.outgoing.some((o) => o.room_id === roomId && o.phase === "running")) {
      runPollers.set(roomId, setTimeout(tick, RUN_POLL_INTERVAL_MS));
    }
  };
  runPollers.set(roomId, setTimeout(tick, RUN_POLL_INTERVAL_MS));
}

/** 每轮邮筒轮询顺手判超时。 */
function expireStale(now: number) {
  const stale = state.outgoing.filter(
    (o) => o.phase === "waiting_grant" && now - o.created_at >= GRANT_WAIT_TIMEOUT_MS,
  );
  for (const o of stale) {
    patchOutgoing(o.room_id, { phase: "failed", error: "对方 10 分钟内没有回应" });
  }
}

// ─── 对外 ──────────────────────────────

export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  if (!stopPolling) {
    stopPolling = startMailboxPolling(
      (items) => { expireStale(Date.now()); onRequests(items); },
      (items) => { expireStale(Date.now()); onGrants(items); },
    );
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && stopPolling) {
      stopPolling();
      stopPolling = null;
    }
  };
}

export const getSnapshot = (): RoomLinkState => state;

export function useRoomLink(): RoomLinkState {
  return useSyncExternalStore(subscribe, getSnapshot);
}

/** A 侧: 发一封请求。抛 = 没发出去 (本机没 install_id / 邮筒不可用)。 */
export async function sendRequest(to: string, text: string): Promise<void> {
  const target = to.trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+$/.test(target)) throw new Error("同事的邮箱格式不对");
  const self = await fetchLocalIdentity();
  const req = newRoomLinkRequest(self, text);
  await postMailbox(target, "request", JSON.stringify(req));
  emit({
    ...state,
    outgoing: [
      { room_id: req.room_id, to: target, text: req.note, created_at: Date.now(), phase: "waiting_grant" },
      ...state.outgoing,
    ],
  });
}

/** A 侧: 从列表里拿掉一条 (终态或放弃等待)。正在跑的不许删 —— 对方还在干。 */
export function dismissOutgoing(roomId: string): void {
  const o = state.outgoing.find((x) => x.room_id === roomId);
  if (!o || o.phase === "running" || o.phase === "dispatching") return;
  emit({ ...state, outgoing: state.outgoing.filter((x) => x.room_id !== roomId) });
}

/** B 侧: 同意 → 签 grant 投回; 拒绝 → 本地丢。抛 = 没成, 条目保留让员工重试。 */
export async function resolveInbox(item: InboxRequest, choice: "approve" | "deny"): Promise<void> {
  if (choice === "approve") await approveInboxRequest(item);
  emit({ ...state, inbox: state.inbox.filter((x) => x.id !== item.id) });
}

/** 测试用: 清空。 */
export function _resetForTest(): void {
  for (const t of runPollers.values()) clearTimeout(t);
  runPollers.clear();
  if (stopPolling) stopPolling();
  stopPolling = null;
  listeners.clear();
  state = { inbox: [], outgoing: [] };
}
