/** BL-COMPANION-SESSION-DEDUP (5/20 鸿波) — sessionGroup 算法单测.
 *
 * 鸿波本机 sidebar 看到两条 "手动测 sync 函数 16:42:13 已完..." 一条 76 条
 * 一条 18 条, 是不同 session 但 LLM 生成 title 撞名. groupSessionsByTitle
 * 把同 title 堆成一组 — 主条最新 + 后面 N 条 sub-session.
 *
 * 跑法: pnpm exec vitest src/lib/sessionGroup.test.ts
 */

import { describe, expect, it } from "vitest";

import { dedupKey, effectiveTitle, groupSessionsByTitle } from "./sessionGroup";
import type { SessionMeta } from "../types/session";

function mk(over: Partial<SessionMeta> = {}): SessionMeta {
  return {
    id: "id-default",
    title: null,
    model: "test",
    startedAt: "2026-05-20T10:00:00Z",
    endedAt: null,
    endReason: null,
    messageCount: 0,
    totalTokens: 0,
    source: "companion",
    firstUserMessage: null,
    ...over,
  } as SessionMeta;
}

describe("effectiveTitle", () => {
  it("uses title when present", () => {
    expect(effectiveTitle(mk({ title: "我的会话" }))).toBe("我的会话");
  });

  it("trims title whitespace", () => {
    expect(effectiveTitle(mk({ title: "  我的会话  " }))).toBe("我的会话");
  });

  it("falls back to firstUserMessage when title empty", () => {
    expect(
      effectiveTitle(mk({ title: null, firstUserMessage: "你好小鲶" })),
    ).toBe("你好小鲶");
  });

  it("falls back to id prefix when both empty", () => {
    expect(
      effectiveTitle(mk({ id: "20260520_abcdef1234567890extra" })),
    ).toBe("(20260520_abcdef12)");
  });

  it("treats whitespace-only title as empty", () => {
    expect(
      effectiveTitle(mk({ title: "   ", firstUserMessage: "fb" })),
    ).toBe("fb");
  });
});

