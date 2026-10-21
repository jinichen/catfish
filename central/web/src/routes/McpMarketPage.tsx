/** /mcp — MCP 连接器市场 (BL-ARCH1 5/10).
 *
 * 列出所有可订阅的 MCP 连接器, 按部门权限过滤。
 *
 * 10/22: 订阅后员工自己电脑上的工具桥 (hermes 起的 catfish_tool_bridge.mcp_server) 每 2 分钟
 * 对一次订阅, 自动接上 / 停掉连接器并通知小鲶刷新工具 —— 不用装、不用重启。在这之前订阅只在
 * 中央留一条记录, 员工机从没拉过, 而且这页本身也跟 registry 的返回对不上 (见 lib/mcp.ts)。
 * 只列 status=active 的; 要 OAuth 的 (jira / gitlab) 员工机还接不了, 是 preview, 不列。
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import {
  listConnectors,
  listMySubscriptions,
  subscribe,
  unsubscribe,
  type McpConnector,
} from "../lib/mcp";

const AUTH_LABEL: Record<string, string> = {
  none: "免授权",
  path_allowlist: "限定目录",
  oauth2: "要授权登录",
  api_key: "要 API Key",
};

const EFFECT_NOTE =
  "订阅只对你自己生效：你电脑上的 Companion 开着并已登录时，约 2 分钟内小鲶就会多出这些工具，不用安装、不用重启；退订同样。";

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
        <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.7 }}>
          每个连接器给小鲶接上一类工具。点开卡片，在详情里订阅。{EFFECT_NOTE}
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
            to={`/market/mcp/${c.id}`}
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
                {c.tools.length} 个工具 · {AUTH_LABEL[c.auth_type] ?? c.auth_type}
              </span>
              {c.subscribed ? (
                <span style={{ color: "var(--status-ok)" }}>✓ 已订阅</span>
              ) : (
                <span style={{ color: "var(--accent)" }}>点开订阅 →</span>
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
  // 10/22: 撤下的 (filesystem) / 还没开放的 (jira、gitlab 是 preview) 从老书签或没刷新的列表点进来,
  // 原来只有一行红字「连接器 filesystem 不存在」, 没说为什么, 也没路回去。
  const [unavailable, setUnavailable] = useState<"gone" | "preview" | null>(null);

  useEffect(() => {
    listConnectors()
      .then((cs) => {
        const c = cs.find((x) => x.id === id);
        if (!c) setUnavailable("gone");
        else if (c.status !== "active") setUnavailable("preview");
        else setConnector(c);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  const back = (
    <Link to="/market/mcp" style={{ color: "var(--accent)", fontSize: 13 }}>
      ← 回到连接器列表
    </Link>
  );
  if (unavailable) {
    return (
      <Card title={unavailable === "gone" ? "这个连接器已经下架" : "这个连接器还没开放订阅"}>
        <div style={{ color: "var(--text-muted)", fontSize: 13, lineHeight: 1.7, marginBottom: "var(--space-3)" }}>
          {unavailable === "gone"
            ? `「${id}」不在市场里了（可能是旧链接，或列表页打开得早、还没刷新）。已经订过的不用处理，员工电脑不会再启动它。`
            : `「${id}」要先授权登录，员工电脑这一步还没做好，暂时不能订阅。`}
        </div>
        {back}
      </Card>
    );
  }
  if (error) {
    return (
      <div>
        <div style={{ color: "var(--status-err)", marginBottom: "var(--space-2)" }}>加载失败：{error}</div>
        {back}
      </div>
    );
  }
  if (!connector) return <div>加载中…</div>;

  const handleSubscribe = async () => {
    setBusy(true);
    const r = await subscribe(connector.id);
    setBusy(false);
    if (!r.ok) {
      setMsg(`订阅失败：${r.error ?? "未知错误"}`);
      return;
    }
    setConnector({ ...connector, subscribed: true, subscriber_count: (connector.subscriber_count ?? 0) + 1 });
    setMsg(
      r.next_step === "ready"
        ? "✓ 已订阅。约 2 分钟内小鲶就能用上。"
        : "已登记，但这个连接器还要授权登录，员工电脑这一步暂不支持。",
    );
  };

  const handleUnsubscribe = async () => {
    setBusy(true);
    try {
      const mine = (await listMySubscriptions()).subscriptions.find(
        (s) => s.connector_id === connector.id && s.status !== "revoked",
      );
      const r = mine ? await unsubscribe(mine.id) : { ok: false, error: "没找到你的这条订阅" };
      if (!r.ok) {
        setMsg(`退订失败：${r.error ?? "未知错误"}`);
        return;
      }
      setConnector({ ...connector, subscribed: false, subscriber_count: Math.max(0, (connector.subscriber_count ?? 1) - 1) });
      setMsg("已退订。约 2 分钟内小鲶就不再用它。");
    } catch (e) {
      setMsg(`退订失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
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
          <Tag label={AUTH_LABEL[connector.auth_type] ?? connector.auth_type} />
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

        <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: "var(--space-3)", lineHeight: 1.7 }}>
          {EFFECT_NOTE}
        </div>
        <div>
          {!connector.subscribed ? (
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
            <>
              <span style={{ color: "var(--status-ok)" }}>✓ 已订阅</span>
              <button
                onClick={() => void handleUnsubscribe()}
                disabled={busy}
                style={{
                  marginLeft: "var(--space-3)",
                  background: "transparent",
                  border: "1px solid var(--border)",
                  color: "var(--text-muted)",
                  padding: "4px 12px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {busy ? "处理中…" : "退订"}
              </button>
            </>
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
