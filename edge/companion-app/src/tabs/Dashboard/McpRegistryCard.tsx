/** Dashboard 卡 — MCP 连接器仓库 (BL-D3 Phase 1+2, 5/9 ship)
 *
 * 列出可用 MCP 连接器 (按部门权限过滤). Phase 2 (5/9 加) 加订阅 / OAuth flow.
 *
 * Phase 1: GET /v1/mcp/registry 列连接器
 * Phase 2: POST /v1/mcp/subscribe (auth_type=none → 直 active; oauth2 → 跳浏览器)
 *          POST /v1/mcp/oauth/start → authorize_url
 *          POST /v1/mcp/oauth/callback → 写 secret-broker → active
 *          DELETE /v1/mcp/subscribe/{id} → revoked
 * Phase 3 (后续): Agent 动态加载 + pod-per-user
 *
 * 走 catfish-gateway 转发, gateway 注入 X-Catfish-User-Dept/Sub.
 */

import { useEffect, useState } from "react";
import { config } from "../../lib/env";
// BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): 改走 fetchWithAuth, 不再 inline getToken +
// raw fetch — wrapper 内部按 useHermes 切 API_SERVER_KEY / OAuth, 加 X-Catfish-User.
import { fetchWithAuth } from "../../lib/me";

interface ConnectorTool {
  name: string;
  description: string;
}

interface ConnectorUi {
  icon: string;
  category: string;
  vendor: string;
  homepage: string;
}

interface ConnectorListItem {
  id: string;
  name: string;
  version: string;
  description: string;
  provider: string;
  status: string;
  allowed_dept: string[];
  auth_type: string;
  tools: ConnectorTool[];
  ui: ConnectorUi;
  subscribed: boolean;
  subscriber_count: number;
}

interface RegistryResponse {
  connectors: ConnectorListItem[];
  total: number;
  user_dept: string;
  filtered_by_dept: boolean;
}

const STATUS_BADGE_BG: Record<string, string> = {
  active: "var(--catfish-cyan)",
  preview: "#f59e0b",
  deprecated: "#6b7280",
};

const AUTH_TYPE_LABEL: Record<string, string> = {
  oauth2: "OAuth2 授权",
  path_allowlist: "路径白名单",
  api_key: "API Key",
  none: "免授权",
};

