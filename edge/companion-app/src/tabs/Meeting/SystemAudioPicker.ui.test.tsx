// @vitest-environment jsdom
/** 新会议的「线上会议」一栏 (11/11)。后端调用换成假的。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SysSources } from "../../lib/tauri_meeting";

const api = vi.hoisted(() => ({ sources: vi.fn() }));
vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingSystemAudioSources: api.sources,
}));

import SystemAudioPicker from "./SystemAudioPicker";

// 这套测试环境里 jsdom 的 localStorage 不全, 换一个最小的
const store = new Map<string, string>();
vi.stubGlobal("localStorage", {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  clear: () => store.clear(),
});

const MAC: SysSources = {
  mode: "tap",
  hint: "去开权限",
  apps: [
    { name: "微信", bundle_id: "com.tencent.xinWeChat", pids: [1], playing: false, meeting: true },
    { name: "腾讯会议", bundle_id: "com.tencent.meeting", pids: [2, 3], playing: true, meeting: true },
  ],
};

async function open(src: SysSources) {
  api.sources.mockResolvedValue(src);
  const onChange = vi.fn();
  render(<SystemAudioPicker onChange={onChange} />);
  await act(async () => { fireEvent.click(screen.getByRole("checkbox")); });
  return onChange;
}

describe("SystemAudioPicker", () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });
  afterEach(() => cleanup());

  it("不勾 = 只录麦克风, 也不去问系统", () => {
    const onChange = vi.fn();
    render(<SystemAudioPicker onChange={onChange} />);
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect(api.sources).not.toHaveBeenCalled();
  });

  it("Mac: 勾上后默认选正在出声的会议软件, 本地人数默认 1, 并记住下次默认勾上", async () => {
    const onChange = await open(MAC);
    expect(onChange).toHaveBeenLastCalledWith({
      pick: { name: "腾讯会议", bundle_id: "com.tencent.meeting", pids: [] },
      localSpeakers: "1",
    });
    expect(screen.getByRole("combobox")).toHaveProperty("value", "com.tencent.meeting");
    expect(localStorage.getItem("catfish.meeting.online")).toBe("1");
  });

  it("改成录全部电脑声音", async () => {
    const onChange = await open(MAC);
    await act(async () => { fireEvent.change(screen.getByRole("combobox"), { target: { value: "" } }); });
    expect(onChange).toHaveBeenLastCalledWith({ pick: { name: "", bundle_id: null, pids: [] }, localSpeakers: "1" });
  });

  it("Windows: 没有程序可选, 只说明录的是全部电脑声音", async () => {
    const onChange = await open({ mode: "loopback", apps: [], hint: "Windows 录的是全部声音" });
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText("Windows 录的是全部声音")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith({ pick: { name: "", bundle_id: null, pids: [] }, localSpeakers: "1" });
  });

  it("系统太老录不了: 说明原因, 开始录音时按只录麦克风", async () => {
    const onChange = await open({ mode: "none", apps: [], hint: "要 macOS 13 以上" });
    expect(screen.getByText("要 macOS 13 以上")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
