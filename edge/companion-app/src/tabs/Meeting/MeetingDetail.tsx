/**
 * 一场会议的详情 (10/1): 按状态走 —— 待转写 → 转写中 → 已转写 (改说话人名 / 看转写 / 纪要)。
 */
import { useEffect, useRef, useState } from "react";

import {
  formatClock,
  meetingDelete,
  meetingMinutes,
  meetingSetSpeakers,
  meetingSpeakers,
  meetingHqStatus,
  meetingTranscribe,
  meetingTranscribeHq,
  meetingTranscribePhase,
  meetingTranscript,
  onTranscribeProgress,
  type AsrStatus,
  type HqStatus,
  type MeetingMeta,
  type Transcript,
} from "../../lib/tauri_meeting";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import AsrSetup from "./AsrSetup";
import LiveAssist, { LiveNotes } from "./LiveAssist";
import MinutesView from "./MinutesView";
import TranscriptView from "./TranscriptView";
import { sampleSegment, toggleClip } from "./meetingPlayer";
import { estimatePercent, STATUS_LABEL } from "./meetingHelpers";
import { COL_CARD } from "./meetingHelpers";

const PHASE_LABEL: Record<string, string> = {
  starting: "启动中",
  loading: "加载模型",
  preparing: "拼接录音",
  recognizing: "识别中",
  refining: "精细识别 (Qwen3)",
  writing: "写结果",
};
const SPK_COLORS = ["#0E6169", "#F0703A", "#6B5BD2", "#2F8F4E", "#B5476B", "#8A6D1F"];

