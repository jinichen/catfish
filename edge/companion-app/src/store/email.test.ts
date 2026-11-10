/** BL-COMPANION-EMAIL-DIGEST-STEP5 (5/20 鸿波) — useEmailStore 单测.
 *
 * 测点:
 * - urgencyMap 启动从 localStorage hydrate
 * - reconcileFromRust 后台拉 + 合并 (Rust > local)
 * - markRead 持久化 readIds 到 localStorage
 * - readIds 上限 1000 LRU 截断
 *
 * 跑法: pnpm exec vitest src/store/email.test.ts
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// mock @tauri-apps/api/core (Tauri invoke) — store 通过 emailUrgencyMap 调
const invokeMock = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

// ── 假 localStorage (vitest jsdom 默认有, 但确保 clean) ──
const _store = new Map<string, string>();
beforeEach(() => {
  _store.clear();
  invokeMock.mockReset();
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    getItem: (k: string) => _store.get(k) ?? null,
    setItem: (k: string, v: string) => { _store.set(k, v); },
    removeItem: (k: string) => { _store.delete(k); },
    clear: () => _store.clear(),
    key: (i: number) => Array.from(_store.keys())[i] ?? null,
    get length() { return _store.size; },
  };
  // 每个测试要重新 import store 才能从干净 localStorage 重新初始化
  vi.resetModules();
});

afterEach(() => {
  _store.clear();
});

describe("useEmailStore - urgency map", () => {
  it("空 localStorage → urgencyMap = {}", async () => {
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().urgencyMap).toEqual({});
  });

  it("启动从 localStorage hydrate urgencyMap", async () => {
    _store.set(
      "catfish.email.urgency.v1",
      JSON.stringify({ map: { "id-1": "急", "id-2": "中" }, savedAt: 123 }),
    );
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().urgencyMap).toEqual({
      "id-1": "急",
      "id-2": "中",
    });
  });

  it("setUrgencyMap 写 localStorage 持久化", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().setUrgencyMap({ "id-X": "急" });

    const raw = _store.get("catfish.email.urgency.v1");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.map).toEqual({ "id-X": "急" });
    expect(parsed.savedAt).toBeGreaterThan(0);
  });

  it("reconcileFromRust 拉 Rust map + 跟 local merge (Rust 权威)", async () => {
    // local 已有 id-A=中, Rust 返 id-A=急 + id-B=低 → merged: id-A=急 (Rust 覆盖) + id-B=低
    _store.set(
      "catfish.email.urgency.v1",
      JSON.stringify({ map: { "id-A": "中" }, savedAt: 100 }),
    );
    invokeMock.mockResolvedValueOnce({ "id-A": "急", "id-B": "低" });

    const { useEmailStore } = await import("./email");
    const merged = await useEmailStore.getState().reconcileFromRust();
    expect(merged).toEqual({ "id-A": "急", "id-B": "低" });
    expect(useEmailStore.getState().urgencyMap).toEqual({ "id-A": "急", "id-B": "低" });
    expect(useEmailStore.getState().lastSyncedAt).not.toBeNull();
  });

  it("reconcileFromRust Rust 调挂 → 保留 local map", async () => {
    _store.set(
      "catfish.email.urgency.v1",
      JSON.stringify({ map: { "id-1": "急" }, savedAt: 100 }),
    );
    invokeMock.mockRejectedValueOnce(new Error("scheduler not ready"));

    const { useEmailStore } = await import("./email");
    const result = await useEmailStore.getState().reconcileFromRust();
    expect(result).toEqual({ "id-1": "急" });  // local 保留
    expect(useEmailStore.getState().lastSyncedAt).toBeNull();  // 没成功 sync
  });

  it("localStorage 损坏 → hydrate 空 map (不抛)", async () => {
    _store.set("catfish.email.urgency.v1", "not json{{}}");
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().urgencyMap).toEqual({});
  });
});

describe("useEmailStore - readIds (sub-task 2 标已读 dedup)", () => {
  it("空 localStorage → readIds = empty set", async () => {
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().readIds.size).toBe(0);
  });

  it("启动从 localStorage hydrate readIds", async () => {
    _store.set(
      "catfish.email.read.v1",
      JSON.stringify({ ids: ["msg-1", "msg-2", "msg-3"], savedAt: 100 }),
    );
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().readIds.size).toBe(3);
    expect(useEmailStore.getState().isRead("msg-1")).toBe(true);
    expect(useEmailStore.getState().isRead("msg-4")).toBe(false);
  });

  it("markRead 加 id + 持久化", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().markRead("urgent-msg-1");
    expect(useEmailStore.getState().isRead("urgent-msg-1")).toBe(true);

    const raw = _store.get("catfish.email.read.v1");
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);
    expect(parsed.ids).toContain("urgent-msg-1");
  });

  it("markRead 同 id 重复调不重复持久化", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().markRead("id-X");
    const firstSave = _store.get("catfish.email.read.v1");
    useEmailStore.getState().markRead("id-X");  // 重复
    const secondSave = _store.get("catfish.email.read.v1");
    // 第二次没真写 (set 没变) — set 实现也保证一致
    expect(JSON.parse(firstSave!).ids).toEqual(JSON.parse(secondSave!).ids);
  });

  it("markReadBulk 加多个 + 一次持久化", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().markReadBulk(["a", "b", "c"]);
    const state = useEmailStore.getState();
    expect(state.isRead("a")).toBe(true);
    expect(state.isRead("b")).toBe(true);
    expect(state.isRead("c")).toBe(true);
    expect(state.readIds.size).toBe(3);
  });

  it("markReadBulk 空数组 / 空 id 跳过", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().markReadBulk([]);
    useEmailStore.getState().markReadBulk(["", "x"]);
    expect(useEmailStore.getState().readIds.size).toBe(1);
    expect(useEmailStore.getState().isRead("x")).toBe(true);
  });

  it("clearReadHistory 重置 readIds 跟 localStorage", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().markReadBulk(["a", "b"]);
    expect(useEmailStore.getState().readIds.size).toBe(2);

    useEmailStore.getState().clearReadHistory();
    expect(useEmailStore.getState().readIds.size).toBe(0);

    const raw = _store.get("catfish.email.read.v1");
    expect(JSON.parse(raw!).ids).toEqual([]);
  });

  it("readIds 上限 1000 LRU 截断", async () => {
    // hydrate 1500 个 → 持久化时截 1000
    const huge = Array.from({ length: 1500 }, (_, i) => `id-${i}`);
    _store.set(
      "catfish.email.read.v1",
      JSON.stringify({ ids: huge, savedAt: 100 }),
    );
    const { useEmailStore } = await import("./email");
    // hydrate 时不截 (set 接 1500), 但 markRead 调 savePersistedRead 时截
    useEmailStore.getState().markRead("new-id");
    const raw = _store.get("catfish.email.read.v1");
    const parsed = JSON.parse(raw!);
    expect(parsed.ids.length).toBeLessThanOrEqual(1000);
    // 最新加的应该保留 (LRU 截掉老的)
    expect(parsed.ids).toContain("new-id");
  });

  it("localStorage 损坏 → readIds 空 (不抛)", async () => {
    _store.set("catfish.email.read.v1", "}}}}{{{");
    const { useEmailStore } = await import("./email");
    expect(useEmailStore.getState().readIds.size).toBe(0);
  });
});

describe("useEmailStore - 列表落盘缓存 (10/31)", () => {
  it("重启后从磁盘读回上次的列表; 内存里已经有就不覆盖", async () => {
    const { useEmailStore } = await import("./email");
    invokeMock.mockImplementation(async (cmd: string) =>
      cmd === "email_list_cache_load"
        ? JSON.stringify({ items: [{ id: "a" }], sentItems: [{ id: "s" }], accounts: [], savedAt: 5 })
        : undefined,
    );
    await useEmailStore.getState().hydrateListCache();
    expect(useEmailStore.getState().listCache?.items).toEqual([{ id: "a" }]);

    invokeMock.mockImplementation(async (cmd: string) =>
      cmd === "email_list_cache_load" ? JSON.stringify({ items: [{ id: "stale" }] }) : undefined,
    );
    await useEmailStore.getState().hydrateListCache();
    expect(useEmailStore.getState().listCache?.items).toEqual([{ id: "a" }]);
  });

  it("拉到新列表时落盘", async () => {
    const { useEmailStore } = await import("./email");
    invokeMock.mockResolvedValue(undefined);
    useEmailStore.getState().setListCache({ items: [{ id: "b" }], sentItems: [], accounts: [] });
    const call = invokeMock.mock.calls.find((c) => c[0] === "email_list_cache_save");
    expect(call).toBeTruthy();
    expect(JSON.parse((call![1] as { payload: string }).payload).items).toEqual([{ id: "b" }]);
  });

  it("磁盘读不到 / 坏文件不报错", async () => {
    const { useEmailStore } = await import("./email");
    invokeMock.mockResolvedValue("{坏的");
    await expect(useEmailStore.getState().hydrateListCache()).resolves.toBeUndefined();
    expect(useEmailStore.getState().listCache).toBeNull();
  });
});

// 11/11: 「看过了切走再回来又变未读」
describe("读过的邮件不被旧列表改回未读", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    invokeMock.mockResolvedValue(undefined); // 缓存落盘走 Tauri invoke, 真实里返回 Promise
  });
  afterEach(() => vi.useRealTimers());

  const mail = (id: string, is_read: boolean) => ({ id, is_read, date: "2026-11-10T19:02:00" });

  it("点开时跨挂载的列表缓存也改成已读 (切回来先显示的就是它)", async () => {
    const { useEmailStore } = await import("./email");
    useEmailStore.getState().setListCache({ items: [mail("a", false), mail("b", false)], sentItems: [], accounts: [] });
    useEmailStore.getState().markRead("a");
    const items = useEmailStore.getState().listCache!.items as { id: string; is_read: boolean }[];
    expect(items.map((m) => [m.id, m.is_read])).toEqual([["a", true], ["b", false]]);
  });

  it("点开之前发出的拉取晚回来: 保持已读; 之后发出的拉取: 以邮件客户端为准", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-11T05:00:00"));
    const { useEmailStore, READ_SETTLE_MS } = await import("./email");
    const fetchStartedBefore = Date.now();
    vi.advanceTimersByTime(8000); // 拉取在路上的时候点开了信
    useEmailStore.getState().markRead("a");
    const readAt = Date.now();

    const stale = useEmailStore.getState().applyRecentRead([mail("a", false), mail("b", false)], fetchStartedBefore);
    expect(stale.map((m) => m.is_read)).toEqual([true, false]);

    // 刚读完那几秒内发出的, 客户端可能还没落库 —— 也保持已读
    expect(useEmailStore.getState().applyRecentRead([mail("a", false)], readAt + READ_SETTLE_MS)[0].is_read).toBe(true);
    // 再往后发出的拉取说未读 (员工在邮件客户端里手动标回未读) → 跟着变
    expect(useEmailStore.getState().applyRecentRead([mail("a", false)], readAt + READ_SETTLE_MS + 1)[0].is_read).toBe(false);
  });

  it("没点开过的信原样返回", async () => {
    const { useEmailStore } = await import("./email");
    const list = [mail("x", false)];
    expect(useEmailStore.getState().applyRecentRead(list, Date.now())).toBe(list);
  });
});
