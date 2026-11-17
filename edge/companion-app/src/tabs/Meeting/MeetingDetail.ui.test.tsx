// @vitest-environment jsdom
/** 删除会议 (10/15): 点两下才删, 录音 / 转写中按钮不可用。后端调用全换成假的。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  del: vi.fn(async () => undefined),
  segments: [] as unknown[],
  names: {} as Record<string, string>,
  setNames: vi.fn(async () => undefined),
  minutes: null as unknown,
  transcribe: vi.fn(async () => undefined),
  phase: vi.fn(async () => null as unknown),
}));

vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingDelete: api.del,
  meetingTranscript: vi.fn(async () => ({ segments: api.segments, text: "" })),
  meetingSpeakers: vi.fn(async () => api.names),
  meetingSetSpeakers: api.setNames,
  meetingTranscribe: api.transcribe,
  meetingTranscribePhase: api.phase,
  meetingMinutes: vi.fn(async () => api.minutes),
  meetingTemplatesList: vi.fn(async () => []),
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

beforeEach(() => {
  api.del.mockClear();
  api.setNames.mockClear();
  api.segments = [];
  api.names = {};
  api.minutes = null;
  api.transcribe.mockClear();
});
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

describe("说话人 / 纪要 · 转写 (11/17 UI 调整)", () => {
  const segs = [
    { spk: 0, start: 0, end: 2, text: "我们开始吧" },
    { spk: 1, start: 2, end: 5, text: "好的, 先说整改" },
    { spk: 2, start: 5, end: 9, text: "安全集成那块材料还缺" },
  ];

  it("没起过名字: 说话人那栏展开; 改完离开输入框自动存, 没改不存", async () => {
    api.segments = segs;
    await mount("transcribed");
    expect(screen.getByText("▾ 说话人 3 位")).toBeTruthy();
    const box = screen.getByLabelText("说话人2 的名字");
    fireEvent.focusOut(box);
    expect(api.setNames).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "王总" } });
    await act(async () => { fireEvent.focusOut(box); });
    expect(api.setNames).toHaveBeenCalledWith("mtg_1", { "1": "王总" });
    expect(screen.getByText("已保存 ✓")).toBeTruthy();
    expect(screen.queryByText("保存名字")).toBeNull();
  });

  it("起过名字: 收成一行名单, 点开能改", async () => {
    api.segments = segs;
    api.names = { "0": "陈鸿波", "1": "王总" };
    await mount("transcribed");
    expect(screen.getByText("▸ 说话人 3 位")).toBeTruthy();
    expect(screen.getByText("陈鸿波、王总、说话人3")).toBeTruthy();
    expect(screen.queryByLabelText("说话人1 的名字")).toBeNull();
    fireEvent.click(screen.getByText("▸ 说话人 3 位"));
    expect((screen.getByLabelText("说话人1 的名字") as HTMLInputElement).value).toBe("陈鸿波");
  });

  it("已经生成过纪要: 没起名字也先收起 (纪要是正事), 提示能让小鲶换", async () => {
    api.segments = segs;
    api.minutes = { json: { meeting_id: "mtg_1", summary: "s", decisions: [], action_items: [], open_questions: [] }, markdown: "", path: "" };
    await mount("transcribed");
    expect(screen.getByText("▸ 说话人 3 位")).toBeTruthy();
    expect(screen.getByText("还没填真名 · 点开填, 或者让小鲶在纪要里换")).toBeTruthy();
  });

  it("11/17 重新转写: 说话人一栏收起时也有, 一点就转 (原转写 / 名字后台自动备份)", async () => {
    api.segments = segs;
    api.names = { "0": "陈鸿波" };
    await mount("transcribed");
    expect(screen.getByText("▸ 说话人 3 位")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("重新转写")); });
    expect(api.transcribe).toHaveBeenCalledWith("mtg_1");
  });

  it("纪要 / 转写分两页, 默认纪要; 切页不卸载 (改到一半的还在)", async () => {
    api.segments = segs;
    await mount("transcribed");
    const [minutesTab, transcriptTab] = screen.getAllByRole("tab");
    expect(minutesTab.getAttribute("aria-selected")).toBe("true");
    expect(transcriptTab.textContent).toBe("转写 · 3 句");
    const wrapOf = (text: string) => screen.getByText(text).closest("div[style*='display']") as HTMLElement;
    expect(screen.getByText(/安全集成那块材料还缺/)).toBeTruthy();   // 挂着, 只是不显示
    fireEvent.click(transcriptTab);
    expect(transcriptTab.getAttribute("aria-selected")).toBe("true");
    expect(wrapOf("生成纪要")).toBeTruthy();
  });
});

describe("转写中的进度 (11/17 一直显示「准备中」)", () => {
  it("没收到事件 (切走再回来) 也按后端的进度显示, 已用时间从后端的开始时间算", async () => {
    api.phase.mockResolvedValue({ phase: "recognizing", duration_secs: 7912, elapsed_secs: 150 });
    await mount("transcribing");
    expect(screen.getByText(/转写中 · 识别中 · 约 \d+%/)).toBeTruthy();
    expect(api.phase).toHaveBeenCalledWith("mtg_1");
  });
});