export default function McpRegistryCard() {
  const [data, setData] = useState<RegistryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const showFlash = (msg: string, ms = 3000) => {
    setFlash(msg);
    window.setTimeout(() => setFlash(null), ms);
  };

  const refresh = async () => {
    try {
      const url = `${config.backendUrl}/v1/mcp/registry`;
      const res = await fetchWithAuth(url);
      if (!res.ok) {
        if (res.status === 502) {
          setError("mcp-registry 未启动 (dev: python -m catfish_mcp_registry.app, port 8996)");
        } else if (res.status === 401) {
          setError("鉴权失败 — 请重新登录");
        } else {
          setError(`HTTP ${res.status}`);
        }
        return;
      }
      const json = (await res.json()) as RegistryResponse;
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 60_000);
    return () => clearInterval(id);
  }, []);

  const subscribe = async (c: ConnectorListItem, ev: React.MouseEvent) => {
    ev.stopPropagation();
    setBusyId(c.id);
    try {
      // BL-AUTH-DECOUPLE-A5 (5/19): fetchWithAuth 自动加 Authorization + X-Catfish-User.
      const jsonHeaders = { "Content-Type": "application/json" };
      const res = await fetchWithAuth(`${config.backendUrl}/v1/mcp/subscribe`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ connector_id: c.id }),
      });
      if (!res.ok) {
        showFlash(`订阅失败: HTTP ${res.status}`);
        return;
      }
      const json = (await res.json()) as {
        next_step: "oauth" | "ready";
        subscription: { id: string };
      };
      if (json.next_step === "ready") {
        showFlash(`已订阅 ${c.name}`);
        await refresh();
        return;
      }
      const startRes = await fetchWithAuth(`${config.backendUrl}/v1/mcp/oauth/start`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ subscription_id: json.subscription.id }),
      });
      if (!startRes.ok) {
        showFlash(`OAuth start 失败: HTTP ${startRes.status}`);
        return;
      }
      const startJson = (await startRes.json()) as { authorize_url: string; state: string };
      if (startJson.authorize_url.includes("mock-callback")) {
        const cbRes = await fetchWithAuth(`${config.backendUrl}/v1/mcp/oauth/callback`, {
          method: "POST",
          headers: jsonHeaders,
          body: JSON.stringify({
            state: startJson.state,
            code: "mock-code",
            mock_token: `mock-${c.id}-token`,
          }),
        });
        if (cbRes.ok) {
          showFlash(`已订阅 ${c.name} (mock OAuth 完成)`);
          await refresh();
        } else {
          showFlash(`OAuth callback 失败: HTTP ${cbRes.status}`);
        }
        return;
      }
      window.open(startJson.authorize_url, "_blank");
      showFlash(`授权窗口已打开, 完成后自动激活`);
    } catch (e) {
      showFlash(`订阅出错: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  const unsubscribe = async (c: ConnectorListItem, ev: React.MouseEvent) => {
    ev.stopPropagation();
    setBusyId(c.id);
    try {
      // BL-AUTH-DECOUPLE-A5 (5/19): fetchWithAuth 自动加 Authorization + X-Catfish-User.
      const subsRes = await fetchWithAuth(`${config.backendUrl}/v1/mcp/subscribed`);
      if (!subsRes.ok) {
        showFlash(`查我的订阅失败: HTTP ${subsRes.status}`);
        return;
      }
      const subsJson = (await subsRes.json()) as {
        subscriptions: { id: string; connector_id: string; status: string }[];
      };
      const mine = subsJson.subscriptions.find(
        (s) => s.connector_id === c.id && s.status === "active",
      );
      if (!mine) {
        showFlash(`未找到 ${c.name} 的活跃订阅`);
        return;
      }
      const r = await fetchWithAuth(
        `${config.backendUrl}/v1/mcp/subscribe/${mine.id}`,
        { method: "DELETE" },
      );
      if (r.ok) {
        showFlash(`已取消订阅 ${c.name}`);
        await refresh();
      } else {
        showFlash(`取消失败: HTTP ${r.status}`);
      }
    } catch (e) {
      showFlash(`取消出错: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div
      style={{
        gridColumn: "1 / -1",
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
            🔌 MCP 连接器
            <span
              style={{
                fontSize: 10,
                fontWeight: 400,
                color: "var(--catfish-text-muted)",
                padding: "2px 6px",
                background: "var(--catfish-bg)",
                borderRadius: 4,
              }}
            >
              Phase 2 · 订阅可用
            </span>
          </h3>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            企业可订阅的 MCP 连接器 (Jira / GitLab / Filesystem / Time) — 展开订阅
          </span>
        </div>
        <button
          type="button"
          onClick={refresh}
          title="刷新"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--catfish-text-muted)",
            fontSize: 11,
            cursor: "pointer",
            padding: 4,
          }}
        >
          ↻
        </button>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          读取失败: {error}
        </div>
      )}

      {flash && (
        <div
          style={{
            color: "var(--catfish-cyan)",
            fontSize: 12,
            marginBottom: 8,
            padding: "4px 8px",
            background: "var(--catfish-bg)",
            border: "1px dashed var(--catfish-cyan)",
            borderRadius: 4,
          }}
        >
          {flash}
        </div>
      )}

      {!data && !error && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>读取中…</div>
      )}

      {data && data.connectors.length === 0 && !error && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            padding: "8px 12px",
            border: "1px dashed var(--catfish-border)",
            borderRadius: 6,
          }}
        >
          {data.filtered_by_dept
            ? `当前部门 (${data.user_dept || "未识别"}) 无可订阅连接器. 联系管理员加部门白名单.`
            : "暂无可用连接器. (manifests/ 目录是空的, 或 gateway 没配反代到 mcp-registry)"}
        </div>
      )}

      {data && data.connectors.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {data.connectors.map((c) => {
            const expanded = expandedId === c.id;
            return (
              <div
                key={c.id}
                style={{
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 6,
                  padding: "8px 12px",
                  fontSize: 12,
                  background: "var(--catfish-bg)",
                  cursor: "pointer",
                }}
                onClick={() => setExpandedId(expanded ? null : c.id)}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
                    <span style={{ fontWeight: 600 }}>{c.name}</span>
                    <span
                      style={{
                        fontSize: 10,
                        color: "var(--catfish-bg)",
                        background: STATUS_BADGE_BG[c.status] ?? "#6b7280",
                        padding: "1px 6px",
                        borderRadius: 3,
                      }}
                    >
                      {c.status}
                    </span>
                    <span style={{ fontSize: 10, color: "var(--catfish-text-muted)" }}>
                      v{c.version} · {c.ui.category || c.provider}
                    </span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {c.subscribed && (
                      <span
                        style={{
                          fontSize: 10,
                          color: "var(--catfish-bg)",
                          background: "var(--catfish-cyan)",
                          padding: "1px 6px",
                          borderRadius: 3,
                          fontWeight: 600,
                        }}
                      >
                        ✓ 已订阅
                      </span>
                    )}
                    <span
                      style={{
                        fontSize: 10,
                        color: "var(--catfish-text-muted)",
                        padding: "1px 6px",
                        background: "var(--catfish-bg-elevated)",
                        borderRadius: 3,
                      }}
                    >
                      {AUTH_TYPE_LABEL[c.auth_type] ?? c.auth_type}
                    </span>
                    <span
                      style={{
                        fontSize: 10,
                        color: "var(--catfish-text-muted)",
                      }}
                    >
                      {c.tools.length} tools
                    </span>
                    <span style={{ fontSize: 10, opacity: 0.5 }}>{expanded ? "▼" : "▶"}</span>
                  </div>
                </div>

                {expanded && (
                  <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--catfish-border)" }}>
                    <div style={{ color: "var(--catfish-text-muted)", marginBottom: 6 }}>
                      {c.description}
                    </div>
                    <div style={{ marginBottom: 6 }}>
                      <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
                        部门权限:{" "}
                      </span>
                      {c.allowed_dept.length === 0 ? (
                        <span style={{ fontSize: 11 }}>全员可订阅</span>
                      ) : (
                        c.allowed_dept.map((d) => (
                          <span
                            key={d}
                            style={{
                              fontSize: 10,
                              padding: "1px 6px",
                              background: "var(--catfish-bg-elevated)",
                              borderRadius: 3,
                              marginRight: 4,
                            }}
                          >
                            {d}
                          </span>
                        ))
                      )}
                    </div>
                    <div>
                      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11, marginBottom: 4 }}>
                        Tools ({c.tools.length}):
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        {c.tools.map((t) => (
                          <div
                            key={t.name}
                            style={{
                              fontSize: 11,
                              fontFamily: "var(--font-mono)",
                              paddingLeft: 8,
                            }}
                          >
                            <span style={{ color: "var(--catfish-cyan)" }}>{t.name}</span>
                            <span style={{ color: "var(--catfish-text-muted)" }}> — {t.description}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div style={{ marginTop: 8, paddingTop: 6, borderTop: "1px dashed var(--catfish-border)" }}>
                      {c.subscribed ? (
                        <button
                          type="button"
                          onClick={(ev) => unsubscribe(c, ev)}
                          disabled={busyId === c.id}
                          style={{
                            width: "100%",
                            padding: "4px 8px",
                            border: "1px solid var(--status-err)",
                            borderRadius: 4,
                            background: "transparent",
                            color: "var(--status-err)",
                            fontSize: 11,
                            cursor: busyId === c.id ? "wait" : "pointer",
                            opacity: busyId === c.id ? 0.5 : 1,
                          }}
                        >
                          {busyId === c.id ? "处理中…" : "✕ 取消订阅"}
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={(ev) => subscribe(c, ev)}
                          disabled={busyId === c.id}
                          style={{
                            width: "100%",
                            padding: "4px 8px",
                            border: "1px solid var(--catfish-cyan)",
                            borderRadius: 4,
                            background: "var(--catfish-cyan)",
                            color: "var(--catfish-bg)",
                            fontSize: 11,
                            cursor: busyId === c.id ? "wait" : "pointer",
                            opacity: busyId === c.id ? 0.5 : 1,
                            fontWeight: 600,
                          }}
                          title={
                            c.auth_type === "oauth2" || c.auth_type === "api_key"
                              ? "需 OAuth 授权 (Phase 2 mock 模式 dev: 自动完成)"
                              : "免授权连接器 (path_allowlist / none), 直接订阅"
                          }
                        >
                          {busyId === c.id
                            ? "订阅中…"
                            : c.auth_type === "oauth2"
                            ? "订阅 (需授权)"
                            : "订阅"}
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {data && (
        <div
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            textAlign: "center",
            marginTop: 8,
          }}
        >
          共 {data.total} 个 ·{" "}
          {data.filtered_by_dept
            ? `按部门 (${data.user_dept}) 过滤`
            : "未识别部门, 仅显示全员开放"}{" "}
          · Phase 1 (只读 / 60s 自动刷新)
        </div>
      )}
    </div>
  );
}
