/**
 * MCP 连接器的「审批人试用订阅」(11/08, docs/SHARE-GOVERNANCE-PLAN.md §2.3 / §3.4):
 *   PendingMcpSection —— 待我审的连接器 (GET /v1/mcp/review) + 「试用订阅」(POST /v1/mcp/subscribe {trial: true})
 *   试用订阅的判定 / 自动退订 —— 中央的订阅记录不带 trial 标 (§6.4 的 SubscriptionView 没这个字段), 所以本机记一份
 *     connector_id → subscription_id (localStorage); 兜底: 订阅的连接器不在已上架列表里 = 试用。
 *     刷新时发现那份提交不在待审了 → DELETE /v1/mcp/subscribe/{id} 自动退订。
 *
 * 没写「测试账号怎么拿」的提交, 中央 403 —— 原话显示给审批人 (不允许拿自己的生产凭据试别人交的连接器)。
 */
import { useEffect, useState } from "react";

import { config } from "../../lib/env";
import { fetchWithAuth } from "../../lib/me";
import { muted } from "../Collab/formsUi";
import { Btn, ErrorLine, itemStyle } from "../Collab/roomLinkUi";

export interface McpSubmission {
  id: string;
  name: string;
  version: string;
  description: string;
  auth_type: string;
  tools: string[];
  scope: "department" | "company";
  departments: string[];
  trial_credentials: string;
  submitted_by: string;
  submitted_at: string;
  submit_note?: string;
  status: string;
}

export interface McpSubscription {
  id: string;
  connector_id: string;
  status: string;
  oauth_token_ref?: string | null;
}

const KEY = "catfish.mcpTrialSubs";

function readTrialSubs(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

function writeTrialSubs(v: Record<string, string>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(v));
  } catch {
    // 私密窗口 / 禁了存储: 只靠「不在已上架列表」兜底
  }
}

export function rememberTrialSub(connectorId: string, subscriptionId: string) {
  writeTrialSubs({ ...readTrialSubs(), [connectorId]: subscriptionId });
}

/** 哪些活跃订阅是试用: 本机记过的, 或者连接器根本不在已上架列表里 */
export function trialSubscriptions(subs: McpSubscription[], registryIds: Set<string>): McpSubscription[] {
  const remembered = readTrialSubs();
  return subs.filter((s) => s.status === "active" && (remembered[s.connector_id] === s.id || !registryIds.has(s.connector_id)));
}

/** 刷新时: 试用订阅对应的提交不在待审了 → 自动退订。返回退掉的 connector_id。 */
export async function syncMcpTrials(subs: McpSubscription[], registryIds: Set<string>, pendingIds: Set<string>,
                                    unsubscribe: (subscriptionId: string) => Promise<boolean>): Promise<string[]> {
  const gone: string[] = [];
  const remembered = readTrialSubs();
  for (const s of trialSubscriptions(subs, registryIds)) {
    if (pendingIds.has(s.connector_id)) continue;
    if (await unsubscribe(s.id)) {
      gone.push(s.connector_id);
      delete remembered[s.connector_id];
    }
  }
  if (gone.length) writeTrialSubs(remembered);
  return gone;
}

export async function fetchPendingMcp(): Promise<McpSubmission[]> {
  const res = await fetchWithAuth(`${config.backendUrl}/v1/mcp/review`);
  if (!res.ok) return [];
  const json = (await res.json()) as { submissions?: McpSubmission[] };
  return json.submissions ?? [];
}

/** 待我审的连接器 */
export function PendingMcpSection({ items, trialSubs, onChanged }: {
  items: McpSubmission[];
  /** connector_id → subscription (已在试用的) */
  trialSubs: Map<string, McpSubscription>;
  onChanged: (msg: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    setManifest(null);
    if (!expanded) return;
    // 待审的清单只有审批人拿得到, 返回带 trial: true
    fetchWithAuth(`${config.backendUrl}/v1/mcp/manifest/${expanded}`)
      .then(async (r) => (r.ok ? ((await r.json()) as { manifest: Record<string, unknown> }).manifest : null))
      .then(setManifest)
      .catch(() => setManifest(null));
  }, [expanded]);

  if (!items.length) return null;

  async function trySubscribe(s: McpSubmission) {
    setBusy(s.id);
    setError(null);
    try {
      const res = await fetchWithAuth(`${config.backendUrl}/v1/mcp/subscribe`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ connector_id: s.id, trial: true }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          detail = ((await res.json()) as { detail?: string }).detail || detail;
        } catch { /* 非 json */ }
        setError(res.status === 403 ? `不能试用: ${detail}` : `试用订阅失败: ${detail}`);
        return;
      }
      const json = (await res.json()) as { next_step: "oauth" | "ready"; subscription: { id: string } };
      rememberTrialSub(s.id, json.subscription.id);
      onChanged(json.next_step === "ready"
        ? `已试用订阅 ${s.name} (测试账号: ${s.trial_credentials}); 审核有结果后自动退订`
        : `已登记试用订阅 ${s.name}, 还要按提交人写的测试账号完成授权: ${s.trial_credentials}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>
        待我审核 <span style={muted}>· {items.length} 个 · 同事还订不了; 有测试账号的可以先试用, 通过 / 驳回在门户里点</span>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {items.map((s) => {
        const trial = trialSubs.get(s.id);
        const open = expanded === s.id;
        return (
          <div key={s.id} style={{ ...itemStyle, gap: 4, cursor: "pointer" }} onClick={() => setExpanded(open ? null : s.id)}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
              <span style={{ fontSize: 13, fontWeight: 600, display: "inline-flex", gap: 6, alignItems: "center" }}>
                {s.name} <span style={muted}>v{s.version}</span>
                {trial && (
                  <span title="你在试用订阅这个连接器 (测试账号); 审核有结果后自动退订"
                        style={{ fontSize: 10, padding: "0 6px", borderRadius: 999, border: "1px solid var(--status-warn, #d97706)",
                                 color: "var(--status-warn, #d97706)" }}>试用</span>
                )}
              </span>
              <span style={muted}>{s.scope === "company" ? "全公司" : s.departments.join("、")} · {s.submitted_by} 提交</span>
            </div>
            <span style={muted}>{s.description}</span>
            {s.submit_note && <span style={muted}>提交人说: {s.submit_note}</span>}
            <span style={muted}>
              {s.trial_credentials ? `测试账号: ${s.trial_credentials}` : "提交人没写测试账号怎么拿 —— 只能看清单, 不能试用"}
            </span>
            {open && (
              <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", paddingLeft: 8 }}>
                工具: {(manifest && Array.isArray(manifest.tools)
                  ? (manifest.tools as { name: string }[]).map((t) => t.name)
                  : s.tools).join(", ") || "—"}
              </div>
            )}
            <div onClick={(ev) => ev.stopPropagation()}>
              {trial
                ? <span style={muted}>试用订阅中</span>
                : <Btn kind="ghost" disabled={!!busy || !s.trial_credentials} onClick={() => void trySubscribe(s)}>
                    {busy === s.id ? "订阅中…" : "试用订阅"}
                  </Btn>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
