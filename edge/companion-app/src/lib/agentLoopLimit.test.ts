import { describe, expect, it } from "vitest";
import { agentLoopLimitText, isAgentLoopLimit } from "./agentLoopLimit";

const RAW = 'HTTP 409: {"detail":{"error":"agent_loop_limit_exceeded","message":"本轮工具调用超过当前网关配置的安全上限","limits":{"max_tool_messages":64,"max_tool_calls":128},"observed":{"tool_messages":65}}}';

describe("agentLoopLimit", () => {
  it("识别网关 409 上限错误, 别的错误不算", () => {
    expect(isAgentLoopLimit(RAW)).toBe(true);
    expect(isAgentLoopLimit("HTTP 409: session_exists")).toBe(false);
    expect(isAgentLoopLimit(undefined)).toBe(false);
  });
  it("说法里带上网关的上限数字; 取不到就不提数字", () => {
    expect(agentLoopLimitText(RAW)).toContain("超过 64 次工具调用");
    expect(agentLoopLimitText("agent_loop_limit_exceeded")).not.toMatch(/\d/);
  });
});
