// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ fetch: vi.fn(), open: vi.fn().mockResolvedValue(undefined) }));
vi.mock("../../lib/http_proxy", () => ({ fetchViaProxy: mocks.fetch }));
vi.mock("../../lib/env", () => ({ config: { webUrl: "https://127.0.0.1" } }));
vi.mock("../../hooks/useMe", () => ({ useMe: () => ({ me: { role: "employee" } }) }));
vi.mock("../../store/ui", () => ({ useUIStore: (select: (s: unknown) => unknown) => select({ openAbout: vi.fn() }) }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion: async () => "test" }));
vi.mock("@tauri-apps/plugin-shell", () => ({ open: mocks.open }));
import WebPortalLink, { pingWeb } from "./WebPortalLink";
afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });
describe("portal diagnostics", () => {
  it("keeps browser links usable when Windows rejects the certificate", async () => {
    mocks.fetch.mockRejectedValue("SEC_E_UNTRUSTED_ROOT (0x80090325)");
    await act(async () => { render(<WebPortalLink />); });
    expect(screen.getByText(/客户端尚未信任服务器证书/)).toBeTruthy();
    fireEvent.click(screen.getByText("📦 资源市场"));
    await act(async () => {});
    expect(mocks.open).toHaveBeenCalledWith("https://127.0.0.1/market");
  });
  it("does not call HTTP 500 healthy", async () => {
    mocks.fetch.mockResolvedValue({ ok: false, status: 500 });
    expect(await pingWeb("https://127.0.0.1")).toMatchObject({ ok: false, detail: expect.stringContaining("500") });
  });
  it("cancels the native request on timeout", async () => {
    vi.useFakeTimers();
    mocks.fetch.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
    }));
    const result = pingWeb("https://127.0.0.1", 20);
    await vi.advanceTimersByTimeAsync(20);
    expect(await result).toMatchObject({ ok: false, kind: "timeout" });
  });
});
