/** /admin/facts/:id — 事实详情 + 受影响 skill + patch 采纳/拒绝 (8/1 拆出).
 *
 * 拆分理由见 factsShared.tsx 文件头。
 *
 * ## 三个确认对话框
 *
 * 这一页的三个操作原来都是 `window.confirm`。它们的**后果轻重完全不同**,
 * 而原生 confirm 长得一模一样:
 *
 *   撤销 fact   只标 dismissed, 不删文件           轻
 *   拒绝 patch  标 rejected, 之后可以重新分析生成   轻
 *   采纳 patch  **立刻在 SkillsHub 发布新版本**,   重 —— 全公司员工下次
 *               所有员工都会拉到                    拉 skill 就会拿到
 *
 * 采纳那条实际不覆盖任何东西 (版本号自动加 `.fact-<id前8位>` 后缀, 后端
 * facts_router.approve_patch 的 docstring 写明了), 而且重复采纳是幂等的
 * —— 所以不算 danger。但"立刻对所有人生效"这件事必须在点之前说清楚。
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { Card, Row } from "../../components/Card";
import { ConfirmDialog } from "../../components/Dialog";
import { PageShell } from "../../components/PageShell";
import {
  factsApi,
  type FactDetail as FactDetailType,
  type SkillImpact,
  type SkillPatch,
} from "../../lib/facts";
import {
  btnDanger,
  btnPrimary,
  btnSecondary,
  errBox,
  fmtSize,
  fmtTime,
  infoBox,
  StatusBadge,
} from "./factsShared";


export function FactDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<FactDetailType | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  /** 撤销确认。原来是 window.confirm。 */
  const [dismissing, setDismissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dismissErr, setDismissErr] = useState<string | null>(null);

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
    setBusy(true);
    setDismissErr(null);
    try {
      await factsApi.dismiss(id);
      navigate("/admin/facts");
    } catch (e) {
      // 留在对话框里 —— 抛到页面上的话正被遮罩盖着, 看起来像"点了没反应"。
      setDismissErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
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
              <button onClick={() => setDismissing(true)} style={btnDanger}>
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
    {dismissing && (
        <ConfirmDialog
          title="撤销这条政策?"
          confirmLabel="撤销"
          busy={busy}
          onCancel={() => {
            setDismissing(false);
            setDismissErr(null);
          }}
          onConfirm={() => void dismiss()}
        >
          标成 <code>dismissed</code>，<b>不删文件、不动已经发布过的 skill 版本</b>。
          <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
            列表里默认还看得到它，只是不再参与后续分析。
          </div>
          {dismissErr && (
            <div style={{ marginTop: 8, color: "var(--status-err)" }}>
              撤销失败：{dismissErr}
            </div>
          )}
        </ConfirmDialog>
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
  /** 正在确认哪个操作。两个后果差得远, 所以对话框正文完全不同。 */
  const [pending, setPending] = useState<"approve" | "reject" | null>(null);
  /** 采纳成功后发布到了哪个版本。原来是 alert 弹一次就没了 ——
   *  而这两个坐标 (版本号 + hub 路径) 正是之后去 SkillsHub 核对要用的。 */
  const [published, setPublished] = useState<string | null>(() => {
    // 上一次采纳的结果 (如果这一轮渲染是从"刷新失败 → 重试成功"回来的)。
    try {
      return sessionStorage.getItem(`catfish.published.${factId}.${patchIdx}`);
    } catch {
      return null;
    }
  });

  const handleApprove = async () => {
    setBusy(true);
    setActionErr(null);
    try {
      const res = await factsApi.approvePatch(factId, patchIdx);
      const published0 =
        `已发布 ${res.published_version} → ` +
        `${res.hub_result.namespace}/${res.hub_result.name}/${res.hub_result.version}`;
      setPending(null);
      setPublished(published0);
      // ⚠ onChanged() 会触发父组件重新拉详情, 而父组件在拉失败时是**早返回**
      // 的 (`if (err) return <div style={errBox}>`) —— 整页被一张错误卡换掉,
      // 刚发布的版本号和 hub 坐标一起没了, 而那两个正是之后去核对要用的。
      // 所以先把它们记进 sessionStorage, 页面回来时还能捞到。
      try {
        sessionStorage.setItem(`catfish.published.${factId}.${patchIdx}`, published0);
      } catch {
        // 隐私模式下 sessionStorage 可能抛 —— 记不住就算了, 不该因此报错。
      }
      onChanged();
    } catch (e) {
      // 留在对话框里, 不抛到卡片上 —— 对话框开着时卡片被遮罩盖着。
      setActionErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    setBusy(true);
    setActionErr(null);
    try {
      await factsApi.rejectPatch(factId, patchIdx);
      setPending(null);
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
              onClick={() => setPending("approve")}
              disabled={busy}
            >
              {busy ? "处理中…" : "采纳 → 发布到 SkillsHub"}
            </button>
            <button
              style={busy ? { ...btnSecondary, opacity: 0.5, cursor: "wait" } : btnSecondary}
              onClick={() => setPending("reject")}
              disabled={busy}
            >
              拒绝
            </button>
          </>
        )}
      </div>
      {/* 采纳成功后把发布坐标留在卡片上, 不是弹一下就没。之后去 SkillsHub
          核对靠的就是这两个值。 */}
      {published && (
        <div style={{ ...infoBox, marginTop: 6 }}>✓ {published}</div>
      )}
      {/* 对话框关着时才在卡片上显示错误 —— 开着时它被遮罩盖住, 错误要在
          对话框正文里 (见下面)。 */}
      {actionErr && !pending && <div style={errBox}>{actionErr}</div>}

      {pending === "approve" && (
        <ConfirmDialog
          title="采纳这个改动, 现在就发布?"
          confirmLabel="采纳并发布"
          busy={busy}
          onCancel={() => {
            setPending(null);
            setActionErr(null);
          }}
          onConfirm={() => void handleApprove()}
        >
          会在 SkillsHub 发布{" "}
          <code>
            {patch.skill_namespace}/{patch.skill_name}
          </code>{" "}
          的新版本，<b>所有员工下次拉这个 skill 就会拿到</b>。
          <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
            {/* 后端 facts_router.approve_patch 的 docstring 写明: 版本号强制
                加 `.fact-<id前8位>` 后缀。所以它是"多一个版本", 不是"改掉
                现有版本" —— 这句能省掉一次"会不会把线上覆盖掉"的担心。 */}
            版本号自动加 <code>.fact-{"{id 前 8 位}"}</code> 后缀，
            <b>不覆盖现有版本</b>；重复采纳同一条不会重复发布。
          </div>
          {actionErr && (
            <div style={{ marginTop: 8, color: "var(--status-err)" }}>
              发布失败：{actionErr}
            </div>
          )}
        </ConfirmDialog>
      )}

      {pending === "reject" && (
        <ConfirmDialog
          title="拒绝这个改动?"
          confirmLabel="拒绝"
          busy={busy}
          onCancel={() => {
            setPending(null);
            setActionErr(null);
          }}
          onConfirm={() => void handleReject()}
        >
          标成 <code>rejected</code>，不发布任何东西。
          <div style={{ marginTop: 6, color: "var(--text-muted)" }}>
            之后重新分析这条政策会重新生成 patch，所以这一步是可以反悔的。
          </div>
          {actionErr && (
            <div style={{ marginTop: 8, color: "var(--status-err)" }}>
              操作失败：{actionErr}
            </div>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}
