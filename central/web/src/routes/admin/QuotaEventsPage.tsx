/** /admin/quota/events —— 逐条配额日志.
 *
 * 7/30 从 AdminPage.tsx 拆出来 (CLAUDE.md 军规 §1)。见 AdminHome.tsx 文件头。
 *
 * ## 8/1 改了两件事
 *
 * **一屏布局。** 原来整个 <main> 滚: 一翻页, 页面标题、五个筛选框、
 * "共 200 条"、分页按钮全跟着滚出屏幕 —— 而这几样恰恰是看日志时最需要
 * 一直在手边的东西 (改个筛选条件要先滚回顶部, 翻下一页要先滚到底)。
 * 现在只有表身滚, 表头 sticky。
 *
 * 表头钉住在这一页尤其要紧: 连着四个数字列 (in / out / tot / 延迟 ms),
 * 滚到第 80 行时"342772"是哪一列只能靠数。
 *
 * **换掉自己那套表格和徽章。** 上一轮的文件头写着"这一轮没换, 下次动这一页
 * 时顺手换掉" —— 这次动了, 换了。thStyle / tdStyle / btnStyle / StatusBadge
 * 全部退役, 走共享的 DataTable / Badge / BTN。
 */

import { useEffect, useState } from "react";

import {
  Badge,
  BTN,
  BTN_PRIMARY,
  DataTable,
  Section,
  Toolbar,
  type BadgeTone,
  type Column,
} from "../../components/DataTable";
import { PageShell, Stale } from "../../components/PageShell";
import {
  fetchAuditEvents,
  type AuditEvent,
  type AuditEventsResponse,
} from "../../lib/me";

// BL-ADMIN-AUDIT (5/12 鸿波): /admin/quota/events — 逐条 audit 历史 + 筛选 + 分页 + CSV.
//
// 跟 /admin/quota 配额规则 read-only 文本互补 (一个看规则, 一个看实际数据).
// API: GET /api/audit/events (gateway, RBAC admin only — sysadmin 走 is_admin() 通过).

