/**
 * 横向协同 (RoomLink) 的握手与派工 (P50, 9/10)。
 *
 * # 三步
 *
 *   1. A → 邮筒 → B   「请求」: A 报出自己的房间坐标 + 一句话说明想让 B 干什么
 *   2. B → 邮筒 → A   「授权」: B 本人在 Companion 点了同意, B 的 hermes 签一张
 *                      grant (P49.1 已收成 dispatch+status), 连同 catalog 和 B 的
 *                      内网地址一起投回
 *   3. A ──直连──→ B   派工: A 用 grant 打 B:8642 /v1/runs, 之后轮询结果。
 *                      B 那边 P49.2/P49.3 再截一道: 工具要 B 批, 回复出端要 B 看
 *
 * 邮筒只是中转 (central room_link_router): 内容对中央不透明, 取走即清空。
 * 第 3 步不经过中央 —— 中央根本不知道 A 让 B 干了什么。
 *
 * # 本文件只做「纯逻辑 + 请求形态」
 *
 * 邮筒读写 / 请求与授权的编解码 / dispatch 组装 / 打 B 的两个请求。UI 与
 * 轮询循环在别处。roomLink.ts 是 B 侧「待审批」那半边, 别混。
 *
 * # 跟 hermes 对齐的地方 (改了这里要同步看那边)
 *
 *   - 17 个 dispatch 字段与 `_IDENTIFIER_RE` / `_DIGEST_RE`:
 *       hermes gateway/hosted_room_peer.py `HostedMemberDispatch.from_mapping`
 *   - grant 里必须与 dispatch 相等的 8 个字段: 同文件 `verify_room_grant`
 *   - `Idempotency-Key: room:{task_id}:{execution_generation}`, body 只许
 *     {input, hosted_room_dispatch}: gateway/platforms/api_server_room_dispatch.py
 *   - `Authorization: HermesRoom <grant>`: api_server_room_grants.py `_room_grant_token`
 *   - `home_install_id` / `authority_gateway_id` 都是 `install:<hex32>`:
 *       tui_gateway/hosted_room_service.py:103
 */
import { invoke } from "@tauri-apps/api/core";

import { config } from "./env";
import { fetchViaProxy } from "./http_proxy";
import { fetchWithAuth } from "./me";

// ─── 邮筒 (central) ──────────────────────────────

/** 跟 central `room_link_router.py` 必须一致。 */
const MAILBOX_PATH = "/api/room-link/mailbox";

export type MailboxKind = "request" | "grant";

export interface MailboxMessage {
  id: number;
  from: string;
  kind: MailboxKind;
  payload: string;
  created_at: string;
}

