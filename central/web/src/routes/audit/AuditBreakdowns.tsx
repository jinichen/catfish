/** 审计页的三张分组明细表 — 抽自 AuditPage.tsx (5/20 拆分).
 *
 * 8/1: 原来这里有一份文件私有的 `Table` (13px 字 / 8px 内边距 / 行高约 35px)。
 * 换成共享的 `DataTable` (12px / 4px 8px / 约 29px), 理由不只是密度:
 *
 *   · 那份私有表的可点行只有 onClick —— Tab 聚焦不到、Enter 没反应,
 *     也就是说"点一行下钻"这个功能对键盘用户是整个不存在的。
 *     共享表 7/30 已经修过这个。
 *   · 选中行的高亮原来靠行内 style, 而 onMouseLeave 会把背景抹回
 *     transparent —— 鼠标划过一次高亮就没了。现在走 DataTable 的 rowActive。
 *   · 同样一张"名称 / 占比 / 请求 / tokens"的表, 这一页 35px、概览页 29px。
 *
 * 保留的是 `Bar` —— 内嵌横条是这一页的特点 (概览页只有百分比数字),
 * 它塞进 DataTable 的 cell 里就行, 不需要私有表格。
 */

import { DataTable, type Column } from "../../components/DataTable";
import { AUDIT_TOP_N, type GlobalAudit } from "../../lib/me";
import {
  costRMB,
  fmtRMB,
  getModelDisplay,
  isKnownModel,
  splitDisplayName,
} from "../../lib/modelDisplay";
import { _palette, fmtTokens } from "./helpers";

/** 三张表的公共行形状。合成一个中间形状而不是给每张表各写一套列定义 ——
 *  它们的后四列(占比/请求/tokens/RMB)本来就该长得一模一样。 */
interface Row {
  key: string;
  label: React.ReactNode;
  /** 员工表的"部门"列。其余两张不显示。 */
  extra?: string;
  count: number;
  tokens: number;
  rmb?: number;
  pct: number;
  barColor: string;
}

function columnsFor(opts: { extraHeader?: string; rmb?: boolean }): Column<Row>[] {
  const cols: Column<Row>[] = [
    { header: "名称", cell: (r) => r.label, width: "28%" },
  ];
  if (opts.extraHeader)
    cols.push({
      header: opts.extraHeader,
      cell: (r) => (
        <span style={{ color: "var(--text-muted)" }} title={r.extra}>
          {r.extra}
        </span>
      ),
      // ⚠ 用 px 不用百分比: truncate 靠 maxWidth 生效, 而
      // `max-width: 16%` 在 auto-layout 的 <td> 上浏览器直接忽略,
      // 省略号永远不会出现 —— 长部门名照样把列撑开。
      width: 160,
      truncate: true,
    });
  cols.push(
    { header: "占比", cell: (r) => <Bar pct={r.pct} color={r.barColor} /> },
    { header: "请求", cell: (r) => r.count.toLocaleString(), align: "right", width: 72 },
    {
      header: "tokens",
      cell: (r) => <span style={{ fontWeight: 500 }}>{fmtTokens(r.tokens)}</span>,
      align: "right",
      width: 84,
    },
  );
  if (opts.rmb)
    cols.push({
      header: "RMB 估算",
      cell: (r) => (
        <span style={{ color: "var(--text-muted)" }}>
          {r.rmb !== undefined ? fmtRMB(r.rmb).replace("≈ ", "") : "—"}
        </span>
      ),
      align: "right",
      width: 84,
    });
  return cols;
}

function Breakdown({
  rows,
  isActive,
  onClickRow,
  extraHeader,
  rmb,
  footer,
  empty,
}: {
  rows: Row[];
  /** 哪些行算"当前下钻选中的"。
   *
   * 用谓词而不是一个 key: 员工表的行 key 是 `邮箱::部门` (同一个人可能挂在
   * 两个部门下各占一行), 而筛选只按邮箱 —— 这时**两行都该亮**。
   * 拿单个 key 比的话只会亮第一行, 看起来像另一行不属于这次筛选。 */
  isActive: (r: Row) => boolean;
  onClickRow: (r: Row) => void;
  extraHeader?: string;
  rmb?: boolean;
  footer?: React.ReactNode;
  /** 没数据时说清楚是哪一类没有。改成分段切换之后这句是必需的 ——
   *  空白的分段面板看起来像加载失败。 */
  empty: string;
}) {
  return (
    <DataTable
      columns={columnsFor({ extraHeader, rmb })}
      rows={rows}
      rowKey={(r) => r.key}
      onRowClick={onClickRow}
      rowActive={isActive}
      // 标识为空的行点了会筛出个空条件, 而 fetchGlobalAudit 把空串当没传
      // 直接丢掉 —— 表现是"点了没反应"。quota_events.model 是 NOT NULL 但
      // 没有 COALESCE(NULLIF(...)) (dept 和 user 那两条都有), 所以空串是
      // 真的可能出现的。
      rowClickable={(r) => r.key !== ""}
      empty={empty}
      footer={footer}
    />
  );
}

