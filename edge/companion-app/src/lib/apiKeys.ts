/** 个人长期钥匙 (11/19, 给 Claude Code 等直连中央用)。中央网关 /api/me/api-keys → 身份服务。
 *  钥匙原文只在生成那一刻返回一次; 列表里只有末 4 位。 */
import { config } from "./env";
import { fetchWithAuth } from "./me";

export interface ApiKeyInfo { id: string; name: string; hint: string; created_at: number; last_used_at: number | null }
export interface NewApiKey extends ApiKeyInfo { key: string }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetchWithAuth(`${config.gatewayUrl}/api/me/api-keys${path}`, init);
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try { msg = (await resp.json()).detail || msg; } catch { /* 不是 JSON */ }
    throw new Error(resp.status === 404 && path === "" ? "中央还没升级到支持长期钥匙的版本 (20261119 之后)" : msg);
  }
  return (await resp.json()) as T;
}

export const listApiKeys = () => call<{ keys: ApiKeyInfo[] }>("").then((r) => r.keys);
export const createApiKey = (name: string) =>
  call<NewApiKey>("", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name }) });
export const revokeApiKey = (id: string) => call<{ revoked: string }>(`/${encodeURIComponent(id)}`, { method: "DELETE" });

/** Claude Code 的接法 (环境变量)。中央模型名用 catfish-auto = 单位配的默认对话模型。 */
export function claudeCodeEnv(key: string, gatewayUrl = config.gatewayUrl): string {
  const base = gatewayUrl.replace(/\/+$/, "");
  return [
    `export ANTHROPIC_BASE_URL=${base}`,
    `export ANTHROPIC_AUTH_TOKEN=${key}`,
    "export ANTHROPIC_MODEL=catfish-auto",
    "export ANTHROPIC_SMALL_FAST_MODEL=catfish-auto",
  ].join("\n");
}
