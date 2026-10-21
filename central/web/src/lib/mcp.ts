/** MCP Registry API 客户端 (BL-ARCH1 5/10).
 *
 * 走 gateway /v1/mcp/* 反代到 mcp-registry :8996.
 */

import { api } from "./api";

export interface McpTool {
  name: string;
  description: string;
}

export interface McpConnector {
  id: string;
  name: string;
  version: string;
  description: string;
  provider: string;
  status: string;
  allowed_dept: string[];
  auth_type: string;
  tools: McpTool[];
  ui?: Record<string, unknown>;
  subscriber_count?: number;
  /** 当前员工订了没 (registry 的字段名就叫 subscribed; 10/22 前这里写成 is_subscribed, 永远读不到)。 */
  subscribed?: boolean;
}

export interface ConnectorListResponse {
  connectors: McpConnector[];
}

export async function listConnectors(): Promise<McpConnector[]> {
  const r = await api.get<ConnectorListResponse>("/v1/mcp/registry");
  return r.connectors || [];
}

export interface SubscribeResponse {
  ok: boolean;
  next_step?: "ready" | "oauth";
  subscription_id?: string;
  error?: string;
}

/** registry 实际返回的形状 (POST /v1/mcp/subscribe)。 */
interface RegistrySubscribeResponse {
  subscription: { id: string; status: string };
  next_step: "ready" | "oauth";
}

export async function subscribe(connectorId: string): Promise<SubscribeResponse> {
  try {
    // 10/22: registry 返 {subscription, next_step}, 没有 ok 字段 —— 原来直接当 SubscribeResponse
    // 用, `!r.ok` 恒真, 订阅成功了页面也报「订阅失败: undefined」。
    const r = await api.post<RegistrySubscribeResponse>("/v1/mcp/subscribe", {
      connector_id: connectorId,
    });
    return { ok: true, next_step: r.next_step, subscription_id: r.subscription.id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function unsubscribe(subscriptionId: string): Promise<{ ok: boolean; error?: string }> {
  try {
    // registry 返被撤销的那条订阅 (status=revoked), 不是 {ok}
    await api.delete(`/v1/mcp/subscribe/${subscriptionId}`);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function listMySubscriptions(): Promise<{
  subscriptions: Array<{ id: string; connector_id: string; status: string }>;
}> {
  return api.get("/v1/mcp/subscribed");
}
