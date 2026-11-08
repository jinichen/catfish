/**
 * 一句话搭专家 (11/08, docs/EXPERT-PACK-PLAN.md §5) —— 专家页最上面一块。
 *
 * 说一句「帮我搭个资质专员: 每周一整理上周的资质邮件附件建任务, 证书快到期提醒, 回答资质政策」,
 * 小鲶起草一张总览卡: 一个专家一组, 每件活一行 (复用已有 / 新搭工作流 / 查知识库 / 还不会要先教), 定时也标出来。
 * 员工勾掉不要的、看看步骤、试跑, 点「全部保存并生效」一次落地:
 *   保存新工作流 + 存专家定义 (tool-bridge) → 建专家分身 (Rust expert_local_apply) → 挂定时。
 * 建分身失败就整张撤回 (已建的分身删掉、新工作流退回草稿), 不留半成品; 定时没挂上的单独列出来能重试。
 * 「还不会」的那件: 点「现在教」开一段教学对话, 教完技能自动接进专家、专家自动重新生效 (见 ChatToolCall)。
 * 新搭的那件直接画出步骤链; 要改就在那件活旁边说一句 (「改一改」), 有问题点「让小鲶修好」——
 * 一般人不用打开步骤编辑器 (那个留给要细调的人)。
 */
import { useEffect, useRef, useState } from "react";

import { refreshExperts } from "../../lib/expertChat";
import {
  expertApply, expertBuildApply, expertBuildDiscard, expertBuildGet, expertBuildList, expertBuildPlan, expertBuildRollback,
  expertBuildRevise, expertBuildTeach, expertBuildUpdate, expertRemove, type BuildExpert, type BuildJob, type ExpertBuild,
} from "../../lib/tauri_experts";
import { workflowScheduleSet } from "../../lib/tauri_workflows";
import { useUIStore } from "../../store/ui";
import { muted } from "../Collab/formsUi";
import { Btn, ErrorLine, inputStyle, itemStyle } from "../Collab/roomLinkUi";
import { StepChain } from "./WorkflowParts";
import { Detail } from "./WorkflowsCard";

const KIND_TEXT: Record<BuildJob["kind"], string> = {
  reuse: "用你已有的", new: "新搭工作流", knowledge: "查知识库回答", teach: "还不会 · 要你先教一遍",
};

/** 一件活怎么做的说明 (卡上那一行) */
export function jobHow(j: BuildJob): string {
  if (j.kind === "reuse") return `用你已有的「${j.use_title ?? j.use ?? ""}」`;
  if (j.kind === "teach") return j.attached ? "已经教会了" : `还不会: ${j.what ?? ""}`;
  return KIND_TEXT[j.kind];
}

/** 校验的话是写给小鲶看的: 「(用 catfish_workflow action=… 看)」这种提示、「第 4 步 (deliver_docx)」里的步骤 id
 *  员工用不上, 显示时去掉 (跟 tool-bridge workflow_lint.plain 一样) */
export function plainProblem(p: string): string {
  return p.replace(/(第 \d+(?:-\d+)? 步) \([a-z][a-z0-9_]*\)/g, "$1").replace(/\s*\((?:用|见) ?catfish_[^)]*\)/g, "").trim();
}

/** 能不能点「全部保存并生效」, 不能的话为什么 */
export function applyBlocker(b: ExpertBuild): string | null {
  const chosen = b.experts.filter((e) => e.include && e.jobs.some((j) => j.include));
  if (!chosen.length) return "一个专家都没勾";
  for (const e of chosen) {
    const bad = e.jobs.find((j) => j.include && j.kind === "new" && (j.problems.length > 0 || !j.draft));
    if (bad) return `「${e.name}」的「${bad.title}」还没搭好: 点「让小鲶修好」, 或者不勾这件`;
  }
  return null;
}

type ScheduleItem = { skill_path: string; title: string; cron: string };

