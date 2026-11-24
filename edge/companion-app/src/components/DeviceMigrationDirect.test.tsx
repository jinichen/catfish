// @vitest-environment jsdom
/** 11/21 换机直传: 新电脑出码 → 收齐后看包、导入; 旧电脑手输配对码 → 核对数字 → 开始传 → 进度; 摄像头打不开改输码。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));
vi.mock("qrcode", () => ({ default: { toCanvas: vi.fn(() => Promise.resolve()) } }));

import DeviceMigrationDirectReceive from "./DeviceMigrationDirectReceive";
import DeviceMigrationDirectSend from "./DeviceMigrationDirectSend";

const info = {
  createdAt: "2026-11-21T10:00:00+08:00",
  sourceOs: "macos",
  userEmail: "linxiao@x.cn",
  companionVersion: "1.2.238",
  files: 120,
  bytes: 5_000_000,
  entries: [],
  withFiles: false,
  samePerson: true,
  loggedInAs: "linxiao@x.cn",
  versionOk: true,
  targetHasData: false,
};
const base = { id: "t_1", code: "ABCD-EFGH", qr: "catfishmove:1:t_1:age1x", safety: "123 456", toDevice: "新 Mac", fromDevice: "", got: 0, n: 0, size: 0, error: null, info: null };

beforeEach(() => invoke.mockReset());
afterEach(cleanup);

describe("新电脑: 从旧电脑直传", () => {
  it("出码 (配对码 + 核对数字) → 收齐 → 看包 → 导入", async () => {
    let status: unknown = null;
    invoke.mockImplementation((cmd: string) => {
      if (cmd === "migration_direct_receive_status") return Promise.resolve(status);
      if (cmd === "migration_direct_receive_start") return Promise.resolve((status = { ...base, state: "waiting" }));
      if (cmd === "migration_direct_import")
        return Promise.resolve({ files: 120, bytes: 5_000_000, pathsRewritten: 0, keptExisting: 0, backupDir: null, redo: ["邮箱：重新授权"], warnings: [] });
      return Promise.resolve(undefined);
    });
    render(<DeviceMigrationDirectReceive />);
    fireEvent.click(await screen.findByText("开始: 在这台出二维码"));
    expect(await screen.findByText("ABCD-EFGH")).toBeTruthy();
    expect(screen.getByText("123 456")).toBeTruthy();
    expect(screen.getByLabelText("换机直传二维码")).toBeTruthy();

    status = { ...base, state: "ready", fromDevice: "旧 MacBook", got: 5, n: 5, size: 5_000_000, info };
    expect(await screen.findByText(/收齐了, 校验通过/, {}, { timeout: 3000 })).toBeTruthy();
    fireEvent.click(screen.getByText("开始导入"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("migration_direct_import", { overwrite: false }));
    expect(await screen.findByText(/导入完成/)).toBeTruthy();
  });

  it("失败了说清楚, 能重新开始", async () => {
    invoke.mockImplementation((cmd: string) =>
      Promise.resolve(cmd === "migration_direct_receive_status" ? { ...base, state: "failed", error: "等了半小时旧电脑没开始传" } : undefined));
    render(<DeviceMigrationDirectReceive />);
    expect(await screen.findByText(/等了半小时旧电脑没开始传/)).toBeTruthy();
    expect(screen.getByText("重新开始")).toBeTruthy();
  });
});

describe("旧电脑: 直传到新电脑", () => {
  it("手输配对码 → 核对数字 → 开始传 (带不带文件员工定)", async () => {
    let status: unknown = null;
    invoke.mockImplementation((cmd: string, a?: Record<string, unknown>) => {
      if (cmd === "migration_direct_send_status") return Promise.resolve(status);
      if (cmd === "migration_direct_lookup") return Promise.resolve({ id: "t_1", recipient: "age1x", toDevice: "新 Mac", safety: "123 456" });
      if (cmd === "migration_direct_send") {
        status = { state: "sending", id: "t_1", toDevice: "新 Mac", size: 4 * 1024 * 1024, n: 4, sent: 2, acked: 1, files: 120, error: null };
        return Promise.resolve(status);
      }
      return Promise.resolve(a);
    });
    render(<DeviceMigrationDirectSend files={["catfish/uploads"]} filesBytes={2048} />);
    fireEvent.click(screen.getByText("输配对码"));
    fireEvent.change(screen.getByPlaceholderText("新电脑上的 8 位配对码"), { target: { value: "abcd efgh" } });
    expect((screen.getByPlaceholderText("新电脑上的 8 位配对码") as HTMLInputElement).value).toBe("ABCD-EFGH");
    fireEvent.click(screen.getByText("找到新电脑"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("migration_direct_lookup", { code: "ABCD-EFGH" }));
    expect(await screen.findByText("123 456")).toBeTruthy();
    fireEvent.click(screen.getByText("一样, 开始传"));
    await waitFor(() =>
      expect(invoke).toHaveBeenCalledWith("migration_direct_send", {
        target: { id: "t_1", recipient: "age1x", toDevice: "新 Mac", safety: "123 456" },
        optional: [],
      }));
    expect(await screen.findByText(/正在传给 新 Mac… 50%/)).toBeTruthy();
  });

  it("没有摄像头: 说清楚, 改成输配对码", async () => {
    invoke.mockResolvedValue(null);
    render(<DeviceMigrationDirectSend files={[]} filesBytes={0} />);
    fireEvent.click(screen.getByText("扫新电脑上的二维码"));
    expect(await screen.findByText(/改输配对码/)).toBeTruthy();
    expect(screen.getByPlaceholderText("新电脑上的 8 位配对码")).toBeTruthy();
  });
});
