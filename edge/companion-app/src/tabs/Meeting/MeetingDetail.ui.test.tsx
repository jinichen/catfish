// @vitest-environment jsdom
/** 删除会议 (10/15): 点两下才删, 录音 / 转写中按钮不可用。后端调用全换成假的。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ del: vi.fn(async () => undefined) }));

vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingDelete: api.del,
  meetingTranscript: vi.fn(async () => ({ segments: [], text: "" })),
  meetingSpeakers: vi.fn(async () => ({})),
  onTranscribeProgress: vi.fn(async () => () => undefined),
  onAsrInstallProgress: vi.fn(async () => () => undefined),
}));

// 「已录完」且没装转写组件时会挂出组件安装卡 (AsrSetup), 它要监听下载进度
vi.mock("../../lib/tauri_components", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_components")>()),
  onComponentProgress: vi.fn(async () => () => undefined),
}));

import MeetingDetail from "./MeetingDetail";

const asr = { installed: null, pack_ready: null, installing: false, transcribing: false } as never;
const meta = (status: string) =>
  ({ id: "mtg_1", title: "周会", created_at: "2026-10-01T10:00:00", attendees: 2, status, duration_secs: 60 }) as never;

async function mount(status: string, onDeleted = vi.fn()) {
  await act(async () => {
    render(<MeetingDetail meta={meta(status)} asr={asr} onChanged={vi.fn()} onDeleted={onDeleted} />);
  });
  return onDeleted;
}

beforeEach(() => api.del.mockClear());
afterEach(cleanup);

describe("删除会议", () => {
  it("第一下只提示, 第二下才删, 删完通知上层", async () => {
    const onDeleted = await mount("recorded");
    await act(async () => { fireEvent.click(screen.getByText("删除")); });
    expect(api.del).not.toHaveBeenCalled();
    expect(screen.getByText(/找不回来/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("再点一次确认删除")); });
    expect(api.del).toHaveBeenCalledWith("mtg_1");
    expect(onDeleted).toHaveBeenCalled();
  });

  it("录音中 / 转写中不能删", async () => {
    for (const status of ["recording", "transcribing"]) {
      await mount(status);
      expect((screen.getByText("删除").closest("button") as HTMLButtonElement).disabled).toBe(true);
      cleanup();
    }
  });

  it("后端拒绝时显示原因, 不通知上层", async () => {
    api.del.mockRejectedValueOnce("这场会议正在转写, 等转写完再删");
    const onDeleted = await mount("transcribed");
    await act(async () => { fireEvent.click(screen.getByText("删除")); });
    await act(async () => { fireEvent.click(screen.getByText("再点一次确认删除")); });
    expect(screen.getByText(/正在转写/)).toBeTruthy();
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
