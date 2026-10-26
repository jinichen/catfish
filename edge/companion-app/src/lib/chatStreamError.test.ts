import { describe, expect, it } from "vitest";
import { chatStreamError, isTruncatedChunk } from "./chatStreamError";

describe("chatStreamError", () => {
  it.each([
    { error: "401 invalid token" },
    { error: { message: "401 invalid token" } },
    { hermes: { failed: true, error: "401 invalid token" } },
    { error: {}, hermes: { error: "401 invalid token" } },
  ])("preserves error detail: %j", (chunk) => {
    expect(chatStreamError(chunk)).toBe("401 invalid token");
  });
  it("reports an explicit failure even without a message", () => {
    expect(chatStreamError({ choices: [{ finish_reason: "error" }] }))
      .toContain("未提供错误详情");
    expect(chatStreamError({ hermes: { failed: true, error: null } }))
      .toContain("未提供错误详情");
  });
  it.each([null, {}, { choices: [{ finish_reason: "stop" }] },
    { choices: [{ finish_reason: "length" }] }, { hermes: { failed: false } },
  ])("does not invent failures: %j", (chunk) => {
    expect(chatStreamError(chunk)).toBeUndefined();
  });
});

describe("isTruncatedChunk", () => {
  it("认得出截断: 直连网关的 finish_reason length, hermes 带错误的截断块", () => {
    expect(isTruncatedChunk({ choices: [{ finish_reason: "length" }] })).toBe(true);
    expect(isTruncatedChunk({
      choices: [{ finish_reason: "length" }],
      error: { message: "Response truncated" },
      hermes: { partial: true, error_code: "output_truncated" },
    })).toBe(true);
  });
  it.each([null, {}, { choices: [{ finish_reason: "stop" }] }, { choices: [{ finish_reason: "error" }] },
    { hermes: { failed: true, error_code: "agent_error" } },
  ])("别的不算: %j", (chunk) => {
    expect(isTruncatedChunk(chunk)).toBe(false);
  });
});
