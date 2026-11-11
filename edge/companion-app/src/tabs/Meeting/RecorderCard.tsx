/**
 * 新建会议 + 录音中 (10/1)。
 *
 * 录音设备默认用系统默认 (不再是写死的序号 0 —— 那会录到 iPhone 连续互通麦克风);
 * 录音时实时显示电平, 连续几秒都是 0 就提示"没收到声音", 不用等会后才发现录了一段空白。
 *
 * 10/11: 「导入录音文件」—— 录音笔 / 手机录的会议, 用同一份标题 / 人数 / 热词建会议,
 * 解码进会议目录, 之后转写、区分说话人、纪要跟现场录音完全一样。
 *
 * 11/11: 「线上会议」—— 同时录会议软件放出来的对方声音 (SystemAudioPicker), 录音时两条各一个电平。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  formatClock,
  meetingAudioDevices,
  meetingCreate,
  meetingImportAudio,
  meetingImportPickFile,
  meetingRecordStart,
  meetingRecordStatus,
  meetingRecordStop,
  type InputDevice,
  type RecorderStatus,
} from "../../lib/tauri_meeting";
import { wikiListFiles } from "../../lib/tauri_wiki";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { clampLocalSpeakers, mergeHotwords, recorderNotices, systemTrackNotice, wikiHotwords } from "./meetingHelpers";
import SystemAudioPicker, { type SystemAudioChoice } from "./SystemAudioPicker";

const SILENT_WARN_SECS = 5;

export default function RecorderCard({ onChanged }: { onChanged: (selectId?: string) => void }) {
  const [devices, setDevices] = useState<InputDevice[]>([]);
  const [device, setDevice] = useState("");
  const [title, setTitle] = useState("");
  const [attendees, setAttendees] = useState("");
  const [hotwords, setHotwords] = useState("");
  const [rec, setRec] = useState<RecorderStatus | null>(null);
  const [silentFor, setSilentFor] = useState(0);
  const [sysSilentFor, setSysSilentFor] = useState(0);
  const [online, setOnline] = useState<SystemAudioChoice | null>(null);
  const onOnlineChange = useCallback((c: SystemAudioChoice | null) => setOnline(c), []);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);
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
      setSysSilentFor((n) => (s.system && s.system.level < 0.002 ? n + 0.5 : 0));
      if (!s.recording) onChanged(); // 到上限自动停了 / 设备出错
    }, 500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [rec?.recording, onChanged]);

  function attendeeCount(): number | null {
    const n = Number(attendees);
    if (!Number.isInteger(n) || n < 1) {
      setError("先填参会人数 (用来区分说话人; 不填的话长会议会把几个人拆成几十个)");
      return null;
    }
    return n;
  }

  function resetForm() {
    setTitle("");
    setAttendees("");
    setHotwords("");
  }

  async function importFile() {
    const n = attendeeCount();
    if (n === null) return;
    setError(null);
    const path = await meetingImportPickFile().catch((e) => {
      setError(String(e));
      return null;
    });
    if (!path) return;
    setBusy(true);
    setImporting(path.split(/[\\/]/).pop() ?? path);
    try {
      const meta = await meetingImportAudio(path, title, n, hotwords.split(/[\s,，、]+/).filter(Boolean));
      resetForm();
      onChanged(meta.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setImporting(null);
    }
  }

  async function start() {
    const n = attendeeCount();
    if (n === null) return;
    setBusy(true);
    setError(null);
    try {
      const meta = await meetingCreate(title, n, hotwords.split(/[\s,，、]+/).filter(Boolean));
      const s = online
        ? await meetingRecordStart(meta.id, device || null, online.pick, clampLocalSpeakers(online.localSpeakers, n))
        : await meetingRecordStart(meta.id, device || null);
      setRec(s);
      setSilentFor(0);
      setSysSilentFor(0);
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
      resetForm();
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
    const meter = (level: number) => (
      <div style={{ height: 8, background: "var(--catfish-bg-cream)", borderRadius: 4 }}>
        <div style={{ width: `${Math.min(100, Math.round(Math.sqrt(level) * 100))}%`, height: "100%", background: "var(--status-ok)", borderRadius: 4, transition: "width 0.2s" }} />
      </div>
    );
    const sys = rec.system;
    const sysNotice = sys ? systemTrackNotice(sys, sysSilentFor, "系统设置 → 隐私与安全性 → 屏幕与系统录音 里打开 Catfish Companion") : null;
    return (
      <div style={cardStyle}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🔴 录音中 · {formatClock(rec.seconds)}</h3>
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{sys ? "麦克风 (这边)" : "设备"}: {rec.device}</div>
        {meter(rec.level)}
        {sys && (
          <>
            <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>对方: {sys.source}</div>
            {meter(sys.level)}
          </>
        )}
        {silentFor >= SILENT_WARN_SECS && (
          <ErrorLine>
            {Math.floor(silentFor)} 秒没收到声音 —— 检查是不是选错了麦克风, 或者系统设置 → 隐私与安全性 → 麦克风里没给鲶鱼授权。
          </ErrorLine>
        )}
        {rec.error && <ErrorLine>{rec.error}</ErrorLine>}
        {sys?.error && <ErrorLine>{sys.error}</ErrorLine>}
        {sysNotice && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{sysNotice}</div>}
        {/* 11/06: 瞬间丢一小段 / 系统换了麦克风都不是错误, 录音照常继续 —— 淡色提示, 不用红字 */}
        {recorderNotices(rec).map((text) => (
          <div key={text} style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>{text}</div>
        ))}
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
      <SystemAudioPicker onChange={onOnlineChange} />
      <textarea
        style={{ ...inputStyle, minHeight: 56, resize: "vertical" }}
        placeholder="热词: 人名、项目名、术语, 空格分隔 (能明显减少识别错字)"
        value={hotwords}
        onChange={(e) => setHotwords(e.target.value)}
      />
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
        <Btn kind="ghost" onClick={() => void importHotwords()}>从知识库带入人名 / 项目</Btn>
        <Btn kind="ghost" disabled={busy} onClick={() => void importFile()}>
          {importing ? "导入中…" : "导入录音"}
        </Btn>
        <Btn kind="primary" disabled={busy} onClick={() => void start()}>开始录音</Btn>
      </div>
      {importing && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>正在导入 {importing}, 长录音要解码几十秒…</div>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
