/** /mcp — MCP 连接器市场 (BL-ARCH1 5/10).
 *
 * 列出所有可订阅的 MCP 连接器, 按部门权限过滤。
 *
 * 10/22: 订阅后员工自己电脑上的工具桥 (hermes 起的 catfish_tool_bridge.mcp_server) 每 2 分钟
 * 对一次订阅, 自动接上 / 停掉连接器并通知小鲶刷新工具 —— 不用装、不用重启。在这之前订阅只在
 * 中央留一条记录, 员工机从没拉过, 而且这页本身也跟 registry 的返回对不上 (见 lib/mcp.ts)。
 * 只列 status=active 的; 要 OAuth 的 (jira / gitlab) 员工机还接不了, 是 preview, 不列。
 *
 * 11/08 分享治理 §3.4: 员工能「提交连接器」(贴 manifest → 待审核); 列表页多「我提交的」和
 * 主管 / 管理员的「待我审核」(McpReviewSections); 表单在 McpSubmitForm。
 */

import { useEffect, useState } from "react";
import { Link, Route, Routes, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { Badge, BTN_PRIMARY, DataTable, Section, Toolbar, type Column } from "../components/DataTable";
import { PageShell } from "../components/PageShell";
import { roleAllows } from "../components/RoleGate";
import {
  listConnectors,
  listMySubscriptions,
  subscribe,
  unsubscribe,
  type McpConnector,
  type McpSubmission,
} from "../lib/mcp";
import { useAuthStore } from "../store/auth";
import { FilterBar, NameCell, SubPage, tabLabel, useRefreshMarketCounts } from "./marketShared";
import { McpReviewDialog, mineColumns, pendingColumns, useMcpReviews } from "./McpReviewSections";
import { McpSubmitForm } from "./McpSubmitForm";

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
      <Route path="submit" element={<SubPage back="/market/mcp" section="连接器" title="提交"><McpSubmitForm /></SubPage>} />
      <Route path=":id" element={<SubPage back="/market/mcp" section="连接器" title="详情"><McpDetail /></SubPage>} />
    </Routes>
  );
}

type Tab = "market" | "review" | "mine";

function McpList() {
  const me = useAuthStore((s) => s.me);
  const reviewer = !!me && roleAllows(me.role, "manager");
  const refreshCounts = useRefreshMarketCounts();
  const [connectors, setConnectors] = useState<McpConnector[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<Tab>("market");
  const [reviewing, setReviewing] = useState<McpSubmission | null>(null);
  const rv = useMcpReviews(reviewer);

  useEffect(() => {
    listConnectors()
      .then(setConnectors)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  const needle = q.trim().toLowerCase();
  const hit = (...vals: Array<string | null | undefined>) => !needle || vals.some((v) => (v || "").toLowerCase().includes(needle));
  const allowed = connectors.filter((c) => c.status === "active");
  const market = allowed.filter((c) => hit(c.name, c.id, c.description, ...c.tools.map((t) => t.name)));
  const pending = (rv.pending ?? []).filter((s) => hit(s.name, s.id, s.description, s.submitted_by));
  const mine = (rv.mine ?? []).filter((s) => hit(s.name, s.id, s.description));

  const tabs = [
    { key: "market" as const, label: tabLabel("可订阅", loading ? null : allowed.length) },
    ...(reviewer ? [{ key: "review" as const, label: tabLabel("待我审核", rv.pending?.length) }] : []),
    ...(rv.mine?.length ? [{ key: "mine" as const, label: tabLabel("我提交的", rv.mine.length) }] : []),
  ];
  const current = tabs.some((t) => t.key === tab) ? tab : "market";

  const cols: Column<McpConnector>[] = [
    { header: "连接器", cell: (c) => <NameCell name={<Link to={`/market/mcp/${c.id}`}>{c.name}</Link>} sub={c.description} /> },
    { header: "工具", width: 60, align: "right", cell: (c) => c.tools.length },
    { header: "授权", width: "14%", truncate: true, cell: (c) => AUTH_LABEL[c.auth_type] ?? c.auth_type },
    { header: "版本", width: 70, nowrap: true, cell: (c) => `v${c.version}` },
    {
      header: "订阅", width: 80, cell: (c) =>
        c.subscribed ? <Badge tone="ok">已订阅</Badge> : <Link to={`/market/mcp/${c.id}`} style={{ fontSize: 12 }}>去订阅</Link>,
    },
  ];

  const hint =
    current === "review"
      ? "你管的部门 (管理员: 全公司范围) 有人提交的连接器。看清楚它连的是什么系统、暴露哪些工具, 通过后同事才能订阅。"
      : current === "mine"
        ? "待审核的要等部门主管 (全公司范围: 管理员) 通过后同事才能订阅; 被驳回的看原因改好再提交。"
        : `每个连接器给小鲶接上一类工具, 点名字进详情订阅。${EFFECT_NOTE}`;

  return (
    <PageShell scroll="data">
      <Toolbar title="连接器">
        <Link to="/market/mcp/submit" style={{ ...BTN_PRIMARY, textDecoration: "none" }}>
          + 提交连接器
        </Link>
      </Toolbar>
      <FilterBar tabs={tabs} tab={current} onTab={setTab} q={q} onQ={setQ} placeholder="搜连接器名 / 说明 / 工具…" hint={hint} />
      {(error || rv.error) && <div style={{ color: "var(--status-err)", fontSize: 12 }}>{error || rv.error}</div>}
      <Section fill>
        {current === "market" && (
          <DataTable fill columns={cols} rows={market} rowKey={(c) => c.id}
                     empty={loading ? "加载中…" : allowed.length === 0 ? "还没有可订阅的连接器" : "没有匹配的连接器"} />
        )}
        {current === "review" && (
          <DataTable fill columns={pendingColumns(setReviewing)} rows={pending} rowKey={(s) => `${s.id}@${s.version}`} empty="没有待审核的。" />
        )}
        {current === "mine" && (
          <DataTable fill columns={mineColumns()} rows={mine} rowKey={(s) => `${s.id}@${s.version}`} empty="没有匹配的" />
        )}
      </Section>
      {reviewing && (
        <McpReviewDialog
          s={reviewing}
          onClose={() => setReviewing(null)}
          onDone={() => {
            setReviewing(null);
            void rv.reload();
            refreshCounts();
          }}
        />
      )}
    </PageShell>
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
