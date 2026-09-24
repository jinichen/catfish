// @vitest-environment jsdom
import { act, renderHook, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ probe: vi.fn(), kill: vi.fn(), setStatus: vi.fn() }));
vi.mock("../lib/tauri", () => ({
  gatewayStatus: mocks.probe, hermesStatus: mocks.probe, chromeStatus: mocks.probe,
  localSearchStatus: mocks.probe, toolBridgeStatus: mocks.probe,
  hermesKill: mocks.kill, chromeKill: mocks.kill, chromeLaunch: mocks.kill,
}));
vi.mock("../store/services", () => ({
  useServicesStore: (selector: (state: unknown) => unknown) => selector({ setStatus: mocks.setStatus }),
}));
vi.mock("../lib/env", () => ({ config: { pollIntervalMs: 1000 } }));
import { useServiceStatus } from "./useServiceStatus";

describe("read-only service polling", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });
  it("never kills a service after repeated unhealthy responses", async () => {
    mocks.probe.mockResolvedValue({ running: true, healthy: false, pid: null });
    renderHook(() => useServiceStatus("hermes"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(mocks.probe.mock.calls.length).toBeGreaterThan(3);
    expect(mocks.kill).not.toHaveBeenCalled();
  });
  it("reports IPC failure as unknown instead of preserving a stale green or red state", async () => {
    mocks.probe.mockRejectedValue("IPC failed");
    renderHook(() => useServiceStatus("chrome"));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(mocks.setStatus).toHaveBeenCalledWith("chrome", expect.objectContaining({
      probeError: true, healthy: false, message: expect.stringContaining("IPC failed"),
    }));
  });
  it("does not overlap slow checks or write after unmount", async () => {
    let resolve!: (value: unknown) => void;
    mocks.probe.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { unmount } = renderHook(() => useServiceStatus("tool_bridge"));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(mocks.probe).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => { resolve({ running: true, healthy: true }); });
    await vi.advanceTimersByTimeAsync(5000);
    expect(mocks.setStatus).not.toHaveBeenCalled();
    expect(mocks.probe).toHaveBeenCalledTimes(1);
  });
});
