/**
 * 横向协同 —— 邮筒轮询 + B 侧「同事的请求」的处理 (P50, 9/10)。
 *
 * # 邮筒是取走即清空的
 *
 * central 的邮筒 GET 一次就把内容删了 (中央不留副本)。所以取回来的信必须
 * 在本模块**内存里攥着**直到员工处理; Companion 关了没处理的就没了 —— A 那
 * 边等 10 分钟超时会重发, 这是有意的取舍: 宁可丢一封, 不让中央存内容。
 *
 * 一次轮询两种信都会来: `request` 是同事想让我的小鲶帮忙 (本模块处理),
 * `grant` 是同事批了我的请求 (回给发起方那半边, 第 2 步做 UI)。
 *
 * # 同意 = 让我的 hermes 签一张 grant
 *
 * 调本机 hermes `POST /v1/room-members/invitations` (api_server_room_grants.py:121),
 * 插件 P49.1 会把权限收成 dispatch+status。返回的 grant + catalog + 我的内网
 * 地址一起投回给同事。之后同事直连我 8642, 每一步工具 / 出站回复都还要我
 * 在 RoomLinkPendingCard 里再点头 —— 这里同意的只是「可以来找我的小鲶」。
 *
 * 拒绝 = 本地丢掉, 不回信。A 侧等超时。
 */
import { config } from "./env";
import { fetchWithAuth } from "./me";
import {
  fetchLocalIdentity,
  parseRoomLinkGrant,
  parseRoomLinkRequest,
  postMailbox,
  takeMailbox,
  type RoomLinkGrant,
  type RoomLinkRequest,
} from "./roomLinkHandshake";

/** 邮筒 10 分钟窗口, 15 秒一看; 中央 4 个 worker 顶几百人这个频率没问题。 */
export const MAILBOX_POLL_INTERVAL_MS = 15_000;
/** 签给同事的 grant 有效期。hermes 上限 86400, 一小时够干一件事。 */
export const GRANT_TTL_SECONDS = 3600;

/** 同事发来的一封请求 (已解析), from 是中央核过的发件人 email。 */
export interface InboxRequest {
  id: number;
  from: string;
  received_at: string;
  request: RoomLinkRequest;
}

/** 同事回给我的一封授权 (已解析)。 */
export interface InboxGrant {
  id: number;
  from: string;
  received_at: string;
  grant: RoomLinkGrant;
}

/** 起邮筒轮询, 返停止函数。解析失败的信直接丢 (来自同事机器, 不可信)。 */
export function startMailboxPolling(
  onRequests: (items: InboxRequest[]) => void,
  onGrants: (items: InboxGrant[]) => void,
  intervalMs: number = MAILBOX_POLL_INTERVAL_MS,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    if (stopped) return;
    const messages = await takeMailbox();
    if (stopped) return;
    const requests: InboxRequest[] = [];
    const grants: InboxGrant[] = [];
    for (const m of messages) {
      if (m.kind === "request") {
        const request = parseRoomLinkRequest(m.payload);
        if (request) requests.push({ id: m.id, from: m.from, received_at: m.created_at, request });
      } else {
        const grant = parseRoomLinkGrant(m.payload);
        if (grant) grants.push({ id: m.id, from: m.from, received_at: m.created_at, grant });
      }
    }
    if (requests.length) onRequests(requests);
    if (grants.length) onGrants(grants);
    timer = setTimeout(tick, intervalMs);
  };

  void tick();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

/** hermes invitation 的返回 (api_server_room_grants.py:211)。 */
interface InvitationResponse {
  grant: string;
  target_profile: string;
  catalog: { catalog_digest: string; [k: string]: unknown };
  expires_at: number;
}

/** 本机 hermes 的端口: 从 backendUrl 取, 默认 8642。同事要连的是这个口。 */
export function hermesPortFrom(backendUrl: string): number {
  const m = /^https?:\/\/[^/:]+:(\d+)/.exec(backendUrl);
  return m ? Number(m[1]) : 8642;
}

/**
 * 同意: 让本机 hermes 签 grant, 连同 catalog + 我的地址投回给同事。
 * 任何一步失败都抛 —— 员工点了按钮, 得让他知道没成。
 */
export async function approveInboxRequest(item: InboxRequest): Promise<void> {
  const r = item.request;
  const resp = await fetchWithAuth(`${config.backendUrl}/v1/room-members/invitations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      room_id: r.room_id,
      home_install_id: r.home_install_id,
      authority_gateway_id: r.authority_gateway_id,
      authority_epoch: r.authority_epoch,
      member_id: r.member_id,
      ttl_seconds: GRANT_TTL_SECONDS,
    }),
  });
  const data = (await resp.json().catch(() => ({}))) as Partial<InvitationResponse> & {
    error?: { message?: string };
  };
  if (!resp.ok || typeof data.grant !== "string" || !data.catalog) {
    // 最常见的真因: hermes approvals 关着 (catalog_mapping 拒 "requires manual or smart approvals")
    throw new Error(data.error?.message ?? `本机 hermes 拒绝签发 (${resp.status})`);
  }

  const self = await fetchLocalIdentity();
  if (!self.lan_ip) {
    throw new Error("拿不到本机内网地址 (没联网?), 同事连不到你");
  }
  const grant: RoomLinkGrant = {
    v: 1,
    room_id: r.room_id,
    grant: data.grant,
    target_profile: data.target_profile ?? "default",
    catalog: data.catalog,
    target_url: `http://${self.lan_ip}:${hermesPortFrom(config.backendUrl)}`,
    expires_at: typeof data.expires_at === "number" ? data.expires_at : 0,
  };
  await postMailbox(item.from, "grant", JSON.stringify(grant));
}
