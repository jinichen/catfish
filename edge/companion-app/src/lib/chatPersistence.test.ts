import { describe, expect, it, vi } from "vitest";
import {
  createFirstTransportHandler,
  shouldPersistUserLocally,
} from "./chatPersistence";

describe("chat user message persistence", () => {
  it("Hermes 是唯一 writer，Companion 不重复写 user message", () => {
    expect(shouldPersistUserLocally("hermes")).toBe(false);
  });

  it("直连 gateway 时由 Companion 补写 user message", () => {
    expect(shouldPersistUserLocally("gateway")).toBe(true);
  });

  it("自动 retry 重复解析通道也只触发一次持久化决策", () => {
    const onFirst = vi.fn();
    const resolve = createFirstTransportHandler(onFirst);

    expect(resolve("gateway")).toBe(true);
    expect(resolve("gateway")).toBe(false);
    expect(resolve("hermes")).toBe(false);
    expect(onFirst).toHaveBeenCalledOnce();
    expect(onFirst).toHaveBeenCalledWith("gateway");
  });
});