function InternalLoopbackCard({ audit }: { audit: GlobalAudit }) {
  // 8/1: 原来是一整张 Card 里放两个数的两列网格 —— 一行字的信息量占了
  // 一屏的 1/6, 而它讲的还不是这一页的主题 (员工用量)。
  // 概览页 7/30 已经把同一件事压成一行脚注, 这里跟上。
  //
  // 但**没有**把它并进上面那排指标: 这两个数跟员工业务是不同口径,
  // 混在一排里"总 tokens"到底含不含它就说不清了 —— 而那正是这个模块
  // 当初被拆出来的原因。
  return (
    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
      🔁 另有 gateway 自身调用 {(audit.internal_request_count ?? 0).toLocaleString()} 次 ·{" "}
      {fmtTokens(audit.internal_tokens ?? 0)} tok
      <span title="summarizer / proactive / 5 维 inject 等 gateway 自调消耗。跟员工业务无关, 但一样进真实 LLM 账单。">
        （总结 / 主动提醒等，不计入上面的员工业务）
      </span>
    </div>
  );
}

/** 表尾那行"被截断了"的说明。
 *
 * 后端对每张表都有 LIMIT, 但页面上看不出来 —— 正好 20 个模型时, 用户没法
 * 分辨是"公司就这 20 个"还是"被截到 20 个", 而占比之和也就永远差一截。 */
function truncatedNote(n: number, limit: number, unit: string, clickHint: string) {
  return n >= limit
    ? `只列用量最高的 ${limit} ${unit} —— 不在表里不代表没用过，占比之和也会不足 100%。${clickHint}`
    : // 没截断时表尾也不能空着: 整行可点只有 cursor:pointer 一个提示,
      // 而鼠标不放上去就看不见。性能页表尾就一直挂着这句。
      clickHint;
}

/** BL-AUDIT-UX-P0: 按模型 — 横向 bar + 比例 % + 友好名 + 颜色.
 *  BL-AUDIT-UX-P1: + RMB 列 (按 model 单价精算)
 *  BL-AUDIT-UX-P2: + onClickRow drill-down, activeModel 高亮 */
function ModelBreakdownCard({
  audit,
  onClickRow,
  activeModel,
}: {
  audit: GlobalAudit;
  onClickRow: (model: string) => void;
  activeModel: string | null;
}) {
  const total = audit.by_model.reduce((s, m) => s + m.total_tokens, 0);
  const rows: Row[] = audit.by_model.map((m, i) => {
    const md = getModelDisplay(m.model);
    const known = isKnownModel(m.model);
    return {
      key: m.model,
      label: known ? (
        <span
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          title={`${md.friendly}\n${m.model}`}
        >
          <span>{md.dotEmoji}</span>
          {/* 只取 "·" 前面那半截。models.yaml 里 display_name 的约定是
              `模型名 · 说明（档位）`, 整串塞进单元格会折两行 —— 跟 7/30 在
              概览页修的是同一个问题, 这一页当时漏了。完整值在 title 里。 */}
          <span style={{ fontWeight: 500, whiteSpace: "nowrap" }}>
            {splitDisplayName(md.friendly).name}
          </span>
          <span
            style={{
              fontSize: 11,
              color: "var(--text-muted)",
              padding: "0 5px",
              background: "var(--bg-elev)",
              borderRadius: 8,
            }}
          >
            {md.tier}
          </span>
        </span>
      ) : (
        // 模型配置里没有这个 id 时显示原始 id, 跟概览页/性能页一样一个裸
        // <code>、**不带圆点**。
        //
        // 兜底的 friendly 是固定的"未知模型"、dotEmoji 是固定的 ⚪ ——
        // 几个下线过的模型会全塌成同一行同一个圆点, 而每行还是可点的、
        // 点进去筛的是不同的模型。而且那个 ⚪ 跟 catfish-private-embed
        // 的圆点一模一样, 看起来像是个正常模型。
        <code style={{ fontSize: 11 }} title={`${m.model}\n模型配置里没有这个 id —— 多半是已下线的模型留下的历史用量`}>
          {m.model || "(未知)"}
        </code>
      ),
      count: m.count,
      tokens: m.total_tokens,
      rmb: costRMB(m.model, m.total_tokens),
      pct: total > 0 ? (m.total_tokens / total) * 100 : 0,
      // 认不出的模型不能都用兜底的 #94a3b8 —— N 个未知模型 N 条一模一样的
      // 灰条, 而这一列的全部作用就是"一眼看出哪个占大头"。
      // 退回按行号取色, 跟部门表/员工表一致。
      barColor: known ? md.color : _palette(i),
    };
  });

  return (
    <Breakdown
      rmb
      rows={rows}
      isActive={(r) => r.key === activeModel}
      onClickRow={(r) => onClickRow(r.key)}
      empty="本期没有模型调用记录。"
      footer={truncatedNote(rows.length, AUDIT_TOP_N.model, "个模型", "点一行 = 只看这个模型")}
    />
  );
}

