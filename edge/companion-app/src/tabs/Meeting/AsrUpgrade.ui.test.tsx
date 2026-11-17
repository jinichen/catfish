// @vitest-environment jsdom
/** 会议组件升级卡 (11/17): 装过、中央有更新的版本才出; 一样或更老不出。 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ latest: "2.0.0" }));
vi.mock("../../lib/tauri_components", () => ({
  componentsList: vi.fn(async () => ({ platform: "mac-arm64", components: [
    { name: "meeting-asr", version: api.latest, platform: "mac-arm64", file: "f", size: 6.5e9, sha256: "x",
      status: { state: "absent" }, busy: false },
  ] })),
  componentsDownload: vi.fn(), componentsCancel: vi.fn(), componentsVerify: vi.fn(),
  onComponentProgress: vi.fn(async () => () => undefined),
}));
vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  onAsrInstallProgress: vi.fn(async () => () => undefined),
  onHqInstallProgress: vi.fn(async () => () => undefined),
}));

import { AsrUpgrade, newerVersion } from "./AsrSetup";

const status = (v: string) => ({ installed: { version: v, python: "p", models: "m" }, pack_ready: null, installing: false, transcribing: false });
afterEach(cleanup);

describe("会议组件升级卡", () => {
  it("版本比较", () => {
    expect(newerVersion("2.0.0", "1.1.0")).toBe(true);
    expect(newerVersion("1.10.0", "1.9.0")).toBe(true);
    expect(newerVersion("1.1.0", "1.1.0")).toBe(false);
    expect(newerVersion("1.0.0", "1.1.0")).toBe(false);
  });

  it("中央有新版: 说清楚是哪个版本、多大", async () => {
    api.latest = "2.0.0";
    await act(async () => { render(<AsrUpgrade status={status("1.0.0") as never} onReady={vi.fn()} />); });
    expect(screen.getByText(/会议组件有新版 2\.0\.0 \(你现在是 1\.0\.0\)/)).toBeTruthy();
    expect(screen.getByText("下载新版")).toBeTruthy();
  });

  it("没有新版: 什么都不显示", async () => {
    api.latest = "1.0.0";
    const { container } = render(<AsrUpgrade status={status("1.0.0") as never} onReady={vi.fn()} />);
    await act(async () => undefined);
    expect(container.innerHTML).toBe("");
  });
});
