import { describe, expect, it } from "vitest";
import type { ChatMessage, ToolCall } from "../types/chat";
import {
  groupToolSteps,
  summarizeToolNames,
  toolCallNeedsAttention,
} from "./chatToolSteps";

const call = (id: string, name = "skill_manage", extra: Partial<ToolCall> = {}): ToolCall => ({
  id, name, args: {}, status: "done", result: '{"ok":true}', ...extra,
});
const msg = (id: string, role: ChatMessage["role"], content = "", tool_calls?: ToolCall[]): ChatMessage => ({
  id, role, content, tool_calls, ts: "2026-10-10T00:00:00Z",
});

describe("groupToolSteps", () => {
  it("连续纯工具消息合并成一组, tool 消息夹在中间不打断", () => {
    const groups = groupToolSteps([
      msg("u1", "user", "记住这个填报方式"),
      msg("a1", "assistant", "", [call("c1")]),
      msg("t1", "tool"),
      msg("a2", "assistant", "", [call("c2")]),
      msg("t2", "tool"),
      msg("a3", "assistant", "", [call("c3")]),
      msg("a4", "assistant", "已存成 skill"),
    ]);
    expect(groups.map((g) => g.msg.id)).toEqual(["u1", "a1", "a4"]);
    expect(groups[1].memberIds).toEqual(["a1", "a2", "a3"]);
    expect(groups[1].msg.tool_calls?.map((c) => c.id)).toEqual(["c1", "c2", "c3"]);
  });

  it("有正文或出错的 assistant 消息不并入", () => {
    const err = { ...msg("a2", "assistant", "", [call("c2")]), status: "error" as const };
    const groups = groupToolSteps([
      msg("a1", "assistant", "先查一下", [call("c1")]),
      err,
      msg("a3", "assistant", "", [call("c3")]),
    ]);
    expect(groups.map((g) => g.memberIds)).toEqual([["a1"], ["a2"], ["a3"]]);
  });

  it("不修改传入的消息", () => {
    const a1 = msg("a1", "assistant", "", [call("c1")]);
    groupToolSteps([a1, msg("a2", "assistant", "", [call("c2")])]);
    expect(a1.tool_calls).toHaveLength(1);
  });
});

describe("toolCallNeedsAttention", () => {
  it("普通完成的步骤可以收起", () => {
    expect(toolCallNeedsAttention(call("c1"))).toBe(false);
  });
  it("运行中、出错、等批准、产出文件都要露出", () => {
    expect(toolCallNeedsAttention(call("c", "x", { status: "running" }))).toBe(true);
    expect(toolCallNeedsAttention(call("c", "x", { status: "error" }))).toBe(true);
    expect(toolCallNeedsAttention(call("c", "x", { result: '{"status":"pending_approval"}' }))).toBe(true);
    expect(toolCallNeedsAttention(call("c", "x", { result: "已保存 /Users/a/outputs/汇总.xlsx" }))).toBe(true);
  });
});

describe("summarizeToolNames", () => {
  it("同名计数, 超过上限省略", () => {
    expect(summarizeToolNames([call("1"), call("2"), call("3")])).toBe("skill_manage ×3");
    expect(summarizeToolNames(["a", "b", "c", "d"].map((n) => call(n, n)))).toBe("a、b、c 等");
  });
});