/** 投递。员工点了按钮才会调, 失败要抛出来让他看见。 */
export async function postMailbox(to: string, kind: MailboxKind, payload: string): Promise<void> {
  const resp = await fetchWithAuth(`${config.gatewayUrl}${MAILBOX_PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to, kind, payload }),
  });
  if (!resp.ok) {
    throw new Error(`邮筒投递失败 (${resp.status})`);
  }
}

/** 取件。后台轮询用: 永不抛、skipReauth、坏返回当空。 */
export async function takeMailbox(): Promise<MailboxMessage[]> {
  try {
    const resp = await fetchWithAuth(
      `${config.gatewayUrl}${MAILBOX_PATH}`,
      { method: "GET" },
      { skipReauth: true },
    );
    if (!resp.ok) return [];
    const data = (await resp.json()) as { messages?: unknown };
    if (!Array.isArray(data?.messages)) return [];
    return data.messages.filter(
      (m): m is MailboxMessage =>
        !!m &&
        typeof m === "object" &&
        typeof (m as MailboxMessage).from === "string" &&
        ((m as MailboxMessage).kind === "request" || (m as MailboxMessage).kind === "grant") &&
        typeof (m as MailboxMessage).payload === "string",
    );
  } catch {
    return [];
  }
}

// ─── 请求 / 授权 的载荷 ──────────────────────────────

const PAYLOAD_VERSION = 1;
/** hermes `_IDENTIFIER_RE`。 */
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$/;
const DIGEST_RE = /^[0-9a-f]{64}$/;
/** 「一句话说明」上限。B 批准前只看这个, 不该是整篇 prompt。 */
export const NOTE_MAX_CHARS = 500;

/** A 发给 B 的请求: B 的 hermes 签 grant 要的 5 个坐标 + 给 B 本人看的说明。 */
export interface RoomLinkRequest {
  v: typeof PAYLOAD_VERSION;
  room_id: string;
  home_install_id: string;
  authority_gateway_id: string;
  authority_epoch: number;
  member_id: string;
  note: string;
}

/** B 回给 A 的授权: hermes invitation 的原样返回 + B 的地址。 */
export interface RoomLinkGrant {
  v: typeof PAYLOAD_VERSION;
  room_id: string;
  grant: string;
  target_profile: string;
  catalog: { catalog_digest: string; [k: string]: unknown };
  target_url: string;
  expires_at: number;
}

function isIdentifier(v: unknown): v is string {
  return typeof v === "string" && IDENTIFIER_RE.test(v);
}

function isPositiveInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v) && v >= 1;
}

/** 解请求。坏的返 null —— 邮筒里的东西来自同事的机器, 不是可信输入。 */
export function parseRoomLinkRequest(payload: string): RoomLinkRequest | null {
  let o: Partial<RoomLinkRequest>;
  try {
    o = JSON.parse(payload);
  } catch {
    return null;
  }
  if (
    !o ||
    o.v !== PAYLOAD_VERSION ||
    !isIdentifier(o.room_id) ||
    !isIdentifier(o.home_install_id) ||
    !isIdentifier(o.authority_gateway_id) ||
    !isPositiveInt(o.authority_epoch) ||
    !isIdentifier(o.member_id) ||
    typeof o.note !== "string" ||
    o.note.length > NOTE_MAX_CHARS
  ) {
    return null;
  }
  return {
    v: PAYLOAD_VERSION,
    room_id: o.room_id,
    home_install_id: o.home_install_id,
    authority_gateway_id: o.authority_gateway_id,
    authority_epoch: o.authority_epoch,
    member_id: o.member_id,
    note: o.note,
  };
}

export function parseRoomLinkGrant(payload: string): RoomLinkGrant | null {
  let o: Partial<RoomLinkGrant>;
  try {
    o = JSON.parse(payload);
  } catch {
    return null;
  }
  if (
    !o ||
    o.v !== PAYLOAD_VERSION ||
    !isIdentifier(o.room_id) ||
    typeof o.grant !== "string" ||
    !o.grant.includes(".") ||
    !isIdentifier(o.target_profile) ||
    !o.catalog ||
    typeof o.catalog !== "object" ||
    !DIGEST_RE.test(String(o.catalog.catalog_digest)) ||
    typeof o.target_url !== "string" ||
    !/^https?:\/\/[^/?#]+$/.test(o.target_url) ||
    typeof o.expires_at !== "number"
  ) {
    return null;
  }
  return {
    v: PAYLOAD_VERSION,
    room_id: o.room_id,
    grant: o.grant,
    target_profile: o.target_profile,
    catalog: o.catalog,
    target_url: o.target_url,
    expires_at: o.expires_at,
  };
}

// ─── A 侧: 生成请求 ──────────────────────────────

export interface LocalIdentity {
  authority_gateway_id: string | null;
  lan_ip: string | null;
}

/** Rust 读 hermes 的 install_id + 探内网地址 (commands/room_link.rs)。 */
export function fetchLocalIdentity(): Promise<LocalIdentity> {
  return invoke<LocalIdentity>("room_link_local_identity");
}

/** A 起一个新房间。authority 就是 A 自己 (两人房间, A 是 host), epoch 从 1 起。 */
export function newRoomLinkRequest(self: LocalIdentity, note: string): RoomLinkRequest {
  if (!self.authority_gateway_id) {
    throw new Error("本机 hermes 还没有 install_id (hermes 没起过?)");
  }
  const trimmed = note.trim();
  if (!trimmed || trimmed.length > NOTE_MAX_CHARS) {
    throw new Error(`说明要 1–${NOTE_MAX_CHARS} 字`);
  }
  return {
    v: PAYLOAD_VERSION,
    room_id: `catfish-room-${crypto.randomUUID()}`,
    home_install_id: self.authority_gateway_id,
    authority_gateway_id: self.authority_gateway_id,
    authority_epoch: 1,
    member_id: `member-${crypto.randomUUID()}`,
    note: trimmed,
  };
}

// ─── A 侧: dispatch 组装 + 打 B ──────────────────────────────

/** grant 的第一段是 base64url(JSON), 没加密只签名 —— A 从里面读回 B 定的坐标。 */
export function decodeGrantPayload(grant: string): Record<string, unknown> {
  const head = grant.split(".")[0] ?? "";
  const b64 = head.replace(/-/g, "+").replace(/_/g, "/");
  const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
  return JSON.parse(atob(padded)) as Record<string, unknown>;
}

async function sha256Hex(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export interface HostedMemberDispatch {
  protocol_version: number;
  room_id: string;
  home_install_id: string;
  authority_gateway_id: string;
  authority_epoch: number;
  member_id: string;
  target_install_id: string;
  target_profile: string;
  task_id: string;
  execution_generation: number;
  source_event_seq: number;
  cancellation_scope_id: string;
  prompt: string;
  prompt_digest: string;
  capability_digest: string;
  execution_policy_digest: string;
  trace_id: string;
}

const GRANT_IDENTIFIERS_TO_COPY = [
  "room_id",
  "home_install_id",
  "authority_gateway_id",
  "member_id",
  "target_install_id",
  "target_profile",
] as const;

/**
 * 从 grant + catalog + prompt 组一份 B 的 hermes 会认的 dispatch。
 * 9 个坐标抄 grant (B 签过的, 改一个字 verify_room_grant 就拒), capability_digest
 * 抄 catalog, 剩下 7 个 A 自己生成。一次请求一个 task, generation/seq 固定 1。
 */
export async function buildDispatch(
  grant: RoomLinkGrant,
  prompt: string,
): Promise<HostedMemberDispatch> {
  const p = decodeGrantPayload(grant.grant);
  for (const f of GRANT_IDENTIFIERS_TO_COPY) {
    if (!isIdentifier(p[f])) throw new Error(`grant 缺字段 ${f}`);
  }
  if (!DIGEST_RE.test(String(p.execution_policy_digest))) {
    throw new Error("grant 缺字段 execution_policy_digest");
  }
  if (!isPositiveInt(p.version) || !isPositiveInt(p.authority_epoch)) {
    throw new Error("grant 版本 / epoch 不对");
  }
  if (p.room_id !== grant.room_id) {
    throw new Error("grant 的 room_id 跟授权信里的不一致");
  }
  const text = prompt.trim();
  if (!text) throw new Error("prompt 不能为空");
  const id = crypto.randomUUID();
  return {
    protocol_version: p.version,
    room_id: p.room_id as string,
    home_install_id: p.home_install_id as string,
    authority_gateway_id: p.authority_gateway_id as string,
    authority_epoch: p.authority_epoch,
    member_id: p.member_id as string,
    target_install_id: p.target_install_id as string,
    target_profile: p.target_profile as string,
    task_id: `task-${id}`,
    execution_generation: 1,
    source_event_seq: 1,
    cancellation_scope_id: `scope-${id}`,
    prompt: text,
    prompt_digest: await sha256Hex(text),
    capability_digest: grant.catalog.catalog_digest,
    execution_policy_digest: p.execution_policy_digest as string,
    trace_id: `trace-${id}`,
  };
}

export const idempotencyKey = (d: HostedMemberDispatch): string =>
  `room:${d.task_id}:${d.execution_generation}`;

/** 派工。走 Rust 代理 (CSP 不放行内网地址)。返 run_id。 */
export async function dispatchToPeer(
  grant: RoomLinkGrant,
  dispatch: HostedMemberDispatch,
): Promise<string> {
  const resp = await fetchViaProxy(`${grant.target_url}/v1/runs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `HermesRoom ${grant.grant}`,
      "Idempotency-Key": idempotencyKey(dispatch),
    },
    body: JSON.stringify({ input: dispatch.prompt, hosted_room_dispatch: dispatch }),
  });
  const data = (await resp.json().catch(() => ({}))) as {
    run_id?: string;
    error?: { message?: string; code?: string };
  };
  if (!resp.ok || typeof data.run_id !== "string") {
    throw new Error(data.error?.message ?? `对方 hermes 拒绝 (${resp.status})`);
  }
  return data.run_id;
}

export type PeerRunStatus =
  | "queued"
  | "started"
  | "running"
  | "waiting_for_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface PeerRun {
  status: PeerRunStatus;
  output?: string;
  error?: string;
}

/** 轮询 B 上的 run。网络错也抛 —— 调用方决定退避, 别在这里吞。 */
export async function pollPeerRun(grant: RoomLinkGrant, runId: string): Promise<PeerRun> {
  const resp = await fetchViaProxy(`${grant.target_url}/v1/runs/${encodeURIComponent(runId)}`, {
    method: "GET",
    headers: { Authorization: `HermesRoom ${grant.grant}` },
  });
  if (!resp.ok) {
    throw new Error(`查询对方 run 失败 (${resp.status})`);
  }
  const data = (await resp.json()) as Partial<PeerRun>;
  if (typeof data?.status !== "string") {
    throw new Error("对方 hermes 返回形态不对");
  }
  return {
    status: data.status,
    output: typeof data.output === "string" ? data.output : undefined,
    error: typeof data.error === "string" ? data.error : undefined,
  };
}

export const isTerminal = (s: PeerRunStatus): boolean =>
  s === "completed" || s === "failed" || s === "cancelled" || s === "interrupted";
