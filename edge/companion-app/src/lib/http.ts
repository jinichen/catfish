/**
 * 直接打 gateway HTTP 的客户端（用于前端不经过 Rust 后端的场景）。
 *
 * 大部分时候应该走 lib/tauri.ts 的 `fetchCatalog/fetchHealthz`（Rust 转发，统一错误处理）。
 * 这层留给"前端独立轮询、Rust 不必参与"的场景，比如纯展示用的 /v1/models 列表。
 */

const DEFAULT_BASE = "http://127.0.0.1:8999";

export class GatewayHttp {
  constructor(private baseUrl: string = DEFAULT_BASE) {}

  async get<T>(path: string): Promise<T> {
    const res = await fetch(this.baseUrl + path);
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}: ${path}`);
    }
    return res.json();
  }

  async post<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(this.baseUrl + path, {
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
