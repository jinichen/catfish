// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invokeMock(...a) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: () => Promise.resolve(() => {}) }));

import ConnectLinkDialog from "./ConnectLinkDialog";

const LINK = { webUrl: "https://10.0.5.20", gatewayUrl: "http://10.0.5.20:8999", identityUrl: "http://10.0.5.20:8998", code: "88DC-A0B2-B193-3921" };
const reload = vi.fn();

beforeEach(() => {
  Object.defineProperty(window, "location", { value: { ...window.location, reload }, writable: true });
});
afterEach(() => { cleanup(); invokeMock.mockReset(); reload.mockReset(); });

function route(pairFails = false) {
  let taken = false;
  invokeMock.mockImplementation((cmd: string) => {
    if (cmd === "connect_link_take") { const r = taken ? null : LINK; taken = true; return Promise.resolve(r); }
    if (cmd === "pair_server_certificate") return pairFails ? Promise.reject("接入码对不上, 没有导入。") : Promise.resolve("/p");
    if (cmd === "read_server_config") return Promise.resolve({ gateway_token: "", gateway_url: "", identity_url: "" });
    if (cmd === "write_server_config") return Promise.resolve(true);
    return Promise.resolve(null);
  });
}

describe("catfish://connect 接入链接 (10/14)", () => {
  it("先给员工看清连哪台, 点连接: 核对接入码 → 写配置 → 刷新", async () => {
    route();
    await act(async () => { render(<ConnectLinkDialog />); });
    expect(screen.getByText("https://10.0.5.20")).toBeTruthy();
    expect(screen.getByText("88DC-A0B2-B193-3921")).toBeTruthy();
    expect(invokeMock.mock.calls.some((c) => c[0] === "write_server_config")).toBe(false); // 没确认前不动配置
    await act(async () => { fireEvent.click(screen.getByText("连接")); });
    const cmds = invokeMock.mock.calls.map((c) => c[0]);
    expect(cmds.indexOf("pair_server_certificate")).toBeLessThan(cmds.indexOf("write_server_config"));
    expect(invokeMock).toHaveBeenCalledWith("write_server_config", expect.objectContaining({
      gatewayUrl: LINK.gatewayUrl, identityUrl: LINK.identityUrl, webUrl: LINK.webUrl }));
    expect(reload).toHaveBeenCalled();
  });

  it("接入码对不上: 不写配置, 说原因", async () => {
    route(true);
    await act(async () => { render(<ConnectLinkDialog />); });
    await act(async () => { fireEvent.click(screen.getByText("连接")); });
    expect(invokeMock.mock.calls.some((c) => c[0] === "write_server_config")).toBe(false);
    expect(screen.getByRole("alert").textContent).toContain("对不上");
    expect(reload).not.toHaveBeenCalled();
  });

  it("取消就关掉, 什么都不改", async () => {
    route();
    await act(async () => { render(<ConnectLinkDialog />); });
    fireEvent.click(screen.getByText("取消"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(invokeMock.mock.calls.some((c) => c[0] === "write_server_config")).toBe(false);
  });
});
