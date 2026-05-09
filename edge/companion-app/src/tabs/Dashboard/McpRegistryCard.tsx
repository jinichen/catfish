/** Dashboard 卡 — MCP 连接器仓库 (BL-D3 Phase 1, 5/9 ship)
 *
 * 列出可用 MCP 连接器 (按部门权限过滤). Phase 1 只读展示,
 * Phase 2 接订阅按钮 + OAuth flow, Phase 3 真接 mcp pod.
 *
 * 走 catfish-gateway 转发 GET /v1/mcp/registry. gateway 透传
 * X-Catfish-User-Dept (从 JWT 抽出) → mcp-registry 部门过滤.
 *
 * 跟 SkillsMcpCard 区别:
 *   - SkillsMcpCard: 列鲶鱼自带 skill / 当前会话已注入的 mcp tool (运行时)
 *   - McpRegistryCard (本卡): 列**企业可订阅**的连接器目录 (静态目录)
 */

import { useEffect, useState } from "react";
import { config } from "../../lib/env";

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

  const refresh = async () => {
    try {
      // gateway 转发到 mcp-registry. 路径走 /v1/mcp/registry, gateway 已配
      // 反向代理 (同 BL-D3 Phase 1 计划). dev 时如果 gateway 没配, 直连
      // 127.0.0.1:8997 兜底.
      const url = `${config.gatewayUrl}/v1/mcp/registry`;
      const res = await fetch(url, {
        credentials: "include",
      });
      if (!res.ok) {
        // 404 / 502 都意味着 mcp-registry 没启或 gateway 没配反代
        if (res.status === 404 || res.status === 502) {
          setError("mcp-registry 未启动 (Phase 1 dev: python -m catfish_mcp_registry.app)");
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
              Phase 1 · 只读目录
            </span>
          </h3>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            企业可订阅的 MCP 连接器 (Jira / GitLab / Filesystem / ...) — Phase 2 接订阅按钮
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
                      <button
                        type="button"
                        disabled
                        style={{
                          width: "100%",
                          padding: "4px 8px",
                          border: "1px solid var(--catfish-border)",
                          borderRadius: 4,
                          background: "var(--catfish-bg-elevated)",
                          color: "var(--catfish-text-muted)",
                          fontSize: 11,
                          cursor: "not-allowed",
                          opacity: 0.6,
                        }}
                        title="Phase 2 (5/22+) 才接通"
                      >
                        订阅 (Phase 2 接通)
                      </button>
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