function DeptBreakdownCard({
  audit,
  onClickRow,
  activeDept,
}: {
  audit: GlobalAudit;
  onClickRow: (dept: string) => void;
  activeDept: string | null;
}) {
  // by_department 在 GlobalAudit 里是必填字段, 后端两条路径都保证给
  // (拿不到部门的请求归进 "(未分组)" 桶, 不是省掉这个键)。
  // 这里不再写 `?? []` —— 三处写法不一 (一处 ?.、一处 ?? []、一处直接用)
  // 只会让人以为它有时候真的会缺, 然后在最热的路径上留一个白屏。
  const depts = audit.by_department;
  const total = depts.reduce((s, d) => s + d.total_tokens, 0);
  const rows: Row[] = depts.map((d, i) => {
    const isUnassigned = d.department === "(未分组)";
    return {
      key: d.department,
      label: (
        <span
          style={{
            fontStyle: isUnassigned ? "italic" : "normal",
            color: isUnassigned ? "var(--text-muted)" : undefined,
          }}
          title={
            isUnassigned
              ? "该桶里员工没绑部门 (dev_token / 老员工 / OIDC 缺 dept claim). Day 8 客户接入手册要求 SSO 必传 dept."
              : undefined
          }
        >
          {d.department}
        </span>
      ),
      count: d.count,
      tokens: d.total_tokens,
      pct: total > 0 ? (d.total_tokens / total) * 100 : 0,
      barColor: _palette(i),
    };
  });
  return (
    <Breakdown
      rows={rows}
      isActive={(r) => r.key === activeDept}
      onClickRow={(r) => onClickRow(r.key)}
      empty="本期没有带部门归属的请求 —— 员工没绑部门, 或者 SSO 没传 dept claim。"
      footer={truncatedNote(rows.length, AUDIT_TOP_N.department, "个部门", "点一行 = 只看这个部门")}
    />
  );
}

/** 员工行 key 里的邮箱那一半。key 形如 `邮箱::部门`。
 *  邮箱本身不含 "::", 部门名理论上可能含 —— 所以按第一个 "::" 切。 */
function emailOf(key: string): string {
  const i = key.indexOf("::");
  return i < 0 ? key : key.slice(0, i);
}

function UserBreakdownCard({
  audit,
  onClickRow,
  activeUser,
}: {
  audit: GlobalAudit;
  onClickRow: (user_email: string) => void;
  activeUser: string | null;
}) {
  const total = audit.by_user.reduce((s, u) => s + u.total_tokens, 0);
  const rows: Row[] = audit.by_user.map((u, i) => {
    const isUnassigned = u.user_email === "(未分组员工)";
    return {
      // 同一个邮箱可能在两个部门下各有一行, 光用邮箱当 key 会撞。
      key: `${u.user_email}::${u.department}`,
      label: (
        <span
          style={{
            fontStyle: isUnassigned ? "italic" : "normal",
            color: isUnassigned ? "var(--text-muted)" : undefined,
          }}
          title={
            isUnassigned
              ? "该桶里请求源没拿到 user_email (dev_token / OIDC 缺 email claim)."
              : u.user_email
          }
        >
          {u.user_email}
        </span>
      ),
      extra: u.department,
      count: u.count,
      tokens: u.total_tokens,
      pct: total > 0 ? (u.total_tokens / total) * 100 : 0,
      barColor: _palette(i),
    };
  });
  return (
    <Breakdown
      extraHeader="部门"
      rows={rows}
      // 筛选按邮箱, 而行 key 是 `邮箱::部门` —— 一个人挂两个部门时
      // **两行都要亮**, 所以按邮箱比而不是按 key 比。
      isActive={(r) => activeUser !== null && emailOf(r.key) === activeUser}
      onClickRow={(r) => onClickRow(emailOf(r.key))}
      empty="本期没有员工业务请求。"
      footer={truncatedNote(rows.length, AUDIT_TOP_N.user, "人", "点一行 = 只看这个人")}
    />
  );
}

/** 内嵌横向条 — 比例 % + 染色. */
function Bar({ pct, color }: { pct: number; color: string }) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div
        style={{
          flex: 1,
          minWidth: 40,
          height: 6,
          background: "var(--bg-elev)",
          borderRadius: 3,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${w}%`,
            height: "100%",
            background: color,
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <span
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          minWidth: 34,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

export { InternalLoopbackCard, ModelBreakdownCard, DeptBreakdownCard, UserBreakdownCard };
