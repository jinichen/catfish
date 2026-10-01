/**
 * 新建会议 + 录音中 (10/1)。
 *
 * 录音设备默认用系统默认 (不再是写死的序号 0 —— 那会录到 iPhone 连续互通麦克风);
 * 录音时实时显示电平, 连续几秒都是 0 就提示"没收到声音", 不用等会后才发现录了一段空白。
 */
import { useEffect, useRef, useState } from "react";

import {
  formatClock,
  meetingAudioDevices,
  meetingCreate,
  meetingRecordStart,
  meetingRecordStatus,
  meetingRecordStop,
  type InputDevice,
  type RecorderStatus,
} from "../../lib/tauri_meeting";
import { wikiListFiles } from "../../lib/tauri_wiki";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { mergeHotwords, wikiHotwords } from "./meetingHelpers";

const SILENT_WARN_SECS = 5;

export default function RecorderCard({ onChanged }: { onChanged: (selectId?: string) => void }) {
  const [devices, setDevices] = useState<InputDevice[]>([]);
  const [device, setDevice] = useState("");
  const [title, setTitle] = useState("");
  const [attendees, setAttendees] = useState("");
  const [hotwords, setHotwords] = useState("");
  const [rec, setRec] = useState<RecorderStatus | null>(null);
  const [silentFor, setSilentFor] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    meetingAudioDevices().then(setDevices).catch((e) => setError(String(e)));
    meetingRecordStatus().then((s) => s.recording && setRec(s)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!rec?.recording) return;
    timer.current = window.setInterval(async () => {
      const s = await meetingRecordStatus().catch(() => null);
      if (!s) return;
      setRec(s);
      setSilentFor((n) => (s.level < 0.002 ? n + 0.5 : 0));
      if (!s.recording) onChanged(); // 到上限自动停了 / 设备出错
    }, 500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [rec?.recording, onChanged]);

  async function start() {
    const n = Number(attendees);
    if (!Number.isInteger(n) || n < 1) {
      setError("先填参会人数 (用来区分说话人; 不填的话长会议会把几个人拆成几十个)");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const meta = await meetingCreate(title, n, hotwords.split(/[\s,，、]+/).filter(Boolean));
      const s = await meetingRecordStart(meta.id, device || null);
      setRec(s);
      setSilentFor(0);
      onChanged(meta.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      const meta = await meetingRecordStop();
      setRec(null);
      setTitle("");
      setAttendees("");
      setHotwords("");
      onChanged(meta?.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function importHotwords() {
    try {
      const words = wikiHotwords(await wikiListFiles());
      setHotwords((cur) => mergeHotwords(cur, words));
      if (!words.length) setError("知识库里还没有人名 / 项目 / 单位条目");
    } catch (e) {
      setError(String(e));
    }
  }

  if (rec?.recording) {
    const pct = Math.min(100, Math.round(Math.sqrt(rec.level) * 100));
    return (
      <div style={cardStyle}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🔴 录音中 · {formatClock(rec.seconds)}</h3>
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>设备: {rec.device}</div>
        <div style={{ height: 8, background: "var(--catfish-bg-cream)", borderRadius: 4 }}>
          <div style={{ width: `${pct}%`, height: "100%", background: "var(--status-ok)", borderRadius: 4, transition: "width 0.2s" }} />
        </div>
        {silentFor >= SILENT_WARN_SECS && (
          <ErrorLine>
            {Math.floor(silentFor)} 秒没收到声音 —— 检查是不是选错了麦克风, 或者系统设置 → 隐私与安全性 → 麦克风里没给鲶鱼授权。
          </ErrorLine>
        )}
        {rec.error && <ErrorLine>{rec.error}</ErrorLine>}
        <Btn kind="primary" disabled={busy} onClick={() => void stop()}>结束录音</Btn>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>录音只存在本机; 最长 4 小时, 到了会自动停。</div>
      </div>
    );
  }

  return (
    <div style={cardStyle}>
      <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🎙 新会议</h3>
      <input style={inputStyle} placeholder="会议标题 (可空)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <input
        style={inputStyle}
        type="number"
        min={1}
        max={50}
        placeholder="参会人数 (必填)"
        value={attendees}
        onChange={(e) => setAttendees(e.target.value)}
      />
      <select style={inputStyle} value={device} onChange={(e) => setDevice(e.target.value)}>
        <option value="">系统默认麦克风{devices.find((d) => d.is_default) ? ` (${devices.find((d) => d.is_default)!.name})` : ""}</option>
        {devices.filter((d) => !d.is_default).map((d) => (
          <option key={d.name} value={d.name}>{d.name}</option>
        ))}
      </select>
      <textarea
        style={{ ...inputStyle, minHeight: 56, resize: "vertical" }}
        placeholder="热词: 人名、项目名、术语, 空格分隔 (能明显减少识别错字)"
        value={hotwords}
        onChange={(e) => setHotwords(e.target.value)}
      />
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        <Btn kind="ghost" onClick={() => void importHotwords()}>从知识库带入人名 / 项目</Btn>
        <Btn kind="primary" disabled={busy} onClick={() => void start()}>开始录音</Btn>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
