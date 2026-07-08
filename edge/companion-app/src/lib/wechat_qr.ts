/** BL-WECHAT-CATFISH-BIND v3 (5/26 鸿波): WeChat iLink QR login via hermes:8642.
 *
 * # 后端契约 (catfish plugin P30, 补 hermes 缺的两条 route)
 *
 * 7/8 audit (P3.5.198): grep hermes v0.18 (v0.17 备份同) gateway/platforms/api_server.py
 * 全库无这两条 route. hermes gateway/platforms/weixin.py:1003 只有 CLI 阻塞的
 * async qr_login() helper (打印 ASCII 二维码到 stdout, 单 flow 循环 poll), 不是
 * HTTP endpoint. catfish 补 P30 (hermes-plugins/catfish-xcatfish-user/plugin.py):
 * 在 APIServerAdapter 上 attach _handle_wechat_qr_start/poll 两个 handler,
 * 复用 hermes ILINK_BASE_URL / EP_GET_BOT_QR / EP_GET_QR_STATUS / _api_get /
 * _make_ssl_connector / save_weixin_account. Route 由 Application.__init__ patch
 * 在 router 未 freeze 时挂上 (跟 P26 cron 同时机).
 *
 * - POST /api/platforms/wechat/qr_login/start
 *   返 { qrcode, qrcode_url, scan_data } — qrcode 当 session token, scan_data 是
 *   "把这串编码进二维码图给微信扫的内容".
 *
 * - GET /api/platforms/wechat/qr_login/poll?qrcode=<token>
 *   返 { status: "wait" | "scaned" | "confirmed" | "expired",
 *        account_id?, user_id? } — confirmed 时 P30 已 save_weixin_account
 *        (~/.hermes/weixin/accounts/{account_id}.json chmod 600).
 *
 * # Auth
 *
 * hermes 8642 上这俩 endpoint 走 self._check_auth (per-handler, 不是 middleware)
 * — Companion fetchWithAuth 在 hermes 模式下自动带 Bearer API_SERVER_KEY.
 *
 * # 失败模式
 *
 * - start 502 → ilink API 挂了 (大概率网络); UI 提示员工检查网络后重试
 * - poll 404 (session not found) → hermes 重启了 in-memory session 丢; UI 自动跑一次 start
 * - poll 200 + _warning 字段 → 临时网络抖动, UI 不动让它下次再 poll (等到 wait/expired)
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";

export interface QrStartResponse {
  qrcode: string;
  qrcode_url: string;
  /** 实际编码进二维码图给微信扫的内容 (qrcode_url 优先, fallback qrcode 原 hex). */
  scan_data: string;
}

export type QrPollStatus = "wait" | "scaned" | "confirmed" | "expired";

export interface QrPollResponse {
  status: QrPollStatus;
  account_id?: string;
  user_id?: string;
  _warning?: string;
}

function hermesBase(): string {
  // BL-AUTH-DECOUPLE-A5 (5/19): hermes 模式下 backendUrl 已经是 hermes:8642.
  // 没启 hermes 模式 → 这俩端点没法用 (走 gateway 不存在). 调用方自己判断.
  return config.backendUrl.replace(/\/+$/, "");
}

export async function wechatQrStart(): Promise<QrStartResponse> {
  const resp = await fetchWithAuth(
    `${hermesBase()}/api/platforms/wechat/qr_login/start`,
    { method: "POST" },
  );
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`start 失败 ${resp.status}: ${body.slice(0, 200)}`);
  }
  return (await resp.json()) as QrStartResponse;
}

export async function wechatQrPoll(qrcode: string): Promise<QrPollResponse> {
  const url = `${hermesBase()}/api/platforms/wechat/qr_login/poll?qrcode=${encodeURIComponent(
    qrcode,
  )}`;
  const resp = await fetchWithAuth(url);
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`poll 失败 ${resp.status}: ${body.slice(0, 200)}`);
  }
  return (await resp.json()) as QrPollResponse;
}
