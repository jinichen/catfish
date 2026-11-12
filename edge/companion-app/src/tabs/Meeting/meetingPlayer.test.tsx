// @vitest-environment jsdom
/** 11/12 听一句: 截哪一段、一次只放一句、再点 = 停; 说话人样本挑最长但不超过 15 秒的一句。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ clip: vi.fn(async () => btoa("RIFFfake")) }));
vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingAudioClip: api.clip,
}));

import { playingKey, sampleSegment, stopClip, toggleClip } from "./meetingPlayer";
import TranscriptView from "./TranscriptView";

beforeEach(() => {
  api.clip.mockClear();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:x") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: vi.fn(async () => undefined) });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: vi.fn() });
});
afterEach(() => { stopClip(); cleanup(); });

const segs = [
  { spk: 0, start: 0, end: 2, text: "开始" },
  { spk: 1, start: 2, end: 30, text: "很长的一段" },
  { spk: 1, start: 31, end: 40, text: "九秒" },
  { spk: 0, start: 41, end: 46, text: "五秒", track: "system" as const },
];

describe("meetingPlayer", () => {
  it("sampleSegment: 最长但不超过 15 秒", () => {
    expect(sampleSegment(segs, 1)?.text).toBe("九秒");
    expect(sampleSegment(segs, 0)?.text).toBe("五秒");
    expect(sampleSegment(segs, 9)).toBeNull();
  });

  it("toggleClip: 截那一句 (带 track), 再点同一句停, 点别的换", async () => {
    await toggleClip("a", "mtg_1", segs[3]);
    expect(api.clip).toHaveBeenCalledWith("mtg_1", 41, 46, "system");
    expect(playingKey()).toBe("a");
    await toggleClip("a", "mtg_1", segs[3]);
    expect(playingKey()).toBeNull();
    await toggleClip("a", "mtg_1", segs[0]);
    await toggleClip("b", "mtg_1", segs[2]);
    expect(playingKey()).toBe("b");
  });
});

describe("TranscriptView 听一句", () => {
  it("点 ▶ 放那一句并标出来; 改的时候能「听这句」", async () => {
    render(<TranscriptView meetingId="mtg_1" transcript={{ duration_secs: 46, speakers: 2, speakers_requested: 2, segments: segs }}
                           names={{}} colors={["#000", "#111"]} />);
    await act(async () => { fireEvent.click(screen.getByLabelText("听第 2 句")); });
    expect(api.clip).toHaveBeenCalledWith("mtg_1", 2, 30, undefined);
    expect(screen.getByLabelText("停止第 2 句")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("改第 3 句"));
    await act(async () => { fireEvent.click(screen.getByText("▶ 听这句")); });
    expect(api.clip).toHaveBeenLastCalledWith("mtg_1", 31, 40, undefined);
  });

  it("录音不在了: 说清楚", async () => {
    api.clip.mockRejectedValueOnce(new Error("这场会议的录音不在本机了 (可能被删了), 听不了"));
    render(<TranscriptView meetingId="mtg_1" transcript={{ duration_secs: 46, speakers: 2, speakers_requested: 2, segments: segs }}
                           names={{}} colors={["#000"]} />);
    await act(async () => { fireEvent.click(screen.getByLabelText("听第 1 句")); });
    expect(screen.getByText(/放不了: 这场会议的录音不在本机了/)).toBeTruthy();
  });
});