export default function ExpertBuildCard({ onApplied }: { onApplied: () => void }) {
  const [sentence, setSentence] = useState("");
  const [builds, setBuilds] = useState<ExpertBuild[]>([]);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const focus = useUIStore((s) => s.pendingExpertBuild);

  const load = async () => {
    try {
      setBuilds((await expertBuildList()).builds);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!focus) return;
    useUIStore.setState({ pendingExpertBuild: null });
    void load().then(() => document.getElementById(`expert-build-${focus}`)?.scrollIntoView?.({ block: "start" }));
  }, [focus]);

  const plan = async () => {
    setPlanning(true);
    setError(null);
    try {
      const { build } = await expertBuildPlan(sentence.trim());
      setBuilds((bs) => [build, ...bs.filter((x) => x.build_id !== build.build_id)]);
      setSentence("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanning(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <strong style={{ fontSize: 13 }}>✨ 一句话搭专家</strong>
      <span style={muted}>
        说要它管哪类事、具体做哪几件, 小鲶一次起草好: 能用你已有的就用, 缺的用积木新搭工作流, 网页上点点点的会请你先教一遍。看过再保存。
      </span>
      <textarea rows={2} value={sentence} onChange={(e) => setSentence(e.target.value)} disabled={planning} style={{ ...inputStyle, resize: "vertical" }}
                aria-label="一句话说要搭什么专家"
                placeholder="例: 帮我搭个资质专员 —— 每周一整理上周的资质邮件附件建个任务, 证书快到期提前提醒我, 平时回答资质政策的问题" />
      <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end" }}>
        {planning && <span style={muted}>小鲶在拆活、搭工作流… 一般一两分钟</span>}
        <Btn kind="primary" disabled={planning || sentence.trim().length < 6} onClick={() => void plan()}>
          {planning ? "起草中…" : "起草"}
        </Btn>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {builds.map((b) => (
        <BuildView key={b.build_id} build={b} highlight={b.build_id === focus}
                   onChange={(nb) => setBuilds((bs) => bs.map((x) => (x.build_id === nb.build_id ? nb : x)))}
                   onGone={() => setBuilds((bs) => bs.filter((x) => x.build_id !== b.build_id))}
                   onApplied={onApplied} />
      ))}
    </div>
  );
}

function BuildView({ build: b, highlight, onChange, onGone, onApplied }: {
  build: ExpertBuild; highlight: boolean; onChange: (b: ExpertBuild) => void; onGone: () => void; onApplied: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failedSchedules, setFailedSchedules] = useState<ScheduleItem[]>([]);
  const [steps, setSteps] = useState<BuildJob | null>(null);
  const [persona, setPersona] = useState<number | null>(null);
  const [revising, setRevising] = useState<string | null>(null);   // 「改一改」框开在哪件活上: `${专家}-${第几件}`
  const ref = useRef<HTMLDivElement>(null);
  const draft = b.status === "draft";
  const blocker = draft ? applyBlocker(b) : null;

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  const patch = (e: BuildExpert, p: Record<string, unknown>) =>
    run("patch", async () => onChange((await expertBuildUpdate(b.build_id, e.key, p)).build));

  const schedule = async (items: ScheduleItem[]) => {
    const failed: ScheduleItem[] = [];
    for (const s of items) {
      try {
        await workflowScheduleSet(s.skill_path, s.title, s.cron);
      } catch {
        failed.push(s);
      }
    }
    setFailedSchedules(failed);
    return failed;
  };

  /** 全部保存并生效: 落地 → 一个个建分身 (失败整张撤回) → 挂定时 (没挂上的列出来能重试) */
  const applyAll = () => run("apply", async () => {
    const r = await expertBuildApply(b.build_id);
    const applied: string[] = [];
    try {
      for (const e of r.experts) {
        await expertApply(e.id);
        applied.push(e.id);
      }
    } catch (err) {
      for (const id of applied) await expertRemove(id).catch(() => {});
      const back = await expertBuildRollback(b.build_id).catch(() => null);
      if (back) onChange(back.build);
      throw new Error(`专家没建成, 已经全部撤回 (新工作流退回草稿): ${err instanceof Error ? err.message : String(err)}`);
    }
    onChange(r.build);
    const failed = await schedule(r.schedules);
    await refreshExperts();
    onApplied();
    setNotice(`搭好了: ${r.experts.map((e) => e.name).join("、")}。在工作台对话框上方「找谁」里能找到${r.experts.length > 1 ? "它们" : "它"}。`
              + (failed.length ? "" : r.schedules.length ? ` 定时也挂上了 (${r.schedules.length} 个)。` : ""));
    if (!r.build.experts.some((e) => e.include && e.jobs.some((j) => j.include && j.kind === "teach" && !j.attached))) {
      window.setTimeout(onGone, 4000);   // 都落地了、没有要教的: 卡片过一会儿收起来
    }
  });

  /** 「改一改」/「让小鲶修好」(request 空): 小鲶在现有草稿上改, 卡跟着刷新 */
  const revise = (e: BuildExpert, index: number, request: string) => run(`revise-${e.key}-${index}`, async () => {
    onChange((await expertBuildRevise(b.build_id, e.key, index, request)).build);
    setRevising(null);
  });

  const teach = (e: BuildExpert, index: number) => run("teach", async () => {
    const t = await expertBuildTeach(b.build_id, e.key, index);
    useUIStore.getState().startEmailChat(t.chat_key, t.prompt);
  });

  return (
    <div id={`expert-build-${b.build_id}`} ref={ref}
         style={{ ...itemStyle, gap: 8, border: highlight ? "1px solid var(--catfish-cyan)" : itemStyle.border }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
        <span style={{ ...muted, flex: 1 }}>「{b.sentence}」</span>
        <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          {draft && <Btn kind="ghost" disabled={!!busy} onClick={() => void run("discard", async () => { await expertBuildDiscard(b.build_id); onGone(); })}>丢掉</Btn>}
          {!draft && <span style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>✓ 已生效</span>}
        </span>
      </div>
      {b.experts.map((e) => (
        <div key={e.key} style={{ display: "flex", flexDirection: "column", gap: 4, opacity: e.include ? 1 : 0.55 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {draft && b.experts.length > 1 && (
              <input type="checkbox" checked={e.include} disabled={!!busy} aria-label={`要不要搭「${e.name}」`}
                     onChange={(ev) => void patch(e, { include: ev.target.checked })} />
            )}
            <strong style={{ fontSize: 13 }}>🧑‍💼 {e.name}</strong>
            <span style={{ ...muted, flex: 1, minWidth: 0 }}>{e.description}</span>
            {draft && <Btn kind="ghost" onClick={() => setPersona(persona === e.key ? null : e.key)}>{persona === e.key ? "收起人设" : "改人设"}</Btn>}
          </div>
          {persona === e.key && (
            <PersonaEditor expert={e} busy={!!busy} onSave={(p) => void patch(e, p).then(() => setPersona(null))} />
          )}
          {e.jobs.map((j, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: draft ? "auto 1fr auto" : "1fr auto", gap: 8, alignItems: "start",
                                  paddingLeft: 8, borderLeft: `2px solid ${j.kind === "teach" && !j.attached ? "var(--status-warn, #d97706)" : "var(--catfish-border)"}`,
                                  opacity: j.include ? 1 : 0.5 }}>
              {draft && (
                <input type="checkbox" checked={j.include} disabled={!!busy || !e.include} aria-label={`要不要「${j.title}」`}
                       onChange={(ev) => void patch(e, { jobs: [{ index: i, include: ev.target.checked }] })} />
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: 13 }}>
                  <strong>{j.title}</strong>
                  <span style={muted}> · {jobHow(j)}</span>
                  {j.schedule && <span style={{ fontSize: 12, color: "var(--catfish-cyan)", whiteSpace: "nowrap" }}> · ⏰ {j.schedule.text}</span>}
                </span>
                <span style={muted}>你会说: {j.when}</span>
                {j.kind === "new" && j.chain && j.chain.length > 0 && <StepChain chain={j.chain} />}
                {j.include && j.kind === "new" && j.problems.length > 0 && (
                  <span style={{ fontSize: 12, color: "var(--status-error, #dc2626)" }}>还没搭好: {j.problems.slice(0, 2).map(plainProblem).join("; ")}</span>
                )}
                {busy === `revise-${e.key}-${i}` && <span style={muted}>小鲶在改… 一般半分钟到两分钟</span>}
                {draft && revising === `${e.key}-${i}` && (
                  <ReviseBox busy={!!busy} onCancel={() => setRevising(null)} onSubmit={(t) => void revise(e, i, t)} />
                )}
              </div>
              <span style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                {draft && j.kind === "new" && j.include && j.problems.length > 0 && (
                  <Btn kind="primary" disabled={!!busy} onClick={() => void revise(e, i, "")}>让小鲶修好</Btn>
                )}
                {draft && j.kind === "new" && (
                  <Btn kind="ghost" disabled={!!busy} onClick={() => setRevising(revising === `${e.key}-${i}` ? null : `${e.key}-${i}`)}>改一改</Btn>
                )}
                {draft && j.kind === "new" && j.draft && <Btn kind="ghost" onClick={() => setSteps(j)}>细调 / 试跑</Btn>}
                {draft && j.schedule && (
                  <Btn kind="ghost" disabled={!!busy} onClick={() => void patch(e, { jobs: [{ index: i, schedule: "" }] })}>不定时</Btn>
                )}
                {j.kind === "teach" && j.include && !j.attached && (
                  <Btn kind={draft ? "ghost" : "primary"} disabled={!!busy} onClick={() => void teach(e, i)}>现在教</Btn>
                )}
                {j.kind === "teach" && j.attached && <span style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>✓ 已接上</span>}
              </span>
            </div>
          ))}
        </div>
      ))}
      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <span style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{notice}</span>}
      {failedSchedules.length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "var(--status-warn, #d97706)" }}>
            定时没挂上: {failedSchedules.map((s) => s.title).join("、")} (专家和工作流已经好了)
          </span>
          <Btn kind="ghost" disabled={!!busy} onClick={() => void run("schedule", async () => { await schedule(failedSchedules); })}>重试定时</Btn>
        </div>
      )}
      {draft && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "flex-end", flexWrap: "wrap" }}>
          {blocker && <span style={{ fontSize: 12, color: "var(--status-warn, #d97706)" }}>{blocker}</span>}
          <Btn kind="primary" disabled={!!busy || !!blocker} onClick={() => void applyAll()}>
            {busy === "apply" ? "保存中…" : "全部保存并生效"}
          </Btn>
        </div>
      )}
      {steps?.draft && (
        <Detail target={{ kind: "draft", name: steps.draft, title: steps.title }} inBuild
                onClose={() => { setSteps(null); void expertBuildGet(b.build_id).then((r) => onChange(r.build)).catch(() => {}); }}
                onChanged={() => {}} />
      )}
    </div>
  );
}

