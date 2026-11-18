// @vitest-environment jsdom
/** 11/18: 撤销手机原来用 window.confirm —— Tauri WebView 里不弹、直接当「取消」, 按钮点了没反应。改成应用内确认框。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ status: vi.fn(), revoke: vi.fn(), confirm: vi.fn() }));
vi.mock("../../lib/mobile", async (orig) => ({
  ...(await orig<typeof import("../../lib/mobile")>()),
  mobileStatus: api.status,
  mobileRevoke: api.revoke,
  mobileSetEnabled: vi.fn(async () => undefined),
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => undefined) }));
vi.mock("../../components/dialogs", () => ({ confirmDialog: api.confirm }));

import MobileCard from "./MobileCard";

const STATUS = {
  enabled: true, connected: true, error: null, maxPhones: 2, relayUrl: "", mobileUrl: "", pending: [],
  phones: [{ device: "d1", name: "鸿波的 iPhone", publicKey: "k", pairedAt: new Date().toISOString(), lastSeen: new Date().toISOString() }],
};

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.status.mockResolvedValue(STATUS);
  api.revoke.mockResolvedValue(undefined);
});
afterEach(cleanup);

describe("撤销手机", () => {
  it("弹应用内确认框; 取消就不撤, 确认才撤", async () => {
    await act(async () => { render(<MobileCard />); });
    api.confirm.mockResolvedValueOnce(false);
    await act(async () => { fireEvent.click(screen.getByText("撤销")); });
    expect(api.confirm).toHaveBeenCalledWith(expect.stringContaining("撤销「鸿波的 iPhone」"), "撤销手机");
    expect(api.revoke).not.toHaveBeenCalled();
    api.confirm.mockResolvedValueOnce(true);
    await act(async () => { fireEvent.click(screen.getByText("撤销")); });
    expect(api.revoke).toHaveBeenCalledWith("d1");
  });
});
