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
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../../components/Card";
import { RoleGate } from "../../components/RoleGate";
import {
  factsApi,
  type FactDetail,
  type FactMeta,
  type FactStatus,
  type SkillImpact,
  type SkillPatch,
} from "../../lib/facts";

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
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <Card
        title="📋 政策同步 / 事实补丁"
        action={
          <button onClick={() => void refresh()} style={btnSecondary}>
            刷新
          </button>
        }
      >
        <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
          上传公司政策/标准/流程变更文件, 鲶鱼自动找出受影响的 skill 并生成改进 patch,
          走审批后落到员工 Companion. 数据 100% 本地, 不出公司.
        </p>
        <UploadForm onUploaded={() => void refresh()} />
      </Card>

      <Card title={`已上传 fact 列表${facts ? ` · ${facts.length} 条` : ""}`}>
        {err && <div style={errBox}>错误: {err}</div>}
        {!facts && !err && <div style={{ color: "var(--text-muted)" }}>加载中…</div>}
        {facts && facts.length === 0 && (
          <div style={{ color: "var(--text-muted)", padding: "var(--space-3) 0" }}>
            还没上传过 fact. 用上面的"上传变更文件"按钮开始.
          </div>
        )}
        {facts && facts.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {facts.map((f) => (
              <FactRow key={f.id} f={f} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function FactRow({ f }: { f: FactMeta }) {
  return (
    <Link
      to={`/admin/facts/${f.id}`}
      style={{
        display: "block",
        padding: "var(--space-3)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-elev)",
        color: "var(--text)",
        textDecoration: "none",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <strong style={{ fontSize: 14 }}>{f.title}</strong>
        <StatusBadge status={f.status} />
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
        {f.original_filename} · {fmtSize(f.size_bytes)} · 上传于 {fmtTime(f.uploaded_at_ms)}
        {f.uploaded_by && ` · by ${f.uploaded_by}`}
      </div>
      {(f.impacts_count !== undefined || f.patches_count !== undefined) && (
        <div style={{ fontSize: 12, marginTop: 4 }}>
          受影响 skill: <b>{f.impacts_count ?? "-"}</b> · 生成 patch: <b>{f.patches_count ?? "-"}</b>
        </div>
      )}
    </Link>
  );
}

function StatusBadge({ status }: { status: FactStatus }) {
  const colorMap: Record<FactStatus, [string, string]> = {
    uploaded: ["#888", "已上传"],
    extracted: ["#3b82f6", "已解析"],
    analyzed: ["#3b82f6", "已分析"],
    patches_ready: ["#22c55e", "待审批"],
    approved: ["#16a34a", "已采纳"],
    dismissed: ["#888", "已撤销"],
  };
  const [color, label] = colorMap[status] || ["#888", status];
  return (
    <span
      style={{
        background: color,
        color: "white",
        padding: "2px 8px",
        borderRadius: "var(--radius-sm)",
        fontSize: 11,
        fontWeight: 500,
        flexShrink: 0,
      }}
    >
      {label}
    </span>
  );
}

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
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: "var(--space-3)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.doc,.md,.markdown,.txt"
          disabled={busy}
          style={{ fontSize: 12 }}
        />
        <input
          type="text"
          placeholder="标题 (可选, 默认用文件名)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
          style={{ ...inputStyle, flex: "1 1 200px", minWidth: 0 }}
        />
        <input
          type="date"
          value={effectiveDate}
          onChange={(e) => setEffectiveDate(e.target.value)}
          disabled={busy}
          style={inputStyle}
          title="生效日期 (可选)"
        />
        <button
          onClick={() => void submit()}
          disabled={busy}
          style={busy ? { ...btnPrimary, opacity: 0.5, cursor: "wait" } : btnPrimary}
        >
          {busy ? "上传中…" : "上传 + 分析"}
        </button>
      </div>
      {err && <div style={errBox}>{err}</div>}
      <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
        支持 .pdf / .docx / .md / .txt, 单文件 ≤ 20MB. 上传完会自动进分析阶段
        (LLM 提事实点 + 找受影响 skill + 生成 patch, 通常 30 秒 - 2 分钟).
      </div>
    </div>
  );
}

// ── 详情 ─────────────────────────────────────

function FactDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<FactDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);

  const refresh = async () => {
    if (!id) return;
    setErr(null);
    try {
      const d = await factsApi.get(id);
      setDetail(d);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  // 首次加载 + auto-analyze 自动触发分析
  useEffect(() => {
    if (!id) return;
    void (async () => {
      await refresh();
      const params = new URLSearchParams(window.location.search);
      if (params.get("auto-analyze") === "1") {
        // 清掉 query param, 防刷新页面重复触发
        window.history.replaceState({}, "", `/admin/facts/${id}`);
        await runAnalyze();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const runAnalyze = async () => {
    if (!id || analyzing) return;
    setAnalyzing(true);
    setErr(null);
    try {
      await factsApi.analyze(id);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const dismiss = async () => {
    if (!id) return;
    if (!confirm("确认撤销这个 fact? 不会真删, 只标 dismissed.")) return;
    try {
      await factsApi.dismiss(id);
      navigate("/admin/facts");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  if (err) return <div style={errBox}>错误: {err}</div>;
  if (!detail) return <div style={{ color: "var(--text-muted)" }}>加载中…</div>;

  const m = detail.meta;
  const factPoints = detail.facts?.facts || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Link to="/admin/facts" style={{ color: "var(--accent)", fontSize: 13 }}>
          ← 返回列表
        </Link>
        <div style={{ display: "flex", gap: 8 }}>
          {m.status !== "dismissed" && (
            <>
              <button onClick={() => void runAnalyze()} disabled={analyzing} style={btnSecondary}>
                {analyzing ? "分析中… (30s-2min)" : "重新分析"}
              </button>
              <button onClick={() => void dismiss()} style={btnDanger}>
                撤销
              </button>
            </>
          )}
        </div>
      </div>

      <Card
        title={m.title}
        action={<StatusBadge status={m.status} />}
      >
        <Row label="文件" value={m.original_filename} />
        <Row label="大小" value={fmtSize(m.size_bytes)} />
        <Row label="生效日期" value={m.effective_date || "未指定"} />
        <Row label="上传者" value={m.uploaded_by} />
        <Row label="上传时间" value={fmtTime(m.uploaded_at_ms)} />
        {detail.facts?.summary && (
          <div style={{ marginTop: "var(--space-3)", fontSize: 13, color: "var(--text)" }}>
            <strong>LLM 摘要:</strong> {detail.facts.summary}
          </div>
        )}
        {detail.facts?.error && (
          <div style={errBox}>LLM 解析错误: {detail.facts.error}</div>
        )}
      </Card>

      {factPoints.length > 0 && (
        <Card title={`📌 提取出的事实点 · ${factPoints.length} 条`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {factPoints.map((p, i) => (
              <div
                key={p.id || i}
                style={{
                  padding: "var(--space-3)",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <strong style={{ fontSize: 13 }}>{p.title}</strong>
                  <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{p.category}</span>
                </div>
                <div style={{ fontSize: 13, color: "var(--text)", marginTop: 4 }}>
                  {p.summary}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                  关键词: {p.keywords?.join(" / ") || "-"} · 影响范围: {p.impact_scope || "-"}
                </div>
                {p.raw_quote && (
                  <details style={{ marginTop: 6, fontSize: 12 }}>
                    <summary style={{ color: "var(--accent)", cursor: "pointer" }}>原文引用</summary>
                    <blockquote
                      style={{
                        margin: "6px 0 0 0",
                        padding: "8px 12px",
                        borderLeft: "3px solid var(--accent)",
                        color: "var(--text-muted)",
                        fontFamily: "var(--font-mono)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      {p.raw_quote}
                    </blockquote>
                  </details>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}

      {detail.impacts.length > 0 && (
        <Card title={`🎯 受影响 skill · ${detail.impacts.length} 个`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {detail.impacts.map((imp, i) => (
              <ImpactRow key={i} imp={imp} />
            ))}
          </div>
        </Card>
      )}

      {detail.patches.length > 0 && (
        <Card title={`✏️ 生成的 patches · ${detail.patches.length} 个 (待审批)`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {detail.patches.map((p, i) => (
              <PatchCard key={i} patch={p} />
            ))}
          </div>
        </Card>
      )}

      {analyzing && (
        <div style={infoBox}>
          ⏳ LLM 正在分析中… 这个过程包含: 提取事实点 → 拉 skills-hub 全部 skill → 找受影响 →
          对每个生成 patch. 时间取决于 skill 数量和 LLM 速度, 一般 30 秒 - 2 分钟.
        </div>
      )}

      {detail.audit.length > 0 && (
        <Card title="📜 操作审计">
          <div style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
            {detail.audit.slice().reverse().map((e, i) => (
              <div key={i} style={{ padding: "4px 0", borderBottom: "1px dotted var(--border)" }}>
                <span style={{ color: "var(--text-muted)" }}>{fmtTime(e.ts_ms)}</span>{" "}
                <strong>{e.action}</strong> by {e.by_user}
                {Object.keys(e.meta).length > 0 && (
                  <span style={{ color: "var(--text-muted)" }}>
                    {" "}· {JSON.stringify(e.meta)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function ImpactRow({ imp }: { imp: SkillImpact }) {
  const conf = (imp.confidence * 100).toFixed(0);
  const confColor = imp.confidence >= 0.8 ? "#16a34a" : imp.confidence >= 0.6 ? "#ca8a04" : "#888";
  return (
    <div
      style={{
        padding: "var(--space-3)",
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 13, fontFamily: "var(--font-mono)" }}>
          {imp.skill_namespace}/{imp.skill_name}
        </strong>
        <span style={{ fontSize: 12, color: confColor, fontWeight: 500 }}>
          confidence {conf}%
        </span>
      </div>
      <div style={{ fontSize: 13, color: "var(--text)", marginTop: 4 }}>{imp.reason}</div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
        对应事实: {imp.fact_summary} · 检测方式: {imp.detection_method}
      </div>
    </div>
  );
}

function PatchCard({ patch }: { patch: SkillPatch }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      style={{
        padding: "var(--space-3)",
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <strong style={{ fontSize: 13, fontFamily: "var(--font-mono)" }}>
          {patch.skill_namespace}/{patch.skill_name} v{patch.skill_version_base} → 新版
        </strong>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {patch.changes.length} 处改动
        </span>
      </div>
      <div style={{ fontSize: 13, color: "var(--text)", marginTop: 6 }}>
        <strong>理由:</strong> {patch.rationale}
      </div>

      <div style={{ marginTop: 8 }}>
        {patch.changes.map((c, i) => (
          <div key={i} style={{ marginTop: 8, fontSize: 12 }}>
            <div style={{ color: "var(--text-muted)" }}>▸ {c.description}</div>
            <div
              style={{
                background: "rgba(239, 68, 68, 0.08)",
                padding: "4px 8px",
                marginTop: 4,
                fontFamily: "var(--font-mono)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              <span style={{ color: "#c43f3f" }}>− </span>
              {c.old_snippet}
            </div>
            <div
              style={{
                background: "rgba(34, 197, 94, 0.08)",
                padding: "4px 8px",
                marginTop: 2,
                fontFamily: "var(--font-mono)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              <span style={{ color: "#16a34a" }}>+ </span>
              {c.new_snippet}
            </div>
          </div>
        ))}
      </div>

      <details
        open={expanded}
        onToggle={(e) => setExpanded((e.target as HTMLDetailsElement).open)}
        style={{ marginTop: 8, fontSize: 12 }}
      >
        <summary style={{ color: "var(--accent)", cursor: "pointer" }}>
          完整改后内容 ({patch.full_new_content.length} 字)
        </summary>
        <pre
          style={{
            marginTop: 6,
            padding: 12,
            background: "var(--bg-elev)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            fontFamily: "var(--font-mono)",
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 400,
            overflow: "auto",
          }}
        >
          {patch.full_new_content}
        </pre>
      </details>

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button
          style={btnPrimary}
          onClick={() => alert("P0 MVP 还没接 SkillRevisionCard 落盘, Day 3 接通. 当前只是预览.")}
        >
          采纳 (Day 3 接通)
        </button>
        <button
          style={btnSecondary}
          onClick={() => alert("P0 MVP 不实现拒绝, Day 3 加")}
        >
          拒绝
        </button>
      </div>
    </div>
  );
}

// ── 工具 / 样式 ───────────────────────────────

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function fmtTime(ms: number): string {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const inputStyle: React.CSSProperties = {
  padding: "6px 10px",
  fontSize: 13,
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--bg)",
  color: "var(--text)",
};

const btnPrimary: React.CSSProperties = {
  background: "var(--accent)",
  color: "white",
  border: "none",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: 500,
};

const btnSecondary: React.CSSProperties = {
  background: "transparent",
  color: "var(--text)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
};

const btnDanger: React.CSSProperties = {
  background: "rgba(196, 63, 63, 0.1)",
  color: "#c43f3f",
  border: "1px solid rgba(196, 63, 63, 0.3)",
  borderRadius: "var(--radius-sm)",
  padding: "6px 14px",
  fontSize: 12,
  cursor: "pointer",
};

const errBox: React.CSSProperties = {
  background: "rgba(196, 63, 63, 0.08)",
  border: "1px solid rgba(196, 63, 63, 0.3)",
  color: "#c43f3f",
  padding: "var(--space-2) var(--space-3)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12,
  marginTop: 8,
};

const infoBox: React.CSSProperties = {
  background: "rgba(45, 138, 135, 0.08)",
  border: "1px solid rgba(45, 138, 135, 0.3)",
  color: "var(--accent)",
  padding: "var(--space-3)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12,
};
