/** IPC 载荷体检。
 *
 * 8/5 Windows 实测: postMessage 抛 `Invalid string length`, 栈里零个应用帧 ——
 * 只知道"某个 invoke 载荷过大", 不知道是哪个。这组测试钉住包装层必须把命令名
 * 带出来, 否则加了等于没加。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { installStartupDiagnostics } from "./startupDiagnostics";

type Inv = (cmd: string, args?: unknown, opts?: unknown) => unknown;
const internals = () =>
  (window as unknown as Record<string, { invoke: Inv }>).__TAURI_INTERNALS__;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  vi.restoreAllMocks();
});

it("正常调用透传, 不改行为", () => {
  const inner = vi.fn(() => "ok");
  (window as any).__TAURI_INTERNALS__ = { invoke: inner };
  installStartupDiagnostics();
  expect(internals().invoke("get_runtime_endpoints", { a: 1 })).toBe("ok");
  expect(inner).toHaveBeenCalledWith("get_runtime_endpoints", { a: 1 }, undefined);
});

it("★ 抛错时必须带上命令名 —— 原始栈里一个应用帧都没有", () => {
  const boom = vi.fn(() => {
    throw new RangeError("Invalid string length");
  });
  (window as any).__TAURI_INTERNALS__ = { invoke: boom };
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  installStartupDiagnostics();
  expect(() => internals().invoke("http_proxy", { req: {} })).toThrow(RangeError);
  const logged = err.mock.calls.flat().join(" ");
  expect(logged).toContain("http_proxy");
});

it("超阈值要 warn 出大小, 不能闷头撞上限", () => {
  (window as any).__TAURI_INTERNALS__ = { invoke: vi.fn(() => "ok") };
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  installStartupDiagnostics();
  internals().invoke("big_one", { blob: "x".repeat(9 * 1024 * 1024) });
  const logged = warn.mock.calls.flat().join(" ");
  expect(logged).toContain("big_one");
  expect(logged).toMatch(/MB/);
});

it("非 Tauri 环境不该炸 —— 诊断不能拖垮启动", () => {
  delete (window as any).__TAURI_INTERNALS__;
  expect(() => installStartupDiagnostics()).not.toThrow();
});
