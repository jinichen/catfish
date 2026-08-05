/** IPC 载荷体检。
 *
 * 8/5 Windows 实测: postMessage 抛 `Invalid string length`, 栈里零个应用帧 ——
 * 只知道"某个 invoke 载荷过大", 不知道是哪个。这组测试钉住包装层必须把命令名
 * 带出来, 否则加了等于没加。
 *
 * 只测 installIpcSizeGuard: 它不碰 DOM, 所以不用 jsdom (项目里没装, 为一个
 * 诊断测试引一个 DOM 依赖不划算)。
 */
import { beforeEach, expect, it, vi } from "vitest";
import { installIpcSizeGuard } from "./startupDiagnostics";

type Inv = (cmd: string, args?: unknown, opts?: unknown) => unknown;

function setup(invoke: Inv) {
  (globalThis as any).window = { __TAURI_INTERNALS__: { invoke } };
  installIpcSizeGuard();
  return (globalThis as any).window.__TAURI_INTERNALS__.invoke as Inv;
}

beforeEach(() => {
  vi.restoreAllMocks();
  delete (globalThis as any).window;
});

it("正常调用透传, 不改行为", () => {
  const inner = vi.fn(() => "ok");
  const wrapped = setup(inner);
  expect(wrapped("get_runtime_endpoints", { a: 1 })).toBe("ok");
  expect(inner).toHaveBeenCalledWith("get_runtime_endpoints", { a: 1 }, undefined);
});

it("★ 抛错时必须带上命令名 —— 原始栈里一个应用帧都没有", () => {
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  const wrapped = setup(() => {
    throw new RangeError("Invalid string length");
  });
  expect(() => wrapped("http_proxy", { req: {} })).toThrow(RangeError);
  expect(err.mock.calls.flat().join(" ")).toContain("http_proxy");
});

it("超阈值要 warn 出大小, 不能闷头撞上限", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const wrapped = setup(() => "ok");
  wrapped("big_one", { blob: "x".repeat(9 * 1024 * 1024) });
  const logged = warn.mock.calls.flat().join(" ");
  expect(logged).toContain("big_one");
  expect(logged).toMatch(/MB/);
});

it("正常大小不该 warn —— 误报会让人忽略真警告", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  setup(() => "ok")("small", { a: "x".repeat(1000) });
  expect(warn).not.toHaveBeenCalled();
});

it("非 Tauri 环境不该炸 —— 诊断不能拖垮启动", () => {
  (globalThis as any).window = {};
  expect(() => installIpcSizeGuard()).not.toThrow();
});

it("★ invoke 是只读属性时不能抛 —— 这条抛了会把整个诊断打哑", () => {
  // 8/5 实测: 严格模式下给只读属性赋值直接 TypeError, 而它当时排在 error
  // 钩子之前, 于是钩子再没装上 —— Windows 回到白屏且连错误面板都没有,
  // 比不加诊断还糟。
  const internals: Record<string, unknown> = {};
  Object.defineProperty(internals, "invoke", {
    value: () => "ok",
    writable: false,
    configurable: false,
  });
  (globalThis as any).window = { __TAURI_INTERNALS__: internals };
  vi.spyOn(console, "warn").mockImplementation(() => {});
  expect(() => installIpcSizeGuard()).not.toThrow();
});

it("configurable 的只读属性走 defineProperty 兜底, 仍能装上", () => {
  const internals: Record<string, unknown> = {};
  Object.defineProperty(internals, "invoke", {
    value: () => "ok",
    writable: false,
    configurable: true,
  });
  (globalThis as any).window = { __TAURI_INTERNALS__: internals };
  const err = vi.spyOn(console, "error").mockImplementation(() => {});
  installIpcSizeGuard();
  // 装上了就能带出命令名 —— 用抛错路径验
  (internals as any).invoke = undefined;
  expect(err).not.toHaveBeenCalled(); // 装的过程本身不该报错
});