export default function MeetingDetail({
  meta,
  asr,
  onChanged,
  onDeleted,
}: {
  meta: MeetingMeta;
  asr: AsrStatus;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [phase, setPhase] = useState<{ phase: string; audio?: number | null; done?: number | null; total?: number | null } | null>(null);
  // 11/17 高精度转写组件 (Qwen3-ASR, 可选): 装了就能「高精度重新转写」, 没装点了先出下载面板
  const [hq, setHq] = useState<HqStatus | null>(null);
  const [hqSetup, setHqSetup] = useState(false);
  const loadHq = () => void meetingHqStatus().then(setHq).catch(() => setHq(null));
  useEffect(loadHq, []);
  const [elapsed, setElapsed] = useState(0);
  const [notesTick, setNotesTick] = useState(0);   // 会后改了会中小鲶的条目 → 纪要重读
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const dirty = useRef(false);
  const [speakersOpen, setSpeakersOpen] = useState(true);
  const [tab, setTab] = useState<"minutes" | "transcript">("minutes");
  // 删除点两下: Tauri WebView 里 window.confirm() 静默返回 null (CronJobsCard 7/6 记过), 跟模板删除同一个做法
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const started = useRef<number>(0);

  useEffect(() => {
    setTranscript(null);
    setError(null);
    setSaved(false);
    setConfirmDelete(false);
    setTab("minutes");
    dirty.current = false;
    if (meta.status === "transcribed") {
      meetingTranscript(meta.id).then(setTranscript).catch((e) => setError(String(e)));
      // 展开 / 收起在打开这场会议时定一次: 没起过名字、也还没生成纪要才展开 (生成前改名, 负责人更准);
      // 不能跟着输入实时算, 不然填下第一个名字那栏就收起来了
      void Promise.all([
        meetingSpeakers(meta.id).catch((): Record<string, string> => ({})),
        meetingMinutes(meta.id).catch(() => null),
      ]).then(([n, m]) => {
        setNames(n);
        setSpeakersOpen(!m && !Object.values(n).some((v) => v.trim()));
      });
    }
  }, [meta.id, meta.status]);

  // onChanged 每次渲染都是新函数 (MeetingTab 里的箭头函数): 放 ref 里, 订阅不跟着它反复退订 / 重订 —— 重订的空当会漏进度事件
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  useEffect(() => {
    const un = onTranscribeProgress((p) => {
      if (p.id !== meta.id) return;
      if (p.done) {
        setPhase(null);
        if (p.error) setError(p.error);
        onChangedRef.current();
      } else {
        if (!started.current) started.current = Date.now();
        setPhase((cur) => ({ phase: p.phase, audio: p.duration_secs ?? cur?.audio }));
      }
    });
    return () => void un.then((f) => f());
  }, [meta.id]);

  // 11/17 (鸿波「一直挂在准备中, 是卡住了吗」): 事件会漏 (切走再回来、重订的空当), 转写中每 3 秒也问一次后端到哪一步了;
  // 已用时间也按后端的开始时间算, 切回来不从 0 数
  useEffect(() => {
    if (meta.status !== "transcribing") return;
    let stop = false;
    const poll = () => void meetingTranscribePhase(meta.id).then((p) => {
      if (stop || !p) return;
      started.current = Date.now() - p.elapsed_secs * 1000;
      setPhase((cur) => ({ phase: p.phase, audio: p.duration_secs ?? cur?.audio, done: p.done, total: p.total }));
    }).catch(() => undefined);
    poll();
    const t = window.setInterval(poll, 3000);
    const tick = window.setInterval(() => started.current && setElapsed((Date.now() - started.current) / 1000), 1000);
    return () => { stop = true; window.clearInterval(t); window.clearInterval(tick); };
  }, [meta.id, meta.status]);

  async function transcribe() {
    setError(null);
    started.current = Date.now();
    try {
      await meetingTranscribe(meta.id);
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  }

  async function transcribeHq() {
    setError(null);
    started.current = Date.now();
    try {
      await meetingTranscribeHq(meta.id);
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  }

  async function remove() {
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    setConfirmDelete(false);
    setDeleting(true);
    setError(null);
    try {
      await meetingDelete(meta.id);
      onDeleted();
    } catch (e) {
      setError(String(e));
    } finally {
      setDeleting(false);
    }
  }

  async function saveNames() {
    if (!dirty.current) return;
    dirty.current = false;
    try {
      await meetingSetSpeakers(meta.id, names);
      setSaved(true);
    } catch (e) {
      setError(String(e));
    }
  }

  const busyNow = meta.status === "recording" || meta.status === "transcribing";
  const head = (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>{meta.title}</h3>
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginTop: 4 }}>
          {meta.created_at.slice(0, 16).replace("T", " ")} · {formatClock(meta.duration_secs)} · {meta.attendees} 人 · {STATUS_LABEL[meta.status]}
        </div>
        {confirmDelete && (
          <div style={{ fontSize: 12, color: "var(--catfish-danger, #d9534f)", marginTop: 4 }}>
            录音、转写、纪要都会删掉, 找不回来; 已「存进知识库」的那份不受影响。确定的话再点一次。
          </div>
        )}
      </div>
      <span title={busyNow ? "录音 / 转写中不能删" : "删除这场会议"}>
        <Btn kind="ghost" disabled={deleting || busyNow} onClick={() => void remove()}>
          {deleting ? "删除中…" : confirmDelete ? "再点一次确认删除" : "删除"}
        </Btn>
      </span>
    </div>
  );

  let body = null;
  if (meta.status === "recording") {
    // 11/15 会中小鲶: 实时文字 + 问小鲶 + 主动提示
    body = <LiveAssist meta={meta} />;
  } else if (meta.status === "recorded" || meta.status === "failed") {
    body = asr.installed ? (
      <>
        {meta.status === "failed" && meta.error && <ErrorLine>上次转写失败: {meta.error}</ErrorLine>}
        <Btn kind="primary" disabled={asr.transcribing} onClick={() => void transcribe()}>
          {asr.transcribing ? "有别的会议在转写, 稍等" : meta.status === "failed" ? "重新转写" : "开始转写"}
        </Btn>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          在本机转写, 大约是会议时长的 1/8 (一小时会议约 8 分钟), 期间可以照常用电脑。
        </div>
      </>
    ) : (
      <AsrSetup status={asr} onReady={onChanged} />
    );
  } else if (meta.status === "transcribing") {
    const pct = estimatePercent(elapsed, phase?.audio ?? meta.duration_secs);
    body = (
      <div style={{ fontSize: 13 }}>
        转写中 · {PHASE_LABEL[phase?.phase ?? ""] ?? "准备中"}
        {pct !== null && phase?.phase === "recognizing" ? ` · 约 ${pct}%` : ""}
        {phase?.phase === "refining" && phase.total ? ` · 约 ${Math.floor(((phase.done ?? 0) / phase.total) * 100)}%` : ""}
        {elapsed > 0 && (
          <span style={{ color: "var(--catfish-text-muted)" }}>
            {" "}· 已用 {Math.floor(elapsed / 60)} 分 {String(Math.floor(elapsed % 60)).padStart(2, "0")} 秒
          </span>
        )}
      </div>
    );
  } else if (meta.status === "transcribed" && transcript) {
    const spks = [...new Set(transcript.segments.map((s) => s.spk))].sort((a, b) => a - b);
    const named = spks.filter((s) => names[String(s)]?.trim());
    const edits = transcript.segments.filter((s) => s.orig_text).length;
    const redoMins = Math.max(1, Math.round(meta.duration_secs / 8 / 60));
    // 11/17 (鸿波「这个 UI 是不是需要优化」): 十个说话人框原来常年摊在纪要上面占四行 —— 改成可收起的一栏,
    // 没起过名字时展开, 起过就收成一行名单; 名字改完离开输入框自动存, 不用再点「保存名字」。
    const open = speakersOpen;
    const speakers = (
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
          <button type="button" onClick={() => setSpeakersOpen(!open)} aria-expanded={open}
                  style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, fontSize: 13, fontWeight: 600,
                           color: "var(--catfish-text)", fontFamily: "inherit" }}>
            {open ? "▾" : "▸"} 说话人 {spks.length} 位
          </button>
          <span style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--catfish-text-muted)", overflow: "hidden",
                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {open
              ? "填上真名, 纪要里的负责人更准; 点 ▶ 听他说的一句认人。离开输入框自动保存"
              : named.length
                ? spks.map((s) => names[String(s)]?.trim() || `说话人${s + 1}`).join("、")
                : "还没填真名 · 点开填, 或者让小鲶在纪要里换"}
          </span>
          {saved && <span style={{ fontSize: 12, color: "var(--status-ok)" }}>已保存 ✓</span>}
          {/* 收起时也要看得到 (11/17 鸿波「没看到重新转写的按钮」—— 原来只在展开时出) */}
          {/* 11/17 鸿波「点了重新转写就直接进转写, 为什么还那么多确认」: 一点就转 —— 原转写和名字都自动备份
              (transcript.before-*.json / speakers.before-*.json), 不会丢 */}
          <span title={`按新的说话人分法重新转一遍, 约 ${redoMins} 分钟; 原转写${edits ? ` (含改过的 ${edits} 句)` : ""}和起过的名字自动备份`}>
            <Btn kind="ghost" disabled={asr.transcribing} onClick={() => void transcribe()}>重新转写</Btn>
          </span>
          {/* 11/17 鸿波选 A: 高精度 (Qwen3-ASR) 做成可选组件, 要紧的会才用 —— 句子 / 说话人照用, 名字不动 */}
          <span title={hq?.installed
            ? "用 Qwen3-ASR 重新识别文字 (说话人、名字不变), 说得清楚的地方更准; 比普通转写慢, 两小时的会约半小时以上"
            : "要先下载高精度转写组件 (Qwen3-ASR)"}>
            <Btn kind="ghost" disabled={asr.transcribing}
                 onClick={() => (hq?.installed ? void transcribeHq() : setHqSetup((v) => !v))}>
              高精度重新转写
            </Btn>
          </span>
        </div>
        {hqSetup && !hq?.installed && (
          hq?.base_installed === false
            ? <ErrorLine>先装会议组件包, 高精度组件要用到它</ErrorLine>
            : <AsrSetup kind="hq" status={{ pack_ready: hq?.pack_ready ?? null, installing: !!hq?.installing,
                                            transcribing: asr.transcribing }}
                        onReady={() => { setHqSetup(false); loadHq(); }} />
        )}
        {open && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 8 }}>
            {/* 11/12: 每个说话人旁边 ▶ 听他说得最长的一句, 听出是谁再填名字 */}
            {spks.map((s) => {
              const sample = sampleSegment(transcript.segments, s);
              const color = SPK_COLORS[s % SPK_COLORS.length];
              return (
                <span key={s} style={{ display: "flex", alignItems: "center", gap: 2, minWidth: 0 }}>
                  <input
                    aria-label={`说话人${s + 1} 的名字`}
                    style={{ ...inputStyle, flex: 1, minWidth: 0, width: "auto", boxShadow: `inset 3px 0 0 ${color}` }}
                    placeholder={`说话人${s + 1}`}
                    value={names[String(s)] ?? ""}
                    onChange={(e) => {
                      setSaved(false);
                      dirty.current = true;
                      setNames({ ...names, [String(s)]: e.target.value });
                    }}
                    onBlur={() => void saveNames()}
                    onKeyDown={(e) => { if (e.key === "Enter") void saveNames(); }}
                  />
                  {sample && (
                    <button type="button" aria-label={`听说话人${s + 1}`} title="听他说的一句, 认出是谁"
                            onClick={() => void toggleClip(`spk-${meta.id}-${s}`, meta.id, sample).catch((e) => setError(`放不了: ${String(e)}`))}
                            style={{ border: "none", background: "transparent", cursor: "pointer", padding: "2px 4px", fontSize: 11,
                                     color: "var(--catfish-text-muted)" }}>▶</button>
                  )}
                </span>
              );
            })}
          </div>
        )}
      </div>
    );
    const tabBtn = (t: "minutes" | "transcript", label: string) => (
      <button type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              style={{ border: "none", background: tab === t ? "var(--catfish-bg-elevated)" : "transparent", cursor: "pointer",
                       padding: "5px 14px", borderRadius: 6, fontSize: 13, fontFamily: "inherit",
                       fontWeight: tab === t ? 600 : 400,
                       color: tab === t ? "var(--catfish-text)" : "var(--catfish-text-muted)",
                       boxShadow: tab === t ? "0 1px 2px rgba(0,0,0,0.12)" : "none" }}>
        {label}
      </button>
    );
    body = (
      <>
        {speakers}
        <div role="tablist" aria-label="纪要 / 转写"
             style={{ display: "inline-flex", alignSelf: "flex-start", gap: 2, padding: 3, borderRadius: 8,
                      background: "var(--catfish-bg-cream)" }}>
          {tabBtn("minutes", "纪要")}
          {tabBtn("transcript", `转写 · ${transcript.segments.length} 句`)}
        </div>
        {/* 两个都挂着, 只切显示: 转写里改的错字、小鲶改到一半的纪要, 切过去再切回来都还在 */}
        <div style={{ display: tab === "minutes" ? "contents" : "none" }}>
          <MinutesView key={notesTick} meta={meta} names={names} />
        </div>
        <div style={{ display: tab === "transcript" ? "contents" : "none" }}>
          <TranscriptView key={meta.id} meetingId={meta.id} transcript={transcript} names={names} colors={SPK_COLORS} />
        </div>
      </>
    );
  }

  return (
    <div style={{ ...cardStyle, ...COL_CARD }}>
      {head}
      {body}
      {meta.status !== "recording" && <LiveNotes key={meta.id} meetingId={meta.id} onChanged={() => setNotesTick((n) => n + 1)} />}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
