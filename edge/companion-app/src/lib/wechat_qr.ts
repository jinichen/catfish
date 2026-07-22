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

import { invoke } from "@tauri-apps/api/core";
import { fetchViaProxy } from "./http_proxy";

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

/** BL-WECHAT-QR-HERMES-STANDALONE (7/18 鸿波 catch Task #1 后 WeChat 404):
 *
 * /api/platforms/* 是 hermes 8642 独占 route (gateway 无这段路由). Task #60 关
 * hermes_api.enabled 让 chat 走 gateway 直连后, config.backendUrl 切 gateway,
 * WeChat URL 拼成 gateway/api/platforms/... → 404.
 *
 * 修: WeChat QR **无视 useHermes** 强走 hermes 8642. 从 Rust 端 hermes_api_url_forced
 * 拿 hermes URL, hermes_api_auth_header_forced 拿 Bearer key. 这俩 command 无视
 * enabled 只看 key 有没. 若 key 缺 (hermes 未配置) → 显式报错让 UI 提示员工.
 *
 * chat 主链路仍走 chat.ts:222 useHermes 分叉, 不影响.
 */
async function hermesForcedBase(): Promise<string> {
  const url = await invoke<string>("hermes_api_url_forced");
  return url.replace(/\/+$/, "");
}

async function hermesForcedAuthHeader(): Promise<string> {
  const h = await invoke<string | null>("hermes_api_auth_header_forced");
  if (!h) {
    throw new Error(
      "hermes API key 缺 (~/.catfish/companion.yaml hermes_api.key). " +
        "WeChat 绑定需要 hermes 8642 · 让 IT 配 companion.yaml + ~/.hermes/.env 里的 API_SERVER_KEY.",
    );
  }
  return h;
}

export async function wechatQrStart(): Promise<QrStartResponse> {
  const [base, auth] = await Promise.all([hermesForcedBase(), hermesForcedAuthHeader()]);
  const resp = await fetchViaProxy(`${base}/api/platforms/wechat/qr_login/start`, {
    method: "POST",
    headers: { Authorization: auth },
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`start 失败 ${resp.status}: ${body.slice(0, 200)}`);
  }
  return (await resp.json()) as QrStartResponse;
}

export async function wechatQrPoll(qrcode: string): Promise<QrPollResponse> {
  const [base, auth] = await Promise.all([hermesForcedBase(), hermesForcedAuthHeader()]);
  const url = `${base}/api/platforms/wechat/qr_login/poll?qrcode=${encodeURIComponent(qrcode)}`;
  const resp = await fetchViaProxy(url, { headers: { Authorization: auth } });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`poll 失败 ${resp.status}: ${body.slice(0, 200)}`);
  }
  return (await resp.json()) as QrPollResponse;
}