/** 「改一改」: 一句话说要怎么改这件活 */
function ReviseBox({ busy, onCancel, onSubmit }: { busy: boolean; onCancel: () => void; onSubmit: (text: string) => void }) {
  const [text, setText] = useState("");
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 2 }}>
      <input value={text} onChange={(ev) => setText(ev.target.value)} disabled={busy} autoFocus aria-label="一句话说要怎么改"
             onKeyDown={(ev) => { if (ev.key === "Enter" && text.trim().length >= 2 && !ev.nativeEvent.isComposing) onSubmit(text.trim()); }}
             placeholder="例: 不要 Word 了, 只存知识库"
             style={{ ...inputStyle, flex: "1 1 220px", minWidth: 0 }} />
      <Btn kind="primary" disabled={busy || text.trim().length < 2} onClick={() => onSubmit(text.trim())}>改</Btn>
      <Btn kind="ghost" disabled={busy} onClick={onCancel}>算了</Btn>
    </div>
  );
}

function PersonaEditor({ expert, busy, onSave }: {
  expert: BuildExpert; busy: boolean; onSave: (p: { name: string; description: string; persona: string }) => void;
}) {
  const [name, setName] = useState(expert.name);
  const [description, setDescription] = useState(expert.description);
  const [persona, setPersona] = useState(expert.persona);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingLeft: 8 }}>
      <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} aria-label="专家名字" />
      <input value={description} onChange={(e) => setDescription(e.target.value)} style={inputStyle} aria-label="一句话简介" />
      <textarea rows={3} value={persona} onChange={(e) => setPersona(e.target.value)} style={{ ...inputStyle, resize: "vertical" }} aria-label="人设" />
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Btn kind="primary" disabled={busy || !name.trim()} onClick={() => onSave({ name, description, persona })}>存人设</Btn>
      </div>
    </div>
  );
}
