/**
 * 一场会议的详情 (10/1): 按状态走 —— 待转写 → 转写中 → 已转写 (改说话人名 / 看转写 / 纪要)。
 */
import { useEffect, useRef, useState } from "react";

import {
  formatClock,
  meetingSetSpeakers,
  meetingSpeakers,
  meetingTranscribe,
  meetingTranscript,
  onTranscribeProgress,
  speakerName,
  type AsrStatus,
  type MeetingMeta,
  type Transcript,
} from "../../lib/tauri_meeting";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import AsrSetup from "./AsrSetup";
import MinutesView from "./MinutesView";
import { estimatePercent, STATUS_LABEL } from "./meetingHelpers";

const PHASE_LABEL: Record<string, string> = {
  loading: "加载模型",
  preparing: "拼接录音",
  recognizing: "识别中",
  writing: "写结果",
};
const SPK_COLORS = ["#0E6169", "#F0703A", "#6B5BD2", "#2F8F4E", "#B5476B", "#8A6D1F"];

export default function MeetingDetail({ meta, asr, onChanged }: { meta: MeetingMeta; asr: AsrStatus; onChanged: () => void }) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [names, setNames] = useState<Record<string, string>>({});
  const [phase, setPhase] = useState<{ phase: string; audio?: number | null } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const started = useRef<number>(0);

  useEffect(() => {
    setTranscript(null);
    setError(null);
    setSaved(false);
    if (meta.status === "transcribed") {
      meetingTranscript(meta.id).then(setTranscript).catch((e) => setError(String(e)));
      meetingSpeakers(meta.id).then(setNames).catch(() => setNames({}));
    }
  }, [meta.id, meta.status]);

  useEffect(() => {
    const un = onTranscribeProgress((p) => {
      if (p.id !== meta.id) return;
      if (p.done) {
        setPhase(null);
        if (p.error) setError(p.error);
        onChanged();
      } else {
        if (!started.current) started.current = Date.now();
        setPhase((cur) => ({ phase: p.phase, audio: p.duration_secs ?? cur?.audio }));
      }
    });
    return () => void un.then((f) => f());
  }, [meta.id, onChanged]);

  useEffect(() => {
    if (meta.status !== "transcribing") return;
    const t = window.setInterval(() => started.current && setElapsed((Date.now() - started.current) / 1000), 1000);
    return () => window.clearInterval(t);
  }, [meta.status]);

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

  async function saveNames() {
    try {
      await meetingSetSpeakers(meta.id, names);
      setSaved(true);
    } catch (e) {
      setError(String(e));
    }
  }

  const head = (
    <div>
      <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>{meta.title}</h3>
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginTop: 4 }}>
        {meta.created_at.slice(0, 16).replace("T", " ")} · {formatClock(meta.duration_secs)} · {meta.attendees} 人 · {STATUS_LABEL[meta.status]}
      </div>
    </div>
  );

  let body = null;
  if (meta.status === "recording") {
    body = <div style={{ fontSize: 13 }}>录音中, 在左边结束录音后就能转写。</div>;
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
      </div>
    );
  } else if (meta.status === "transcribed" && transcript) {
    const spks = [...new Set(transcript.segments.map((s) => s.spk))].sort((a, b) => a - b);
    body = (
      <>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)", alignItems: "center" }}>
          {spks.map((s) => (
            <input
              key={s}
              style={{ ...inputStyle, width: 120, borderColor: SPK_COLORS[s % SPK_COLORS.length] }}
              placeholder={`说话人${s + 1}`}
              value={names[String(s)] ?? ""}
              onChange={(e) => {
                setSaved(false);
                setNames({ ...names, [String(s)]: e.target.value });
              }}
            />
          ))}
          <Btn kind="ghost" onClick={() => void saveNames()}>{saved ? "已保存 ✓" : "保存名字"}</Btn>
        </div>
        <MinutesView meta={meta} />
        <div style={{ maxHeight: 420, overflowY: "auto", fontSize: 13, lineHeight: 1.6 }}>
          {transcript.segments.map((seg, i) => (
            <div key={i}>
              <span style={{ color: "var(--catfish-text-muted)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{formatClock(seg.start)} </span>
              <span style={{ color: SPK_COLORS[seg.spk % SPK_COLORS.length], fontWeight: 600 }}>{speakerName(seg.spk, names)}: </span>
              {seg.text}
            </div>
          ))}
        </div>
      </>
    );
  }

  return (
    <div style={cardStyle}>
      {head}
      {body}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
