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
  is_subscribed?: boolean;
  subscription_id?: string;
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
  next_step?: "ready" | "oauth_required";
  authorize_url?: string;
  subscription_id?: string;
  error?: string;
}

export async function subscribe(connectorId: string): Promise<SubscribeResponse> {
  try {
    return await api.post<SubscribeResponse>("/v1/mcp/subscribe", {
      connector_id: connectorId,
    });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export async function unsubscribe(subscriptionId: string): Promise<{ ok: boolean }> {
  try {
    return await api.delete<{ ok: boolean }>(
      `/v1/mcp/subscribe/${subscriptionId}`,
    );
  } catch {
    return { ok: false };
  }
}

export async function listMySubscriptions(): Promise<{
  subscriptions: Array<{ id: string; connector_id: string; status: string }>;
}> {
  return api.get("/v1/mcp/subscribed");
}