describe("groupSessionsByTitle", () => {
  it("single session → single group of size 1", () => {
    const groups = groupSessionsByTitle([mk({ id: "a", title: "X" })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(1);
    expect(groups[0].displayTitle).toBe("X");
  });

  it("duplicate titles → single group of size N", () => {
    // 鸿波场景: "手动测 sync 函数 16:42:13 已完..." 两条
    const groups = groupSessionsByTitle([
      mk({ id: "newer", title: "手动测 sync", messageCount: 18 }),
      mk({ id: "older", title: "手动测 sync", messageCount: 76 }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(2);
    // displayTitle 取第一条 (caller 按 started_at desc 排, 主条最新)
    expect(groups[0].displayTitle).toBe("手动测 sync");
    // session 顺序保留
    expect(groups[0].sessions[0].id).toBe("newer");
    expect(groups[0].sessions[1].id).toBe("older");
  });

  it("different titles → multiple groups", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "X" }),
      mk({ id: "b", title: "Y" }),
      mk({ id: "c", title: "Z" }),
    ]);
    expect(groups).toHaveLength(3);
    expect(groups.every((g) => g.sessions.length === 1)).toBe(true);
  });

  it("title 大小写不敏感", () => {
    // "X" / "x" / " X " 都算同组 (但 displayTitle 保留第一条原 case)
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "Foo Bar" }),
      mk({ id: "b", title: "foo bar" }),
      mk({ id: "c", title: " FOO BAR " }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(3);
    expect(groups[0].displayTitle).toBe("Foo Bar");
  });

  it("混合: 部分撞名 + 部分独立", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "撞名" }),
      mk({ id: "b", title: "独立 1" }),
      mk({ id: "c", title: "撞名" }),
      mk({ id: "d", title: "独立 2" }),
      mk({ id: "e", title: "撞名" }),
    ]);
    expect(groups).toHaveLength(3);
    const dup = groups.find((g) => g.displayTitle === "撞名")!;
    expect(dup.sessions).toHaveLength(3);
    expect(dup.sessions.map((s) => s.id)).toEqual(["a", "c", "e"]);
  });

  it("title null + firstUserMessage 撞名 也算同组", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: null, firstUserMessage: "你好" }),
      mk({ id: "b", title: null, firstUserMessage: "你好" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(2);
    expect(groups[0].displayTitle).toBe("你好");
  });

  it("title 撞 firstUserMessage 也算同组 (effectiveTitle 同值)", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "你好" }),
      mk({ id: "b", title: null, firstUserMessage: "你好" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(2);
  });

  it("空 input → 空 array", () => {
    expect(groupSessionsByTitle([])).toEqual([]);
  });

  it("保留 input 顺序: groups 按第一次遇到 key 的顺序", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "y1", title: "Y" }),
      mk({ id: "x1", title: "X" }),
      mk({ id: "y2", title: "Y" }),
      mk({ id: "x2", title: "X" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].displayTitle).toBe("Y");
    expect(groups[1].displayTitle).toBe("X");
  });

  // ── prefix dedup (5/20 v2): 跟 sidebar ellipsis 截断长度对齐 ──

  it("prefix dedup: 前 22 字符相同, 后缀不同 → 同组", () => {
    // 鸿波场景: "手动测 sync 函数 16:42:13 已完成 v0.1.7" vs "...v0.1.8"
    // 前 22 字符 "手动测 sync 函数 16:42:13 已完" 相同
    const groups = groupSessionsByTitle([
      mk({ id: "v17", title: "手动测 sync 函数 16:42:13 已完成 v0.1.7" }),
      mk({ id: "v18", title: "手动测 sync 函数 16:42:13 已完成 v0.1.8" }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(2);
    // displayTitle 取第一条原 title (全文 — 渲染时 sidebar CSS 截断)
    expect(groups[0].displayTitle).toBe("手动测 sync 函数 16:42:13 已完成 v0.1.7");
  });

  it("prefix dedup: 前 22 字符不同 → 不同组 (防过激合并)", () => {
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "汇报 5月20日 周二待办" }),
      mk({ id: "b", title: "汇报 5月21日 周三计划" }),
    ]);
    // 前 22 字符不同 (5月20 vs 5月21 在第 6 字符就分叉)
    expect(groups).toHaveLength(2);
  });

  it("dedupKey: 短 title 直接返", () => {
    expect(dedupKey("短")).toBe("短");
    expect(dedupKey("Hello")).toBe("hello");
  });

  it("dedupKey: 长 title 截前 22 字符", () => {
    const long = "手动测 sync 函数 16:42:13 已完成 v0.1.7";
    const key = dedupKey(long);
    expect(key).toHaveLength(22);
    expect(key).toBe(long.toLowerCase().slice(0, 22));
  });

  it("prefix dedup + 大小写不敏感", () => {
    // 前 22 字符相同 (差异在 22 字符之后), 但大小写不一样
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "Catfish-Long-Common-Title-v0.1.7" }),
      mk({ id: "b", title: "catfish-long-common-title-v0.1.8" }),
    ]);
    // 前 22 字符 lowercase trim = "catfish-long-common-ti"
    expect(groups).toHaveLength(1);
    expect(groups[0].sessions).toHaveLength(2);
  });

  it("prefix dedup: 鸿波截图三条 ### 添加 TODO 类视觉撞名", () => {
    // 截图里 "### 添加 TODO 任务并确认重复" / "### 添加测试 TODO 任务"
    // 前 8 字符相同 ("### 添加 ") 后面分叉 → 22 字符前缀仍不同, 不应合并
    const groups = groupSessionsByTitle([
      mk({ id: "a", title: "### 添加 TODO 任务并确认重复" }),
      mk({ id: "b", title: "### 添加测试 TODO 任务" }),
    ]);
    // "### 添加 TODO 任务并确认重复" 前 22 字: "### 添加 TODO 任务并确认重复" (= 22 字符)
    // "### 添加测试 TODO 任务" 前 22 字: "### 添加测试 TODO 任务" (= 14 字符, 全)
    // 前 8 字符 "### 添加 " vs "### 添加测" 第 8 字不同 (空格 vs 测) → 不同组
    expect(groups).toHaveLength(2);
  });
});
