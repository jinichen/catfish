/**
 * 知识体系「约束检查」(11/26 鸿波「现在的本体是不是少了约束条件」)。
 *
 * 写入口 (属性卡片 / 整理属性 / 小鲶改知识) 只拦这次改到的值; 知识库里早就写着的不合规在这里列出来:
 * 必填没填、值不在选项里 / 格式不对、编号重复、日期先后不对、一个人隶属两个部门、隶属绕成环、属性跟关系各说各的。
 * 能确定改法的 (关系里隶属企发部 → 「部门」填企发部) 一键改, 一批也行; 每一项改之前都备份 (wiki/.history)。
 * 后端 tool-bridge wiki_constraints_report.py。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { X } from "@phosphor-icons/react";
import { ModalShell } from "../../components/ModalShell";
import { V, field } from "./wikiTheme";
import {
  wikiConstraintsFix,
  wikiConstraintsReport,
  type ConstraintFix,
  type ConstraintIssue,
  type ConstraintKind,
  type ConstraintReport,
} from "../../lib/tauri_wiki_types";
import { useWikiStore } from "../../store/wiki";

export const KIND_LABEL: Record<ConstraintKind, string> = {
  bad_value: "值不合规",
  duplicate: "编号重复",
  rule: "日期先后不对",
  relation_max: "关系超数量",
  relation_cycle: "关系绕成环",
  link_conflict: "属性和关系不一致",
  link_prop: "属性没填（关系里有）",
  link_relation: "缺关系（属性里有）",
  missing_required: "必填没填",
};
const ORDER = Object.keys(KIND_LABEL) as ConstraintKind[];
const SHOW = 200;

const chip = (active: boolean) => ({
  padding: "4px 10px", borderRadius: 999, fontSize: 12, cursor: "pointer",
  border: `1px solid ${active ? V.accent : V.border}`, background: active ? V.accentSoft : "transparent", color: V.text,
}) as const;
const small = { border: `1px solid ${V.border}`, borderRadius: 6, background: "transparent", color: V.accent, cursor: "pointer", fontSize: 12, padding: "3px 8px" } as const;
const primary = { padding: "6px 12px", border: 0, borderRadius: 8, background: V.accent, color: "#fff", cursor: "pointer", fontSize: 13 } as const;

/** 一键改的按钮上写什么 */
export function fixLabel(fix: ConstraintFix): string {
  if (fix.op === "set_prop") return fix.value ? `填成「${fix.value}」` : "清掉这个值";
  if (fix.op === "add_relation") return "补上关系";
  return `关系改成「${fix.name}」`;
}

/** 改法完整、不用人再填的 (能一批改) */
export const autoFixable = (i: ConstraintIssue) =>
  !!i.fix && (i.fix.op !== "set_prop" || !!i.fix.value) && (i.kind === "link_prop" || i.kind === "link_relation");

