/** /admin/facts — 事实补丁系统 (BL-Q3-FACT P0 MVP Day 2, 5/10).
 *
 * 三视图 (URL 路由):
 *   /admin/facts              列表 + 上传按钮
 *   /admin/facts/:id          详情 (事实点 + 受影响 skill + patches)
 *
 * 鸿波 5/10 'BL-Q3-FACT 是刚需现在就做' 触发的 demo MVP, 5/14 演示用.
 * 详 docs/CATFISH-FACT-PATCH-DESIGN.md.
 */

import { useEffect, useRef, useState } from "react";
import { Link, Route, Routes, useNavigate } from "react-router-dom";

import { BTN, DataTable, Section } from "../../components/DataTable";
import { PageShell } from "../../components/PageShell";
import { RoleGate } from "../../components/RoleGate";
import { factsApi, type FactMeta } from "../../lib/facts";
// 8/1 拆文件: 753 行 + 三个对话框会越军规红线, 见 factsShared.tsx 文件头。
import { FactDetailPage } from "./FactDetail";
import { btnPrimary, errBox, fmtSize, fmtTime, inputStyle, StatusBadge } from "./factsShared";

export function FactsPage() {
  return (
    <RoleGate require={["admin", "sysadmin"]}>
      <Routes>
        <Route index element={<FactsList />} />
        <Route path=":id" element={<FactDetailPage />} />
      </Routes>
    </RoleGate>
  );
}

// ── 列表 + 上传 ─────────────────────────────────

function FactsList() {
  const navigate = useNavigate();
  const [facts, setFacts] = useState<FactMeta[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = async () => {
    setErr(null);
    try {
      const { facts } = await factsApi.list();
      setFacts(facts);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <PageShell scroll="data">
      <Section
        title="政策同步"
        action={
          <button onClick={() => void refresh()} style={BTN}>
            刷新
          </button>
        }
      >
        {/* 7/30: 原来这里有一段 2 行 13px 的常驻说明("上传公司政策/标准/流程
            变更文件, 鲶鱼自动找出受影响的 skill 并生成改进 patch…")。删了 ——
            它对第一次用的人有价值, 对之后每一次都是 40px 纯占位, 而这一页
            是同一批人反复用的。下面上传行的提示文字已经说明了格式和耗时。 */}
        <UploadForm onUploaded={() => void refresh()} />
      </Section>

      <Section fill title={`已上传${facts ? ` · ${facts.length} 条` : ""}`}>
        {err && <div style={errBox}>错误: {err}</div>}
        {!facts && !err && (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>加载中…</div>
        )}
        {facts && (
          <DataTable
            fill
            rows={facts}
            rowKey={(f) => f.id}
            onRowClick={(f) => navigate(`/admin/facts/${f.id}`)}
            empty='还没有政策文件。选择文件后点击“上传并分析”。'
            columns={[
              {
                header: "标题",
                // 真 <Link> 而不是只靠整行 onRowClick —— 后者会让用户失去
                // 中键 / ⌘+点击开新标签、右键"在新标签页打开"、以及悬停看
                // 目标 URL。整行可点只是"点空白处也能进去"的便利。
                cell: (f) => (
                  <Link
                    to={`/admin/facts/${f.id}`}
                    style={{ fontWeight: 600, color: "var(--text)" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {f.title}
                  </Link>
                ),
              },
              { header: "状态", cell: (f) => <StatusBadge status={f.status} /> },
              {
                header: "文件",
                cell: (f) => (
                  <span style={{ color: "var(--text-muted)" }} title={f.original_filename}>
                    {f.original_filename}
                  </span>
                ),
              },
              {
                header: "大小",
                align: "right",
                nowrap: true,
                cell: (f) => (
                  <span style={{ color: "var(--text-muted)" }}>{fmtSize(f.size_bytes)}</span>
                ),
              },
              {
                header: "受影响",
                align: "right",
                cell: (f) => f.impacts_count ?? "-",
              },
              {
                header: "待处理建议",
                align: "right",
                cell: (f) => f.patches_count ?? "-",
              },
              {
                header: "上传",
                nowrap: true,
                cell: (f) => (
                  <span style={{ color: "var(--text-muted)" }} title={f.uploaded_by || undefined}>
                    {fmtTime(f.uploaded_at_ms)}
                  </span>
                ),
              },
            ]}
          />
        )}
      </Section>
    </PageShell>
  );
}

// 7/30: 原来的 FactRow 删了 —— 一条记录占 84px 装 8 个字段 (标题/状态/文件名/
// 大小/时间/上传者/受影响数/patch 数), 表格里同样 8 个字段是一行 29px。
// 一屏从 5 条变 15 条。上传者收进标题 tooltip, 因为它几乎总是同一个人。

/** 7/30: 改用共享 Badge。
 *
 * 原来这里是本文件自己的实现, 而它**没写 display:inline-block** ——
 * 之前它是 FactRow 里 flex 容器的直接子项, 会被 blockify, 所以纵向 padding
 * 生效; 挪进 <td> 之后就是个普通 inline span, **纵向 padding 不撑行盒高度**,
 * 有色背景会溢出到行分隔线上。密度提上来之后这种溢出很明显。
 *
 * 颜色映射到语义色: 待审批是要人动手的 → warn; 已采纳 → ok; 其余中性。
 */
// ── 上传表单 ─────────────────────────────────

function UploadForm({ onUploaded: _onUploaded }: { onUploaded: () => void }) {
  // 上传完直接 window.location.href 跳转, 不走 props callback (parent refresh
  // 通过 navigate 自动触发, _onUploaded 留接口防 future 真要回调)
  const fileRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [effectiveDate, setEffectiveDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setErr("请先选文件");
      return;
    }
    setBusy(true);
    try {
      const res = await factsApi.upload(file, title || file.name, effectiveDate);
      // 上传完直接跑 analyze (体验顺滑, 让员工不用再点一次"分析")
      // analyze 内部会自动 extract + find_impact + generate_patches, 30s-2min
      // 先 navigate 去详情页让员工看到 spinner, 不阻塞这边 UI
      window.location.href = `/admin/facts/${res.fact_id}?auto-analyze=1`;
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(220px, 1.4fr) minmax(180px, 1fr) 150px auto", gap: 10, alignItems: "end", marginTop: "var(--space-3)" }}>
      <label style={fieldLabel}>
        政策文件
        <input ref={fileRef} type="file" accept=".pdf,.docx,.doc,.md,.markdown,.txt" disabled={busy} style={inputStyle} />
      </label>
      <label style={fieldLabel}>
        文件标题（可选）
        <input type="text" placeholder="默认使用文件名" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} style={inputStyle} />
      </label>
      <label style={fieldLabel}>
        生效日期（可选）
        <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} disabled={busy} style={inputStyle} />
      </label>
      <button onClick={() => void submit()} disabled={busy} style={busy ? { ...btnPrimary, opacity: 0.5, cursor: "wait" } : btnPrimary}>
        {busy ? "分析中…" : "上传并分析"}
      </button>
      {err && <div style={errBox}>{err}</div>}
      <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--text-muted)" }}>支持 PDF、Word、Markdown 和文本文件，上传后会自动分析影响范围。</div>
    </div>
  );
}

const fieldLabel: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 5,
  fontSize: 12,
  fontWeight: 600,
};
