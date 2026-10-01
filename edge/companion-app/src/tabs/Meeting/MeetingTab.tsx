/**
 * 「会议」tab (10/1, docs/MEETING-MINUTES-PLAN.md) —— 录会议 → 本机转写 → 纪要 → 写回任务库 / 知识库。
 *
 * 左栏: 新会议 / 录音中 + 会议列表; 右栏: 选中那场的详情。
 * 音频 / 转写 / 纪要都在 ~/.catfish/meetings/, 只有转写文字在生成纪要时经网关去大模型。
 */
import { useCallback, useEffect, useState } from "react";

import { formatClock, meetingAsrStatus, meetingList, type AsrStatus, type MeetingMeta } from "../../lib/tauri_meeting";
import { cardStyle, ErrorLine } from "../Collab/roomLinkUi";
import MeetingDetail from "./MeetingDetail";
import RecorderCard from "./RecorderCard";
import { STATUS_LABEL } from "./meetingHelpers";

const EMPTY_ASR: AsrStatus = { installed: null, pack_ready: null, installing: false, transcribing: false };

export default function MeetingTab() {
  const [meetings, setMeetings] = useState<MeetingMeta[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [asr, setAsr] = useState<AsrStatus>(EMPTY_ASR);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (selectId?: string) => {
    try {
      const [list, st] = await Promise.all([meetingList(), meetingAsrStatus()]);
      setMeetings(list);
      setAsr(st);
      setError(null);
      if (selectId) setSelected(selectId);
      else setSelected((cur) => cur ?? list[0]?.id ?? null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 转写中的会议要等后台完成: 列表每 5 秒刷一次状态
  useEffect(() => {
    if (!meetings.some((m) => m.status === "transcribing")) return;
    const t = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(t);
  }, [meetings, refresh]);

  const current = meetings.find((m) => m.id === selected) ?? null;

  return (
    <div style={{ maxWidth: 1600, margin: "0 auto", padding: "var(--space-4)" }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(280px, 380px) minmax(0, 1fr)", gap: "var(--space-4)", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <RecorderCard onChanged={(id) => void refresh(id)} />
          <div style={{ ...cardStyle, gap: "var(--space-1)" }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>会议记录</div>
            {meetings.length === 0 && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>还没有会议</div>}
            {meetings.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setSelected(m.id)}
                style={{
                  textAlign: "left",
                  padding: "8px 10px",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  background: m.id === selected ? "var(--catfish-bg-cream)" : "transparent",
                  cursor: "pointer",
                  color: "var(--catfish-text)",
                  fontFamily: "inherit",
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 500 }}>{m.title}</div>
                <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
                  {m.created_at.slice(5, 16).replace("T", " ")} · {formatClock(m.duration_secs)} · {STATUS_LABEL[m.status]}
                </div>
              </button>
            ))}
          </div>
          {error && <ErrorLine>{error}</ErrorLine>}
        </div>
        {current ? (
          <MeetingDetail meta={current} asr={asr} onChanged={() => void refresh()} />
        ) : (
          <div style={{ ...cardStyle, fontSize: 13, color: "var(--catfish-text-muted)" }}>
            开一场会: 在左边填参会人数, 点「开始录音」。会后在本机转写、区分说话人, 再由小鲶生成纪要和待办。
          </div>
        )}
      </div>
    </div>
  );
}
