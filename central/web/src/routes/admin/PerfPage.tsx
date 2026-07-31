/** /admin/perf — 全公司 LLM 性能仪表 (P3.5.60, 6/22 鸿波 catch "继续完成").
 *
 * 跟 /audit 区别:
 *   - /audit 走 quota_events → 看 token / cost / 配额, 没 latency
 *   - /admin/perf 走 gateway_audit → 看 latency p50/p95/p99 + ttft, 看慢
 *
 * 数据合同: GET /api/audit/global/perf (admin only, app.py).
 * 后端聚合: catfish_gateway.metrics.query_perf_summary_global, PG 主路径 +
 *           JSONL fallback. 跟 PerfCard (Companion 端) 一致, 不是分散逻辑.
 *
 * 设计: 复用 AuditKPI 的 Card / Stat 风格, 不引新组件. 时间窗 (24h/7d/30d)
 *       + 模型/部门 drill-down filter. 跟 audit 同一套 UX. 不做 chart (recharts
 *       重), 拿 table + grid 表达. 后期要曲线 BL-PERF-CHARTS 再加.
 */

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

import {
  BTN,
  BTN_PRIMARY,
  DataTable,
  Section,
  Tabs,
  Toolbar,
} from "../../components/DataTable";
import { getModelDisplay, isKnownModel } from "../../lib/modelDisplay";
import {
  fetchGlobalPerf,
  fetchModelCatalog,
  type CatalogModel,
  type GlobalPerf,
  PERF_TOP_N,
} from "../../lib/me";
// P3.5.94 (6/23 鸿波): model/dept filter 改 select 下拉. 复用 AccessPage 已
// 用的 listDepartments + 新加的 fetchModelCatalog (调 /v1/catalog).
import { adminApi, type Department } from "../../lib/admin";

/** 工具条里的下拉。跟 BTN 同高, 不然一行控件高低不齐。 */
const SELECT: CSSProperties = {
  padding: "2px 6px",
  border: "1px solid var(--border)",
  borderRadius: 3,
  fontSize: 11,
  background: "var(--bg-elev)",
  color: "var(--text)",
  fontFamily: "inherit",
  maxWidth: 200,
};

/** metrics.py 对空 department 的展示名 (`r[0] or "(未分组)"`), 不是真部门名。 */
const UNGROUPED = "(未分组)";

const TIME_WINDOWS = [
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v < 1000) return `${Math.round(v)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}K`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

/** Health rating — 跟 PerfCard 一致, 给 KPI 上色. */
function ratingP95(p95: number | null): "good" | "warn" | "bad" {
  if (p95 == null) return "good";
  if (p95 < 5_000) return "good";
  if (p95 < 30_000) return "warn";
  return "bad";
}

function colorOfRating(r: "good" | "warn" | "bad"): string {
  return r === "good"
    ? "var(--status-ok)"
    : r === "warn"
      ? "var(--status-warn)"
      : "var(--status-err)";
}

function Stat({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: color ?? "var(--text)" }}>{value}</div>
      {hint && (
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{hint}</div>
      )}
    </div>
  );
}

function SourceBadge({ source }: { source: GlobalPerf["source"] }) {
  const map: Record<GlobalPerf["source"], { txt: string; bg: string }> = {
    pg: { txt: "🏢 中央 PG", bg: "#dcfce7" },
    jsonl: { txt: "📄 中央 JSONL", bg: "#fef9c3" },
    none: { txt: "⚠️ 无数据", bg: "#fee2e2" },
  };
  const m = map[source];
  return (
    <span
      style={{
        fontSize: 11,
        background: m.bg,
        color: "#374151",
        padding: "2px 8px",
        borderRadius: 4,
      }}
    >
      {m.txt}
    </span>
  );
}

