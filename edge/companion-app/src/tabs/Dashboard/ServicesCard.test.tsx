// @vitest-environment jsdom
// 10/29: 服务状态卡 —— 红 / 黄 / 绿的含义, 以及「本地全启」失败要说出来 (原来点了没反应也不知道为什么)。
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { ok } = vi.hoisted(() => ({
  ok: (message: string) => async () => ({ running: true, healthy: true, pid: null, port: 8642, message }),
}));
vi.mock("../../lib/tauri", () => ({
  gatewayStatus: async () => ({ running: false, healthy: false, pid: null, message: "连不上中央服务器（http://x:8999 上没有服务在监听）—— 核对「服务器地址」或联系 IT" }),
  hermesStatus: ok("运行中 · 微信已连接 · 2 个对话进行中"),
  chromeStatus: ok("ok"),
  localSearchStatus: ok("ok"),
  toolBridgeStatus: ok("ok"),
  chromeLaunch: vi.fn(async () => undefined),
  chromeKill: vi.fn(async () => undefined),
  localSearchStart: vi.fn(async () => { throw new Error("找不到 Python"); }),
  localSearchStop: vi.fn(async () => undefined),
  toolBridgeStart: vi.fn(async () => undefined),
  toolBridgeStop: vi.fn(async () => undefined),
  hermesRestart: vi.fn(async () => undefined),
}));

import ServicesCard from "./ServicesCard";

afterEach(() => {
  cleanup();
});

describe("ServicesCard", () => {
  it("本地全启有一项失败 —— 卡片上说出是哪一项、为什么", async () => {
    render(<ServicesCard />);
    await act(async () => {
      fireEvent.click(screen.getByText("本地全启"));
    });
    const alert = await screen.findByText(/本地全启没有全部成功/);
    expect(alert.textContent).toContain("Local Search: 找不到 Python");
  });

  it("Hermes 就绪时也显示就绪详情; 中央网关连不上写人话", async () => {
    render(<ServicesCard />);
    expect(await screen.findByText("运行中 · 微信已连接 · 2 个对话进行中")).toBeTruthy();
    expect((await screen.findAllByText(/连不上中央服务器/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/点 "启动" 按钮/)).toBeNull();
  });
});
