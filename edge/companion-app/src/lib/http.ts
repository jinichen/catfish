/**
 * 直接打 gateway HTTP 的客户端（用于前端不经过 Rust 后端的场景）。
 *
 * 大部分时候应该走 lib/tauri.ts 的 `fetchCatalog/fetchHealthz`（Rust 转发，统一错误处理）。
 * 这层留给"前端独立轮询、Rust 不必参与"的场景，比如纯展示用的 /v1/models 列表。
 *
 * 默认 base 从 lib/env.ts 拿 (VITE_CATFISH_GATEWAY_URL 等 env 控制), 不再
 * 硬编码 127.0.0.1:8999。
 */

import { config } from "./env";
// BL-CSP-PROXY (7/18 鸿波): 走 Rust reqwest 代理, CSP connect-src 严格.
import { fetchViaProxy } from "./http_proxy";

export class GatewayHttp {
  // BL-AUTH-DECOUPLE-A5 (5/19): 改读 backendUrl (hermes proxy 启用时 = hermes URL).
  // 这个 client 主要做匿名 GET (e.g. /v1/catalog), hermes 也透传, 无需改 header.
  constructor(private baseUrl: string = config.backendUrl) {}

  async get<T>(path: string): Promise<T> {
    const res = await fetchViaProxy(this.baseUrl + path);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${path}`);
    }
    return res.json();
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetchViaProxy(this.baseUrl + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${path}`);
    }
    return res.json();
  }
}

export const gatewayHttp = new GatewayHttp();