export function PerfPage() {
  const [hours, setHours] = useState<number>(24);
  const [modelFilter, setModelFilter] = useState<string>("");
  const [deptFilter, setDeptFilter] = useState<string>("");
  const [perf, setPerf] = useState<GlobalPerf | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  // P3.5.60.1 (6/22 鸿波 catch "是太慢还是没有数据"): 不静默吞 error,
  // loading / error / empty 三态各自展示, 用户能立刻区分.
  const [error, setError] = useState<{ status?: number; message: string } | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);

  // P3.5.94 (6/23 鸿波): model/dept 下拉数据源, 独立 fetch 不受 filter 影响.
  // 不用 perf.by_model — filter 选后只剩 1 项会锁死 dropdown.
  const [allModels, setAllModels] = useState<CatalogModel[]>([]);
  const [allDepts, setAllDepts] = useState<Department[]>([]);

  useEffect(() => {
    // 拉一次 model catalog + dept list (mount 后)
    void fetchModelCatalog().then(setAllModels);
    void adminApi
      .listDepartments()
      .then((r) => setAllDepts(r.departments ?? []))
      .catch(() => {
        /* admin 可能拿不到 dept (manager 路径), 忽略, 下拉退化为空 */
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const startMs = Date.now();
    fetchGlobalPerf(hours, {
      model: modelFilter || null,
      dept: deptFilter || null,
    }).then((r) => {
      if (cancelled) return;
      setPerf(r.data);
      setError(r.error);
      setLoading(false);
      setFetchedAt(Date.now() - startMs);
    });
    return () => {
      cancelled = true;
    };
  }, [hours, modelFilter, deptFilter]);

  const successRate = perf && perf.request_count > 0 ? perf.ok_count / perf.request_count : 0;
  const rating = ratingP95(perf?.latency_p95_ms ?? null);

  // 明细表按维度切换 (见下面 Section 里的说明)
  const [tab, setTab] = useState<"model" | "dept">("model");

  // 三态互斥, 在这里算一次, 下面只渲染一个框
  const statusNote: ReactNode = error ? (
    <div>
      <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: 6 }}>
        {error.status === 404
          ? "endpoint /api/audit/global/perf 没注册 (404) — gateway 进程没重启? 这是 P3.5.60 新加的 endpoint, 老 gateway 没这条路由。重启 8999 进程加载。"
          : error.status === 401
            ? "401 未认证 — 需要 admin/sysadmin 才能看性能页。"
            : error.status === 403
              ? "403 权限不够 — RBAC 卡了, 看 _require_admin 过没过。"
              : error.status
                ? `HTTP ${error.status} — 中央 gateway 报错, 看 /var/log/catfish-gateway/ 或 docker logs catfish-gateway`
                : `网络 / fetch 失败: ${error.message.slice(0, 200)}`}
      </div>
      <details style={{ fontSize: 11, color: "var(--text-muted)" }}>
        <summary style={{ cursor: "pointer" }}>真实错误 (devtool 用)</summary>
        <pre
          style={{
            background: "var(--bg)",
            padding: 8,
            borderRadius: 4,
            marginTop: 6,
            overflowX: "auto",
            whiteSpace: "pre-wrap",
          }}
        >
          status: {error.status ?? "(none)"}
          {"\n"}
          message: {error.message}
        </pre>
      </details>
    </div>
  ) : loading && !perf ? (
    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
      正在拉中央 audit，长窗口 (7d/30d) 的 PG PERCENTILE_CONT 要几秒…
    </div>
  ) : perf && perf.request_count === 0 && !loading ? (
    <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.7 }}>
      这个窗口内没有数据。
      {perf.source === "none"
        ? "（中央 gateway_audit 表和 JSONL 都是空的 —— 新部署, 还没员工跑过 LLM。）"
        : `（${perf.source === "pg" ? "PG gateway_audit" : "JSONL audit"} 在这个窗口内没有记录。）`}
      <br />
      可以试：拉长时间窗、去掉筛选，或者先去 Companion 里聊几句攒点数据。
    </div>
  ) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {/* 7/30: 控制条原来套在一张 Card 里 —— 66px 外壳装 30px 内容。
          改成裸工具条。 */}
      <Toolbar title="LLM 性能">
        {perf && <SourceBadge source={perf.source} />}
        {TIME_WINDOWS.map((w) => (
          <button
            key={w.hours}
            onClick={() => setHours(w.hours)}
            style={hours === w.hours ? BTN_PRIMARY : BTN}
          >
            {w.label}
          </button>
        ))}
        {/* P3.5.94 (6/23 鸿波): model/dept 下拉. 数据源是 catalog + dept list,
            不受 filter 影响 (否则切回别的 model 会锁死)。
            当前值不在列表里时 (例如从老 audit 行点进来的已下线模型),
            补一行兜底 option, 免得 UI 显示成"全部"。 */}
        <select
          value={modelFilter}
          onChange={(e) => setModelFilter(e.target.value)}
          style={SELECT}
        >
          <option value="">全部模型 ({allModels.length})</option>
          {modelFilter && !allModels.some((m) => m.id === modelFilter) && (
            <option value={modelFilter}>{modelFilter} (已过期)</option>
          )}
          {allModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.display_name || m.id}
            </option>
          ))}
        </select>
        <select
          value={deptFilter}
          onChange={(e) => setDeptFilter(e.target.value)}
          style={SELECT}
        >
          <option value="">全部部门 ({allDepts.length})</option>
          {deptFilter && !allDepts.some((d) => d.name === deptFilter) && (
            <option value={deptFilter}>{deptFilter} (历史)</option>
          )}
          {allDepts.map((d) => (
            <option key={d.name} value={d.name}>
              {d.name}
            </option>
          ))}
        </select>
        {loading ? (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>加载中…</span>
        ) : fetchedAt != null && perf && !error ? (
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{fetchedAt}ms</span>
        ) : null}
      </Toolbar>

      {/* P3.5.60.1 (6/22 鸿波 catch "是太慢还是没有数据"): 三态明确展示, 不静默。
          7/30: 三态原本各占一整张 Card (loading / error / 空数据), 各背 66px 外壳,
          而它们**互斥** —— 任何时刻最多只有一个会渲染。合成一个 Section。
          其中 loading 那张的 title 是空字符串, Card 照样渲染 <h3> + 12px margin。 */}
      {statusNote ? <Section>{statusNote}</Section> : null}

      {/* KPI grid */}
      {perf && !error && (
        <Section title="核心指标">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
              gap: 8,
            }}
          >
            <Stat
              label="总调用"
              value={perf.request_count.toLocaleString()}
              hint={`成功 ${perf.ok_count.toLocaleString()} · 错 ${perf.error_count.toLocaleString()}`}
            />
            <Stat
              label="成功率"
              value={fmtPct(successRate)}
              color={
                successRate >= 0.99
                  ? "var(--status-ok)"
                  : successRate >= 0.9
                    ? "var(--status-warn)"
                    : "var(--status-err)"
              }
            />
            <Stat label="总 tokens" value={fmtTokens(perf.total_tokens)} />
            <Stat
              label="活跃员工"
              value={String(perf.active_users)}
              hint={`${perf.active_departments} 部门`}
            />
            <Stat
              label="Latency p50"
              value={fmtMs(perf.latency_p50_ms)}
              color={colorOfRating(rating)}
            />
            <Stat
              label="Latency p95"
              value={fmtMs(perf.latency_p95_ms)}
              color={colorOfRating(rating)}
            />
            <Stat
              label="Latency p99"
              value={fmtMs(perf.latency_p99_ms)}
              color={colorOfRating(rating)}
            />
            <Stat label="TTFT p50" value={fmtMs(perf.ttft_p50_ms)} />
            <Stat label="TTFT p95" value={fmtMs(perf.ttft_p95_ms)} />
          </div>
        </Section>
      )}

      {/* 7/30 二改: 并排 → 分段切换.
          这两张各 7 列, 并排时每张只分到约 720px, 而"catfish-public-nvidia-
          nemotron"这类模型名和部门名都是不定长的 —— 框内部会各自横向滚动,
          左边的列被切掉。全宽之后 7 列有 200px/列, 放得下。
          两张回答的是同一个问题的两个切面 (谁慢 / 谁错), 适合切换。 */}
      {perf && !error && (perf.by_model.length > 0 || perf.by_department.length > 0) && (
        <Section>
          <div style={{ marginBottom: 6 }}>
            <Tabs
              active={tab}
              onChange={setTab}
              tabs={[
                { key: "model", label: `按模型 ${perf.by_model.length}` },
                { key: "dept", label: `按部门 ${perf.by_department.length}` },
              ]}
            />
          </div>

          {tab === "model" ? (
            <DataTable
              rows={perf.by_model}
              rowKey={(m) => m.model}
              // ⚠ 空 model 不能拿去当筛选值。后端 metrics.py 对没记到模型名的
              // 行返 `""`, 而 `""` 正好是上面下拉框"全部模型"那个 option 的值 ——
              // 点「(未知)」这一行会**静默清空筛选**, 看起来像点了没反应。
              onRowClick={(m) => {
                if (m.model) setModelFilter(m.model);
              }}
              empty="窗口内没有模型被调用"
              footer={
                perf.by_model.length >= PERF_TOP_N.model
                  ? `只显示调用量最高的 ${PERF_TOP_N.model} 个模型。点一行 = 筛选该模型`
                  : "点一行 = 筛选该模型"
              }
              columns={[
                {
                  header: "模型",
                  cell: (m) =>
                    m.model && isKnownModel(m.model) ? (
                      <span title={m.model}>{getModelDisplay(m.model).friendly}</span>
                    ) : (
                      <code style={{ fontSize: 11 }}>{m.model || "(未知)"}</code>
                    ),
                },
                { header: "调用", align: "right", width: 90, cell: (m) => m.count.toLocaleString() },
                {
                  header: "错误率",
                  align: "right",
                  width: 90,
                  cell: (m) => {
                    const r = m.count > 0 ? m.error_count / m.count : 0;
                    return (
                      <span
                        style={{ color: r > 0.05 ? "var(--status-err)" : undefined }}
                        title={`${m.error_count} 次错误`}
                      >
                        {fmtPct(r)}
                      </span>
                    );
                  },
                },
                { header: "p50", align: "right", width: 90, cell: (m) => fmtMs(m.p50_ms) },
                { header: "p99", align: "right", width: 90, cell: (m) => fmtMs(m.p99_ms) },
                { header: "tokens", align: "right", width: 110, cell: (m) => fmtTokens(m.total_tokens) },
              ]}
            />
          ) : (
            <DataTable
              rows={perf.by_department}
              rowKey={(d) => d.department}
              // ⚠ (未分组) 是后端 metrics.py 为"没有部门"合成的展示名, 不是真部门。
              // 而 perf 的 WHERE 是字面量 `department = %s`, 没有 audit 那条链
              // (quota.py _build_audit_filter) 的合成桶兼容 —— 拿它去筛必然 0 行,
              // 页面翻成"这个窗口内没有数据", 用户还得手动切回"全部部门"。
              onRowClick={(d) => {
                if (d.department && d.department !== UNGROUPED) setDeptFilter(d.department);
              }}
              empty="窗口内没有部门产生调用"
              footer={
                perf.by_department.length >= PERF_TOP_N.department
                  ? `只显示调用量最高的 ${PERF_TOP_N.department} 个部门。点一行 = 筛选该部门`
                  : "点一行 = 筛选该部门"
              }
              columns={[
                { header: "部门", cell: (d) => d.department },
                { header: "员工", align: "right", width: 80, cell: (d) => d.active_users },
                { header: "调用", align: "right", width: 90, cell: (d) => d.count.toLocaleString() },
                {
                  header: "错误",
                  align: "right",
                  width: 80,
                  cell: (d) => (
                    <span style={{ color: d.error_count > 0 ? "var(--status-err)" : undefined }}>
                      {d.error_count}
                    </span>
                  ),
                },
                { header: "p50", align: "right", width: 90, cell: (d) => fmtMs(d.p50_ms) },
                { header: "p99", align: "right", width: 90, cell: (d) => fmtMs(d.p99_ms) },
                { header: "tokens", align: "right", width: 110, cell: (d) => fmtTokens(d.total_tokens) },
              ]}
            />
          )}
        </Section>
      )}

      {/* schema note: 数据零出端透明声明 */}
      {perf?.schema_note && (
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
            padding: "var(--space-2)",
            borderLeft: "3px solid var(--border)",
            paddingLeft: "var(--space-3)",
          }}
        >
          {perf.schema_note}
        </div>
      )}
    </div>
  );
}

// 7/30: 这里原来是本文件私有的 tableStyle / thStyle / thRightStyle / tdStyle /
// tdRightStyle / linkBtn 六个常量。全仓一共有 7 张表, 每张都各写一套, 同类
// 数据的行高实测落在 22/25/29/31/35/37px 六个不同值上。现在统一走
// components/DataTable。linkBtn 也没了 —— 整行可点比行内一个下划线链接更好点,
// DataTable 的 onRowClick 带 hover 高亮。
