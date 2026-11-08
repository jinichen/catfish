/** 分享审计 (11/08 分享治理 §2.4): 技能 / 专家包 / 知识页 / 连接器四条线的提交、审核、试用、撤回合成一张表。
 *
 * 三个来源, 三种形状, 在这里统一成一行 (时间 / 哪条线 / 事件 / 谁 / 对象 / 备注):
 *   · GET /v1/hub/audit   — skills-hub, 每条带 line: skill | expert; 技能用 by_user + namespace/name/version,
 *                            专家包用 by + id/version
 *   · GET /v1/wiki/audit  — wiki-hub, publish / unpublish / review_approve / review_withdraw; 没带 line, 这里标 wiki
 *   · GET /v1/mcp/audit   — mcp-registry, {user_sub, connector_id, action, meta}, 带 line: mcp
 * 三条都是 admin 才能拉。任一条拉失败不影响其他两条, 失败的在表头说明。
 */
import { useEffect, useMemo, useState } from "react";

import { Badge, DataTable, Section, Tabs, type BadgeTone } from "../../components/DataTable";
import { fetchAudit, type AuditEvent as HubAuditEvent } from "../../lib/hub";
import { fetchMcpAudit, type McpAuditEvent } from "../../lib/mcp";
import { api } from "../../lib/api";

export type ShareLine = "skill" | "expert" | "wiki" | "mcp";

export interface ShareAuditRow {
  key: string;
  ts: string;
  line: ShareLine;
  event: string;
  who: string;
  object: string;
  note: string;
}

export const LINE_LABEL: Record<ShareLine, string> = { skill: "技能", expert: "专家包", wiki: "知识页", mcp: "连接器" };
const LINE_TONE: Record<ShareLine, BadgeTone> = { skill: "accent", expert: "warn", wiki: "ok", mcp: "neutral" };

/** 事件名 → 中文。不认识的原样显示, 不猜。 */
export const EVENT_LABEL: Record<string, string> = {
  submit: "提交",
  approve: "通过",
  reject: "驳回",
  withdraw: "撤回",
  trial: "试用",
  trial_result: "试用结果",
  review_approve: "看过",
  review_withdraw: "撤下",
  review_reject: "驳回",
  publish: "发布",
  unpublish: "撤回",
  delete_version: "删除版本",
  install: "安装",
  subscribe: "订阅",
  subscribe_trial: "试用订阅",
  unsubscribe: "退订",
  oauth_complete: "授权完成",
  oauth_failed: "授权失败",
};
export const eventLabel = (e: string) => EVENT_LABEL[e] ?? e;

interface WikiAuditEvent {
  ts: string;
  event: string;
  namespace?: string;
  file_id?: string;
  title?: string;
  by_user?: string;
  note?: string;
  reason?: string;
  line?: string;
  [k: string]: unknown;
}

const str = (v: unknown) => (v == null ? "" : String(v));

export function hubRow(e: HubAuditEvent, i: number): ShareAuditRow {
  const line: ShareLine = e.line === "expert" ? "expert" : "skill";
  const object =
    line === "expert"
      ? [str(e.id), e.version ? `@${e.version}` : ""].join("")
      : [e.namespace && e.name ? `${e.namespace}/${e.name}` : str(e.name || e.namespace), e.version ? `@${e.version}` : ""].join("");
  const bits: string[] = [];
  if (e.note) bits.push(str(e.note));
  if (e.scope) bits.push(e.scope === "company" ? "全公司" : `部门 ${(e.departments as string[] | undefined)?.join("、") || ""}`.trim());
  if (typeof e.ok === "boolean") bits.push(e.ok ? "成功" : "失败");
  if (e.reason) bits.push(str(e.reason));
  return { key: `h${i}`, ts: str(e.ts), line, event: str(e.event), who: str(e.by_user || e.by), object, note: bits.join(" · ") };
}

export function wikiRow(e: WikiAuditEvent, i: number): ShareAuditRow {
  const object = e.title ? `${e.title} (${str(e.namespace)})` : [str(e.namespace), str(e.file_id)].filter(Boolean).join("/");
  return {
    key: `w${i}`, ts: str(e.ts), line: "wiki", event: str(e.event), who: str(e.by_user), object,
    note: str(e.note || e.reason),
  };
}

