/** /mcp — MCP 连接器市场 (BL-ARCH1 5/10).
 *
 * 列出所有可订阅的 MCP 连接器, 按部门权限过滤. 订阅后跳 Companion (本机装).
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import {
  listConnectors,
  subscribe,
  type McpConnector,
} from "../lib/mcp";

export function McpMarketPage() {
  return (
    <Routes>
      <Route index element={<McpList />} />
      <Route path=":id" element={<McpDetail />} />
    </Routes>
  );
}

function McpList() {
  const [connectors, setConnectors] = useState<McpConnector[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listConnectors()
      .then(setConnectors)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div>加载中…</div>;
  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;

  const allowed = connectors.filter((c) => c.status === "active");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card title={`MCP 连接器市场 · ${allowed.length} 个可用`}>
        <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
          MCP (Model Context Protocol) 是 Anthropic 开放标准. 每个连接器接通一类
          外部工具 (Jira / GitLab / 飞书 / 文件系统 / 时间). 订阅后桌面 Companion
          自动装, 小鲶就能调那些工具帮你查 / 操作.
        </div>
      </Card>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "var(--space-3)",
        }}
      >
        {allowed.map((c) => (
          <Link
            key={c.id}
            to={`/mcp/${c.id}`}
            style={{
              background: "var(--bg-elev)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              padding: "var(--space-4)",
              textDecoration: "none",
              color: "var(--text)",
            }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.borderColor = "var(--accent)")
            }
            onMouseLeave={(e) =>
              (e.currentTarget.style.borderColor = "var(--border)")
            }
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: "var(--space-2)",
              }}
            >
              <span style={{ fontWeight: 500 }}>{c.name}</span>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                v{c.version}
              </span>
            </div>
            <div
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                marginBottom: "var(--space-2)",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {c.description}
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <span>
                {c.tools.length} 个工具 · {c.auth_type}
              </span>
              {c.is_subscribed ? (
                <span style={{ color: "var(--status-ok)" }}>✓ 已订阅</span>
              ) : (
                <span>未订阅</span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function McpDetail() {
  const { id } = useParams<{ id: string }>();
  const [connector, setConnector] = useState<McpConnector | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    listConnectors()
      .then((cs) => {
        const c = cs.find((x) => x.id === id);
        if (!c) setError(`连接器 ${id} 不存在`);
        else setConnector(c);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  if (error) return <div style={{ color: "var(--status-err)" }}>{error}</div>;
  if (!connector) return <div>加载中…</div>;

  const handleSubscribe = async () => {
    setBusy(true);
    const r = await subscribe(connector.id);
    setBusy(false);
    if (!r.ok) {
      setMsg(`订阅失败: ${r.error}`);
      return;
    }
    if (r.next_step === "ready") {
      setMsg("✓ 已订阅. 打开桌面 Companion 重启对话即可使用.");
    } else if (r.next_step === "oauth_required" && r.authorize_url) {
      window.open(r.authorize_url, "_blank");
      setMsg("→ 已开 OAuth 授权页, 完成后回来刷新");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title={connector.name}
        action={
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            v{connector.version} · {connector.provider}
          </span>
        }
      >
        <div style={{ marginBottom: "var(--space-3)" }}>{connector.description}</div>

        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-2)",
            marginBottom: "var(--space-3)",
            fontSize: 12,
          }}
        >
          <Tag label={`auth: ${connector.auth_type}`} />
          <Tag
            label={
              connector.allowed_dept.length > 0
                ? `限部门: ${connector.allowed_dept.join(", ")}`
                : "全员可订阅"
            }
          />
          <Tag label={`${connector.tools.length} 个工具`} />
          {connector.subscriber_count !== undefined && (
            <Tag label={`${connector.subscriber_count} 人订阅`} />
          )}
        </div>

        <div>
          {!connector.is_subscribed ? (
            <button
              onClick={handleSubscribe}
              disabled={busy}
              style={{
                background: "var(--accent)",
                color: "white",
                border: "none",
                padding: "8px 20px",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                fontSize: 13,
              }}
            >
              {busy ? "订阅中…" : "订阅"}
            </button>
          ) : (
            <span style={{ color: "var(--status-ok)" }}>✓ 已订阅</span>
          )}
          {msg && (
            <span
              style={{
                marginLeft: "var(--space-3)",
                fontSize: 12,
                color: "var(--text-muted)",
              }}
            >
              {msg}
            </span>
          )}
        </div>
      </Card>

      <Card title={`工具列表 (${connector.tools.length})`}>
        <ul
          style={{
            margin: 0,
            paddingLeft: 20,
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-2)",
            fontSize: 13,
          }}
        >
          {connector.tools.map((t) => (
            <li key={t.name}>
              <code style={{ background: "var(--bg-secondary)", padding: "1px 6px", borderRadius: 3 }}>
                {t.name}
              </code>
              <span style={{ marginLeft: 8, color: "var(--text-muted)" }}>
                — {t.description}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Tag({ label }: { label: string }) {
  return (
    <span
      style={{
        background: "var(--bg-secondary)",
        padding: "2px 8px",
        borderRadius: "var(--radius-sm)",
        color: "var(--text-muted)",
      }}
    >
      {label}
    </span>
  );
}
