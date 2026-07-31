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
import { Badge, BTN, DataTable, Section, type BadgeTone } from "../../components/DataTable";
import { PageShell } from "../../components/PageShell";
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
    <PageShell>
      <Section
        title="政策同步 / 事实补丁"
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

      <Section title={`已上传${facts ? ` · ${facts.length} 条` : ""}`}>
        {err && <div style={errBox}>错误: {err}</div>}
        {!facts && !err && (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>加载中…</div>
        )}
        {facts && (
          <DataTable
            rows={facts}
            rowKey={(f) => f.id}
            onRowClick={(f) => navigate(`/admin/facts/${f.id}`)}
            empty='还没上传过。用上面的"上传 + 分析"开始。'
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
                header: "patch",
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
function StatusBadge({ status }: { status: FactStatus }) {
  const MAP: Record<FactStatus, [BadgeTone, string]> = {
    uploaded: ["neutral", "已上传"],
    extracted: ["neutral", "已解析"],
    analyzed: ["accent", "已分析"],
    patches_ready: ["warn", "待审批"],
    approved: ["ok", "已采纳"],
    dismissed: ["neutral", "已撤销"],
  };
  const [tone, label] = MAP[status] ?? (["neutral", status] as [BadgeTone, string]);
  return <Badge tone={tone}>{label}</Badge>;
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
    <PageShell gap="var(--space-4)">
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
        <Card
          title={`✏️ 生成的 patches · ${detail.patches.length} 个 · ${
            detail.patches.filter((p) => p.status === "pending").length
          } 待审批 / ${
            detail.patches.filter((p) => p.status === "approved").length
          } 已采纳 / ${
            detail.patches.filter((p) => p.status === "rejected").length
          } 已拒绝`}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {detail.patches.map((p, i) => (
              <PatchCard
                key={i}
                patch={p}
                patchIdx={i}
                factId={id || ""}
                onChanged={() => void refresh()}
              />
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
    </PageShell>
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

function PatchCard({
  patch,
  patchIdx,
  factId,
  onChanged,
}: {
  patch: SkillPatch;
  patchIdx: number;
  factId: string;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const handleApprove = async () => {
    if (busy) return;
    if (!confirm(
      `采纳后会立即在 SkillsHub 发布新版本 ${patch.skill_namespace}/${patch.skill_name}.\n\n` +
      `version 会自动改成 <原 v>.fact-<id 前 8 位>, 不会覆盖现有版本.\n\n` +
      "继续?",
    )) return;
    setBusy(true);
    setActionErr(null);
    try {
      const res = await factsApi.approvePatch(factId, patchIdx);
      alert(`✓ 已发布新版本: ${res.published_version}\nSkillsHub: ${res.hub_result.namespace}/${res.hub_result.name}/${res.hub_result.version}`);
      onChanged();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (busy) return;
    if (!confirm("拒绝这个 patch? 标 rejected, 不会发布. 之后可重新分析重新生成 patch.")) return;
    setBusy(true);
    setActionErr(null);
    try {
      await factsApi.rejectPatch(factId, patchIdx);
      onChanged();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const isApproved = patch.status === "approved";
  const isRejected = patch.status === "rejected";
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

      {/* 7/30: diff 原来是永远展开的。一个 3 处改动的 patch 轻松 300-500px,
          一屏经常只看得到 1-2 个 patch —— 而这一页的核心动作是**逐个 patch
          决定采纳还是拒绝**, 采纳按钮永远在视野外。

          荒唐的是隔壁 587 行早就有个 <details> 折叠"完整改后内容", 偏偏
          没折叠 diff 本身。默认收起后, 一屏能看到 5-6 个 patch 的标题 + 理由,
          想细看再展开哪一个。

          改动描述 (c.description) **留在折叠外面** —— 每条一行, 而它恰恰是
          决定"这条要不要展开细看"的依据。只折叠 old/new 代码块。
          (第一版把 description 一起收进去了, 结果收起状态下一个 patch 只剩
           "理由"加一句"看具体改了什么(3 处)", 等于零信息, 没法判断。) */}
      <div style={{ marginTop: 8 }}>
        {patch.changes.map((c, i) => (
          <div key={i} style={{ marginTop: 8, fontSize: 12 }}>
            <div style={{ color: "var(--text-muted)" }}>▸ {c.description}</div>
            <details>
              <summary
                style={{
                  cursor: "pointer",
                  fontSize: 11,
                  color: "var(--accent)",
                  marginTop: 2,
                }}
              >
                看改动内容
              </summary>
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
            </details>
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

      <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
        {isApproved ? (
          <span style={{ ...btnSecondary, color: "#16a34a", cursor: "default" }}>
            ✓ 已采纳, 发布版本 {(patch as SkillPatch & { published_version?: string }).published_version || "(未知)"}
          </span>
        ) : isRejected ? (
          <span style={{ ...btnSecondary, color: "#888", cursor: "default" }}>
            ✗ 已拒绝
          </span>
        ) : (
          <>
            <button
              style={busy ? { ...btnPrimary, opacity: 0.5, cursor: "wait" } : btnPrimary}
              onClick={() => void handleApprove()}
              disabled={busy}
            >
              {busy ? "处理中…" : "采纳 → 发布到 SkillsHub"}
            </button>
            <button
              style={busy ? { ...btnSecondary, opacity: 0.5, cursor: "wait" } : btnSecondary}
              onClick={() => void handleReject()}
              disabled={busy}
            >
              拒绝
            </button>
          </>
        )}
      </div>
      {actionErr && <div style={errBox}>{actionErr}</div>}
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
