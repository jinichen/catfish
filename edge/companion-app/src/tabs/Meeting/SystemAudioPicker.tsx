/**
 * 新会议里的「线上会议」一栏 (11/11)。
 *
 * 腾讯会议 / AONE 这类会议, 员工戴耳机时对方的声音只进耳机, 麦克风录不到。勾上这一栏,
 * 录音时再录一路会议软件放出来的声音, 跟麦克风分两条存; 转写时两条分开认人, 外放时的回声去掉。
 *
 *   · Mac (macOS 14.2+ / 13–14.1): 选录哪个程序的声音, 默认选正在出声的会议软件
 *   · Windows: 录全部电脑声音 (不分程序), 只给一句说明
 *   · 录不了 (系统太老): 说明原因, 勾选框禁用
 */
import { useCallback, useEffect, useState } from "react";

import { meetingSystemAudioSources, type SysPick, type SysSources } from "../../lib/tauri_meeting";
import { inputStyle } from "../Collab/roomLinkUi";
import { defaultSysKey, sysAppKey, sysPickFor } from "./meetingHelpers";

const REMEMBER_KEY = "catfish.meeting.online";

function remembered(): boolean {
  try {
    return localStorage.getItem(REMEMBER_KEY) === "1";
  } catch {
    return false;
  }
}

export interface SystemAudioChoice {
  pick: SysPick;
  localSpeakers: string;
}

const muted = { fontSize: 12, color: "var(--catfish-text-muted)" } as const;

export default function SystemAudioPicker({
  onChange,
}: {
  /** null = 只录麦克风 */
  onChange: (choice: SystemAudioChoice | null) => void;
}) {
  const [on, setOn] = useState(remembered);
  const [src, setSrc] = useState<SysSources | null>(null);
  const [key, setKey] = useState("");
  const [local, setLocal] = useState("1");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (keepKey: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const s = await meetingSystemAudioSources();
      setSrc(s);
      setKey((cur) => (keepKey && (cur === "" || s.apps.some((a) => sysAppKey(a) === cur)) ? cur : defaultSysKey(s)));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (on && !src) void refresh(false);
  }, [on, src, refresh]);

  const usable = !!src && src.mode !== "none";
  useEffect(() => {
    onChange(on && usable && src ? { pick: sysPickFor(src, key), localSpeakers: local } : null);
  }, [on, usable, src, key, local, onChange]);

  const toggle = (v: boolean) => {
    setOn(v);
    try {
      localStorage.setItem(REMEMBER_KEY, v ? "1" : "0");
    } catch {
      /* 记不住就算了 */
    }
  };

  const pickable = src?.mode === "tap" || src?.mode === "sck";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, cursor: "pointer" }}>
        <input type="checkbox" checked={on} onChange={(e) => toggle(e.target.checked)} style={{ marginTop: 2 }} />
        <span>
          线上会议 (腾讯会议 / AONE 等): 同时录对方的声音
          <span style={{ ...muted, display: "block" }}>戴耳机开会时麦克风听不到对方, 勾上才录得到</span>
        </span>
      </label>
      {on && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingLeft: 22 }}>
          {loading && !src && <div style={muted}>正在看哪些程序在用声音…</div>}
          {error && <div style={{ ...muted, color: "var(--status-error, #dc2626)" }}>{error}</div>}
          {src && !usable && <div style={{ ...muted, color: "var(--status-error, #dc2626)" }}>{src.hint}</div>}
          {pickable && src && (
            <>
              <div style={muted}>录哪个程序的声音</div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <select style={{ ...inputStyle, flex: 1, minWidth: 0 }} value={key} onChange={(e) => setKey(e.target.value)}>
                  {src.apps.map((a) => (
                    <option key={sysAppKey(a)} value={sysAppKey(a)}>
                      {a.name}{a.playing ? " (正在出声)" : ""}
                    </option>
                  ))}
                  <option value="">全部电脑声音</option>
                </select>
                <button
                  type="button"
                  onClick={() => void refresh(true)}
                  disabled={loading}
                  style={{ background: "none", border: "none", padding: 0, font: "inherit", fontSize: 12, color: "var(--catfish-text-muted)", textDecoration: "underline", cursor: "pointer", whiteSpace: "nowrap" }}
                >
                  {loading ? "刷新中…" : "刷新"}
                </button>
              </div>
              <div style={muted}>会议软件要先进会议室才会出现在列表里; 列表里没有就选「全部电脑声音」。{src.hint}</div>
            </>
          )}
          {src?.mode === "loopback" && <div style={muted}>{src.hint}</div>}
          {usable && (
            <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "var(--catfish-text-muted)" }}>
              这台电脑前说话的人数 (含自己)
              <input
                type="number"
                min={1}
                max={49}
                value={local}
                onChange={(e) => setLocal(e.target.value)}
                style={{ ...inputStyle, width: 64 }}
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