export function mcpRow(e: McpAuditEvent, i: number): ShareAuditRow {
  const m = e.meta || {};
  const bits: string[] = [];
  if (m.note) bits.push(str(m.note));
  if (m.scope) bits.push(m.scope === "company" ? "全公司" : `部门 ${(m.departments as string[] | undefined)?.join("、") || ""}`.trim());
  if (m.reason) bits.push(str(m.reason));
  if (m.trial) bits.push("试用");
  return { key: `m${i}`, ts: str(e.ts), line: "mcp", event: str(e.action), who: str(e.user_sub), object: str(e.connector_id), note: bits.join(" · ") };
}

/** 三条线合并、按时间倒序。导出来给测试和页面共用。 */
export function mergeShareAudit(hub: HubAuditEvent[], wiki: WikiAuditEvent[], mcp: McpAuditEvent[]): ShareAuditRow[] {
  return [...hub.map(hubRow), ...wiki.map(wikiRow), ...mcp.map(mcpRow)].sort((a, b) => b.ts.localeCompare(a.ts));
}

const fetchWikiAudit = async (limit: number): Promise<WikiAuditEvent[]> => {
  const r = await api.get<{ events: WikiAuditEvent[] }>(`/v1/wiki/audit?limit=${limit}`);
  return r.events || [];
};

const when = (t: string) => {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? t : d.toLocaleString("zh-CN", { hour12: false }).slice(0, -3);
};

type Filter = "all" | ShareLine;

export function ShareAudit() {
  const [rows, setRows] = useState<ShareAuditRow[] | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let alive = true;
    const limit = 200;
    const wrap = async <T,>(label: string, p: Promise<T[]>): Promise<T[]> => {
      try {
        return await p;
      } catch (e) {
        if (alive) setFailed((f) => [...f, `${label}: ${e instanceof Error ? e.message : String(e)}`]);
        return [];
      }
    };
    setFailed([]);
    Promise.all([
      wrap("技能 / 专家包", fetchAudit(limit)),
      wrap("知识页", fetchWikiAudit(limit)),
      wrap("连接器", fetchMcpAudit(limit)),
    ]).then(([h, w, m]) => {
      if (alive) setRows(mergeShareAudit(h, w, m));
    });
    return () => {
      alive = false;
    };
  }, []);

  const counts = useMemo(() => {
    const c: Record<ShareLine, number> = { skill: 0, expert: 0, wiki: 0, mcp: 0 };
    for (const r of rows ?? []) c[r.line]++;
    return c;
  }, [rows]);
  const shown = useMemo(() => (filter === "all" ? rows ?? [] : (rows ?? []).filter((r) => r.line === filter)), [rows, filter]);

  return (
    <Section fill>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
        <Tabs
          active={filter}
          onChange={setFilter}
          tabs={[
            { key: "all", label: `全部 ${rows?.length ?? ""}` },
            { key: "skill", label: `技能 ${counts.skill}` },
            { key: "expert", label: `专家包 ${counts.expert}` },
            { key: "wiki", label: `知识页 ${counts.wiki}` },
            { key: "mcp", label: `连接器 ${counts.mcp}` },
          ]}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          谁提交、谁试过、谁批的 / 驳的、谁撤的 —— 四条线最近各 200 条。
        </span>
      </div>
      {failed.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: 6 }}>
          有的来源拉不到，下面少了那条线的记录：{failed.join("；")}
        </div>
      )}
      {rows === null ? (
        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>加载中…</div>
      ) : (
        <DataTable<ShareAuditRow>
          fill
          rows={shown}
          rowKey={(r) => r.key}
          empty="还没有分享相关的记录"
          columns={[
            { header: "时间", width: 140, nowrap: true, cell: (r) => when(r.ts) },
            { header: "哪条线", width: 72, cell: (r) => <Badge tone={LINE_TONE[r.line]}>{LINE_LABEL[r.line]}</Badge> },
            { header: "事件", width: 84, nowrap: true, cell: (r) => eventLabel(r.event) },
            { header: "谁", width: 180, truncate: true, cell: (r) => <span title={r.who}>{r.who || "—"}</span> },
            { header: "对象", width: "26%", truncate: true, cell: (r) => <span title={r.object} style={{ fontFamily: "var(--font-mono)" }}>{r.object || "—"}</span> },
            { header: "备注", truncate: true, cell: (r) => <span title={r.note}>{r.note || ""}</span> },
          ]}
        />
      )}
    </Section>
  );
}
