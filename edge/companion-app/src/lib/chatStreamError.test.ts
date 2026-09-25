import { describe, expect, it } from "vitest";
import { chatStreamError } from "./chatStreamError";

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