export function AdminQuotaEvents() {
  const PAGE_SIZE = 50;
  const [data, setData] = useState<AuditEventsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 4 维筛选 + 时间窗口
  const [hoursBack, setHoursBack] = useState(24);
  const [dept, setDept] = useState("");
  const [userFilter, setUserFilter] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);

  const load = () => {
    setLoading(true);
    setError(null);
    const since_ms = Date.now() - hoursBack * 3600 * 1000;
    fetchAuditEvents({
      since_ms,
      dept: dept || undefined,
      user_filter: userFilter || undefined,
      model: model || undefined,
      status: status || undefined,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    })
      .then((r) => {
        if (r.ok) {
          setData(r.data);
        } else {
          setData(null);
          setError(r.error);
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // 不进 deps — 手动 "查询" 按钮触发, 防一边输一边狂查 PG
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const exportCSV = () => {
    if (!data || data.events.length === 0) return;
    const header = [
      "ts", "user", "department", "model", "tokens_in", "tokens_out",
      "tokens_total", "latency_ms", "ttft_ms", "status", "error",
    ].join(",");
    const rows = data.events.map((e) => [
      new Date(e.ts * 1000).toISOString(),
      csvEscape(e.user),
      csvEscape(e.department),
      csvEscape(e.model),
      e.prompt_tokens,
      e.completion_tokens,
      e.total_tokens,
      e.latency_ms,
      e.ttft_ms ?? "",
      csvEscape(e.status),
      csvEscape(e.error ?? ""),
    ].join(","));
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-events-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // ⚠ 凡是 truncate 的列都配了 title。旧版整列 nowrap 不截、靠横向滚动,
  // 全值始终可读; 换成截断之后, 没有 title 的话被省略号吃掉的部分就真没了
  // (DataTable 的 truncate 文档也写着这条)。
  const columns: Column<AuditEvent>[] = [
    // 8/8: 「时间」原来没写 width。表格换成 table-layout:fixed 之后, 没写
    // width 的列拿的是"剩余空间", 而这张表 px 之和 (1200) 本来就超过后台
    // 内容区 (约 940) —— 剩余是负的, 实测这一列被算成 **0 宽整列消失**。
    // 十列的审计日志本来就要横向滚, 那就让每列都有确定宽度、滚得到。
    {
      header: "时间",
      cell: (e) => new Date(e.ts * 1000).toLocaleString("zh-CN"),
      nowrap: true,
      width: 136,
    },
    {
      header: "员工",
      cell: (e) => <span title={e.user}>{e.user}</span>,
      truncate: true,
      width: 200,
    },
    {
      header: "部门",
      cell: (e) => <span title={e.department}>{e.department}</span>,
      truncate: true,
      width: 120,
    },
    {
      header: "模型",
      cell: (e) => <span title={e.model}>{e.model}</span>,
      truncate: true,
      width: 200,
    },
    { header: "in", cell: (e) => e.prompt_tokens.toLocaleString(), align: "right", width: 76 },
    { header: "out", cell: (e) => e.completion_tokens.toLocaleString(), align: "right", width: 68 },
    {
      header: "tot",
      cell: (e) => <span style={{ fontWeight: 500 }}>{e.total_tokens.toLocaleString()}</span>,
      align: "right",
      width: 84,
    },
    {
      header: "延迟 ms",
      cell: (e) => Math.round(e.latency_ms).toLocaleString(),
      align: "right",
      width: 76,
    },
    { header: "状态", cell: (e) => <StatusBadge status={e.status} />, width: 96 },
    {
      header: "错误",
      cell: (e) => (
        <span style={{ color: "var(--text-muted)" }} title={e.error ?? ""}>
          {e.error ?? ""}
        </span>
      ),
      truncate: true,
      width: 280,
    },
  ];

  return (
    <PageShell scroll="data">
      <Toolbar title="配额日志（逐条）">
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          直接读 gateway_audit 表
          <span
            title="跟「配额」页互补 —— 那边看规则, 这里看实际发生了什么。RBAC 严格 admin only (sysadmin 通过)。"
            style={{ cursor: "help", marginLeft: 4 }}
          >
            ⓘ
          </span>
        </span>
      </Toolbar>

      {/* 筛选条。**留在滚动区外面** —— 改筛选条件是"改主意"的动作,
          滚到第 80 行才想起要换个部门时, 不该先滚回顶部。 */}
      <Section>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
          <FilterField label="时间范围">
            <select
              value={hoursBack}
              onChange={(e) => setHoursBack(Number(e.target.value))}
              style={inputStyle}
            >
              <option value={1}>过去 1h</option>
              <option value={6}>过去 6h</option>
              <option value={24}>过去 24h</option>
              <option value={72}>过去 3 天</option>
              <option value={168}>过去 7 天</option>
              <option value={720}>过去 30 天</option>
            </select>
          </FilterField>
          <FilterField label="部门">
            <input
              value={dept}
              onChange={(e) => setDept(e.target.value)}
              placeholder="例 engineering"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="员工 email">
            <input
              value={userFilter}
              onChange={(e) => setUserFilter(e.target.value)}
              placeholder="例 alice@ffcs.cn"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="模型">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="例 catfish-private-main"
              style={inputStyle}
            />
          </FilterField>
          <FilterField label="状态">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              style={inputStyle}
            >
              <option value="">全部</option>
              <option value="ok">ok</option>
              <option value="error">error</option>
              <option value="interrupted_resumed">interrupted_resumed</option>
            </select>
          </FilterField>
          <button
            onClick={() => {
              setPage(0);
              load();
            }}
            style={BTN_PRIMARY}
          >
            {loading ? "查询中…" : "查询"}
          </button>
          <button
            onClick={exportCSV}
            disabled={!data || data.events.length === 0}
            style={BTN}
          >
            导出 CSV
          </button>
        </div>
      </Section>

      {error && <ErrorPanel error={error} />}

      {!error && (
        <Section
          fill
          title={
            data ? (
              <span style={{ fontWeight: 400, color: "var(--text-muted)" }}>
                共 {data.total.toLocaleString()} 条 · 当前{" "}
                {data.events.length === 0
                  ? 0
                  : `${page * PAGE_SIZE + 1}–${page * PAGE_SIZE + data.events.length}`}
              </span>
            ) : undefined
          }
          action={
            data && data.events.length > 0 ? (
              // 分页器放标题行 —— 它跟"共 N 条"是同一件事, 而且在滚动区
              // **外面**, 翻页不用先滚到底。原来它在表格下方, 50 行一页时
              // 每次翻页都要滚一整屏。
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  style={BTN}
                >
                  ← 上一页
                </button>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {page + 1} / {totalPages}
                </span>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={page + 1 >= totalPages}
                  style={BTN}
                >
                  下一页 →
                </button>
              </span>
            ) : undefined
          }
        >
          {/* 8/1: 加载期间压暗。原来 loading 只塞在 empty 里 —— 那只在 0 行时
              生效, 于是改完筛选点查询, 表里显示的还是**上一次的行**, 而筛选条
              和"共 N 条"都已经是新的, 看起来像"查完了, 结果就是这些"。
              筛选条和分页器留在外面, 它们是改主意的出口。 */}
          <Stale fill loading={loading}>
            <DataTable
              fill
              columns={columns}
              rows={data?.events ?? []}
              // ts 秒级会撞 (同一秒里多条), 加上下标才唯一。
              rowKey={(e, i) => `${e.ts}-${i}`}
              empty={
                // data 还没回来时不能说"没有匹配的事件" —— 首帧
                // (data=null, loading=false) 会闪一下这句假话。
                data === null
                  ? "加载中…"
                  : "没有匹配的事件 —— 试试放宽时间范围 / 清空筛选"
              }
            />
          </Stale>
        </Section>
      )}
    </PageShell>
  );
}

function csvEscape(s: string | number): string {
  const v = String(s);
  if (v.includes(",") || v.includes('"') || v.includes("\n")) {
    return '"' + v.replace(/"/g, '""') + '"';
  }
  return v;
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{label}</span>
      {children}
    </div>
  );
}

/** 8/1: 原来是这个文件私有的一份实现, 硬编码 #10b981 / #f59e0b / #ef4444
 *  —— 也就是 DataTable 文件头说的"6 份徽章"里的一份。换成共享 Badge 之后
 *  颜色走 --status-* 变量, 跟别处的成功/警告/失败是同一套。 */
function StatusBadge({ status }: { status: string }) {
  const tone: BadgeTone =
    status === "ok" ? "ok" : status === "interrupted_resumed" ? "warn" : "err";
  return <Badge tone={tone}>{status}</Badge>;
}

function ErrorPanel({ error }: { error: string }) {
  return (
    <Section title="请求失败">
      <div style={{ fontSize: 12, lineHeight: 1.6 }}>
        <div style={{ fontFamily: "var(--font-mono)", color: "var(--status-err)" }}>
          {error}
        </div>
        <div style={{ marginTop: 8, color: "var(--text-muted)" }}>
          常见原因:
          <ul style={{ paddingLeft: 20, margin: "4px 0 0 0" }}>
            <li>
              <b>404</b>：gateway 还没重启 —— <code>/api/audit/events</code> 是后加的
            </li>
            <li>
              <b>403</b>：当前角色不是 admin / sysadmin
            </li>
            <li>
              <b>401</b>：OIDC token 过期，退出重登
            </li>
            <li>
              <b>network error</b>：gateway 进程没起 / 端口换了
            </li>
          </ul>
        </div>
      </div>
    </Section>
  );
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "3px 8px",
  color: "var(--text)",
  fontSize: 12,
  minWidth: 150,
};
