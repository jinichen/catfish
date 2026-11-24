// @vitest-environment jsdom
/** 11/21 换机迁移卡: 先问这台是旧电脑还是新电脑, 只给那一半; 直传不等「清点本机数据」; 正在传 / 收的直接进那一半。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
vi.mock("qrcode", () => ({ default: { toCanvas: vi.fn(() => Promise.resolve()) } }));

import DeviceMigrationCard from "./DeviceMigrationCard";

let sendStatus: unknown = null;
beforeEach(() => {
  sendStatus = null;
  invoke.mockReset().mockImplementation((cmd: string) => {
    if (cmd === "migration_scan") return new Promise(() => {}); // 清点一直没完
    if (cmd === "migration_direct_send_status") return Promise.resolve(sendStatus);
    return Promise.resolve(null);
  });
});
afterEach(cleanup);

describe("DeviceMigrationCard", () => {
  it("先问这台是哪台; 选旧电脑只给搬出去那一半, 直传不等清点", async () => {
    render(<DeviceMigrationCard />);
    expect(screen.getByText("这台电脑是…")).toBeTruthy();
    expect(screen.queryByText("📷 扫新电脑上的二维码")).toBeNull();
    fireEvent.click(screen.getByText("旧电脑"));
    expect(screen.getByText("📷 扫新电脑上的二维码")).toBeTruthy();
    expect(screen.queryByText("从旧电脑直传 (推荐)")).toBeNull();
    fireEvent.click(screen.getByText(/导出成文件/));
    expect(screen.getByText("正在清点本机数据…")).toBeTruthy();
    fireEvent.click(screen.getByText("← 不是这台"));
    fireEvent.click(screen.getByText("新电脑"));
    expect(screen.getByText("从旧电脑直传 (推荐)")).toBeTruthy();
    expect(screen.queryByText("📷 扫新电脑上的二维码")).toBeNull();
  });

  it("正在传的, 回到这页直接进旧电脑那一半看进度", async () => {
    sendStatus = { state: "sending", id: "t_1", toDevice: "新 Mac", size: 4 << 20, n: 4, sent: 1, acked: 0, files: 9, error: null };
    render(<DeviceMigrationCard />);
    expect(await screen.findByText(/这台是旧电脑/)).toBeTruthy();
    expect(await screen.findByText(/正在传给 新 Mac/)).toBeTruthy();
  });
});
