import { describe, expect, it } from "vitest";
import type { ChatMessage, ToolCall } from "../types/chat";
import {
  groupToolSteps,
  summarizeToolNames,
  toolCallNeedsCard,
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

  it("工具步骤并进紧挨着的上一条有正文的回答, 用户说话才断开", () => {
    const groups = groupToolSteps([
      msg("u1", "user", "分类栏应该同类合并?"),
      msg("a1", "assistant", "", [call("c1", "write_file")]),
      msg("a2", "assistant", "等一下, 改成只动数据区", [call("c2", "patch")]),
      msg("a3", "assistant", "", [call("c3", "execute_code")]),
      msg("u2", "user", "好"),
      msg("a4", "assistant", "", [call("c4")]),
    ]);
    expect(groups.map((g) => g.memberIds)).toEqual([["u1"], ["a1"], ["a2", "a3"], ["u2"], ["a4"]]);
    expect(groups[2].msg.content).toBe("等一下, 改成只动数据区");
    expect(groups[2].msg.tool_calls?.map((c) => c.id)).toEqual(["c2", "c3"]);
  });

  it("出错的 assistant 消息不并入, 后面的也不并给它", () => {
    const err = { ...msg("a2", "assistant", "", [call("c2")]), status: "error" as const };
    const groups = groupToolSteps([
      msg("u1", "user", "x"),
      err,
      msg("a3", "assistant", "", [call("c3")]),
    ]);
    expect(groups.map((g) => g.memberIds)).toEqual([["u1"], ["a2"], ["a3"]]);
  });

  it("不修改传入的消息", () => {
    const a1 = msg("a1", "assistant", "", [call("c1")]);
    groupToolSteps([a1, msg("a2", "assistant", "", [call("c2")])]);
    expect(a1.tool_calls).toHaveLength(1);
  });
});

describe("toolCallNeedsCard", () => {
  it("普通完成、产出文件的步骤都收起", () => {
    expect(toolCallNeedsCard(call("c1"))).toBe(false);
    expect(toolCallNeedsCard(call("c", "x", { result: "已保存 /Users/a/outputs/汇总.xlsx" }))).toBe(false);
  });
  it("运行中、出错、等批准要整张卡片露出", () => {
    expect(toolCallNeedsCard(call("c", "x", { status: "running" }))).toBe(true);
    expect(toolCallNeedsCard(call("c", "x", { status: "error" }))).toBe(true);
    expect(toolCallNeedsCard(call("c", "x", { result: '{"status":"pending_approval"}' }))).toBe(true);
  });
});