function IssueRow({ issue, busy, run, open }: {
  issue: ConstraintIssue;
  busy: boolean;
  run: (items: (ConstraintFix & { rel_path: string })[]) => Promise<void>;
  open: (relPath: string) => void;
}) {
  const [value, setValue] = useState("");
  const needsValue = issue.kind === "bad_value" && issue.fix?.op === "set_prop";
  return (
    <div style={{ display: "grid", gap: 4, padding: "8px 0", borderBottom: `1px solid ${V.border}` }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <button type="button" onClick={() => open(issue.rel_path)}
          style={{ border: 0, background: "transparent", padding: 0, color: V.text, fontWeight: 600, fontSize: 13, cursor: "pointer", textAlign: "left" }}>
          {issue.title}
        </button>
        <span style={{ fontSize: 12, color: V.muted }}>{issue.type_label}</span>
      </div>
      <div style={{ fontSize: 13, color: V.muted, lineHeight: 1.5, overflowWrap: "anywhere" }}>{issue.message}</div>
      {(issue.fix || issue.alt_fix) && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          {needsValue ? (
            <>
              {issue.options?.length ? (
                <select style={{ ...field, width: 160 }} value={value} onChange={(e) => setValue(e.target.value)} aria-label={`${issue.label}改成`}>
                  <option value="">选一个</option>
                  {issue.options.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              ) : (
                <input style={{ ...field, width: 200 }} value={value} onChange={(e) => setValue(e.target.value)} placeholder="改成" aria-label={`${issue.label}改成`} />
              )}
              <button type="button" style={small} disabled={busy || !value.trim()}
                onClick={() => void run([{ ...issue.fix!, value: value.trim(), rel_path: issue.rel_path }])}>改</button>
              <button type="button" style={small} disabled={busy}
                onClick={() => void run([{ ...issue.fix!, value: "", rel_path: issue.rel_path }])}>清掉</button>
            </>
          ) : (
            <>
              {issue.fix && <button type="button" style={small} disabled={busy} onClick={() => void run([{ ...issue.fix!, rel_path: issue.rel_path }])}>{fixLabel(issue.fix)}</button>}
              {issue.alt_fix && <button type="button" style={small} disabled={busy} onClick={() => void run([{ ...issue.alt_fix!, rel_path: issue.rel_path }])}>{`属性${fixLabel(issue.alt_fix)}`}</button>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function WikiConstraintsModal({ onClose }: { onClose: () => void }) {
  const selectFile = useWikiStore((s) => s.selectFile);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  const [report, setReport] = useState<ConstraintReport | null>(null);
  const [kind, setKind] = useState<ConstraintKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await wikiConstraintsReport();
      setReport(r);
      setKind((k) => (k && r.summary[k] ? k : ORDER.find((x) => r.summary[x]) ?? null));
    } catch (e) {
      setError(`约束检查失败：${String(e instanceof Error ? e.message : e)}`);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => (report?.issues ?? []).filter((i) => !kind || i.kind === kind), [report, kind]);
  const batch = shown.filter(autoFixable);

  const run = async (items: (ConstraintFix & { rel_path: string })[]) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await wikiConstraintsFix(items);
      setNotice(`改好 ${r.done} 项${r.errors.length ? `，${r.errors.length} 项没改成` : ""}（改之前都备份了）`);
      if (r.errors.length) setError(r.errors.slice(0, 3).map((e) => e.error).join("；"));
      await load();
      void loadFiles();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalShell onClose={onClose} maxWidth={960} themed>
      <div style={{ width: "100%", fontFamily: V.font, color: V.text }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <h3 style={{ margin: 0, fontSize: 17 }}>约束检查</h3>
          <button type="button" onClick={onClose} aria-label="关闭" style={{ border: 0, background: "transparent", cursor: "pointer", color: V.muted }}>
            <X size={16} />
          </button>
        </div>
        <p style={{ margin: "0 0 12px", color: V.muted, fontSize: 13, lineHeight: 1.5 }}>
          新写进去的属性和关系都会先检查；这里列的是知识库里早就写着、不符合类型定义的。选项、必填、能不能重复在「类型定义」里改。
        </p>
        {error && <p style={{ color: V.error, fontSize: 13, margin: "0 0 8px" }}>{error}</p>}
        {notice && <p style={{ color: V.text, fontSize: 13, margin: "0 0 8px" }}>{notice}</p>}
        {!report ? (
          !error && <p style={{ fontSize: 13, color: V.muted }}>检查中…</p>
        ) : report.total === 0 ? (
          <p style={{ fontSize: 13 }}>没有发现问题。</p>
        ) : (
          <>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
              {ORDER.filter((k) => report.summary[k]).map((k) => (
                <button key={k} type="button" style={chip(kind === k)} aria-pressed={kind === k} onClick={() => setKind(k)}>
                  {KIND_LABEL[k]} {report.summary[k]}
                </button>
              ))}
            </div>
            {kind === "missing_required" && (
              <div style={{ fontSize: 12, color: V.muted, marginBottom: 8, lineHeight: 1.6 }}>
                {report.types.filter((t) => Object.keys(t.missing).length).map((t) => (
                  <div key={t.key}>{t.label}（{t.entries} 条）：{Object.entries(t.missing).map(([k, n]) => `${k} 缺 ${n}`).join("，")}</div>
                ))}
                <div>必填只提示、不拦保存。在知识页的属性卡片上补，或者「整理属性」让小鲶从正文和原件里找。</div>
              </div>
            )}
            {batch.length > 1 && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                <button type="button" style={primary} disabled={busy}
                  onClick={() => void run(batch.map((i) => ({ ...i.fix!, rel_path: i.rel_path })))}>
                  {busy ? "改着…" : `这 ${batch.length} 项全部按建议改`}
                </button>
                <small style={{ color: V.muted, fontSize: 12 }}>只改这一类里改法确定的，每页改前备份</small>
              </div>
            )}
            <div style={{ maxHeight: "56vh", overflowY: "auto", overflowX: "hidden", paddingRight: 4 }}>
              {shown.slice(0, SHOW).map((i, n) => (
                <IssueRow key={`${i.rel_path}:${i.kind}:${i.key ?? ""}:${n}`} issue={i} busy={busy} run={run}
                  open={(rel) => { void selectFile(rel); onClose(); }} />
              ))}
              {shown.length > SHOW && <p style={{ fontSize: 12, color: V.muted }}>还有 {shown.length - SHOW} 条没列出来，先改掉上面的再看。</p>}
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
