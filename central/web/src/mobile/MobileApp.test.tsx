// @vitest-environment jsdom
/** 手机页: 已配过的电脑, 点「连接」后进对话页 (11/01 鸿波: 点连接后整页空白)。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { b64, newKeyPair } from "./crypto";

vi.mock("../lib/auth", () => ({
  getIdToken: async () => "tok",
  getCurrentUser: async () => ({}),
  login: async () => undefined,
}));

class FakeWS {
  static OPEN = 1;
  static last: FakeWS | null = null;
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.last = this;
    queueMicrotask(() => { this.readyState = 1; this.onopen?.(); });
  }
  send(s: string) { this.sent.push(JSON.parse(s)); }
  close() { this.readyState = 3; }
  deliver(f: unknown) { this.onmessage?.({ data: JSON.stringify(f) }); }
}
(globalThis as Record<string, unknown>).WebSocket = FakeWS;
// Node 25 自带一个 (没配文件就不能用的) localStorage, 盖过 jsdom 的; 换成内存版
const mem = new Map<string, string>();
const store = {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => void mem.set(k, v),
  removeItem: (k: string) => void mem.delete(k),
};
Object.defineProperty(globalThis, "localStorage", { value: store, configurable: true });
Object.defineProperty(window, "localStorage", { value: store, configurable: true });

import { MobileApp } from "./MobileApp";

afterEach(cleanup);

describe("MobileApp", () => {
  it("点连接 → 进对话页, 不白屏", async () => {
    localStorage.setItem("catfish.mobile.pcs", JSON.stringify({ "pc-1": { name: "我的 Mac", pk: b64(newKeyPair().publicKey) } }));
    render(<MobileApp />);
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    act(() => {
      FakeWS.last!.deliver({ t: "ready", max_phones: 3 });
      FakeWS.last!.deliver({ t: "peers", peers: [{ device: "pc-1", name: "我的 Mac", paired: true }] });
    });
    fireEvent.click(await screen.findByText("连接"));
    expect(screen.getByText("历史对话")).toBeTruthy();
    expect(screen.getByText("💬 对话")).toBeTruthy();
  });
});

import { SimpleMarkdown } from "../components/SimpleMarkdown";

describe("小鲶回复按 Markdown 显示", () => {
  it("表格成表、分隔线、保留换行", () => {
    const md = "📊 日报\n第二行\n\n---\n\n| 标的 | 收盘 |\n|---|---|\n| NVDA | $204.64 |";
    const { container } = render(<SimpleMarkdown source={md} breaks />);
    expect(container.querySelector("table td")?.textContent).toBe("NVDA");
    expect(container.querySelector("hr")).toBeTruthy();
    expect(container.querySelector("p br")).toBeTruthy();
  });
});

import { needsHomeScreenHint } from "./pwa";

describe("加到主屏幕的提示", () => {
  it("只在 iPhone 浏览器里提示, 主屏幕打开的不提示", () => {
    const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari";
    expect(needsHomeScreenHint(iphone, false)).toBe(true);
    expect(needsHomeScreenHint(iphone, true)).toBe(false);
    expect(needsHomeScreenHint("Mozilla/5.0 (Linux; Android 14) Chrome", false)).toBe(false);
  });
});

import { applyPcApproval } from "./MobileApp";

describe("电脑上的批准推到手机", () => {
  it("新的加上、重复的不加、手机对话里已有的不加、处理掉的收起", () => {
    const m = { op: "approval", runId: "run-1", command: "rm x", description: "删除" };
    let list = applyPcApproval([], m, new Set());
    expect(list).toEqual([{ runId: "run-1", command: "rm x", description: "删除", state: "pending" }]);
    expect(applyPcApproval(list, m, new Set())).toBe(list);
    expect(applyPcApproval([], { ...m, runId: "run-2" }, new Set(["run-2"]))).toEqual([]);
    list = applyPcApproval(list, { op: "approval_gone", runId: "run-1" }, new Set());
    expect(list).toEqual([]);
  });
});
