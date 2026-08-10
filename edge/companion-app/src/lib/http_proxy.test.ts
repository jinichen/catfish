/** http_proxy 超时分档 (8/10).
 *
 * 早安页「advisor 综合判断暂不可用」查了一整天, 最后是 Companion console
 * 给出的原话定位到的:
 *
 *     [advisor] LLM 调用挂: "error sending request for url
 *      (http://localhost:8642/v1/chat/completions?...) ← operation timed out"
 *
 * **超时在 Companion → hermes 这一跳**, 不在 gateway → 上游。当天在 gateway
 * 那层查了很久 (压缩 / thinking / system 归一 / preflight) 全都不是。
 *
 * 一条链四个超时, 只有最小的说了算 —— 而最小的那个 (30 秒) 是**写死的**,
 * 没有任何 caller 知道自己被限了 30 秒。
 */
import { describe, expect, it } from "vitest";

import { __isLlmCallForTest as isLlmCall } from "./http_proxy";

describe("超时分档: LLM 调用要长, 其它保持短", () => {
  it("★ 今天现场那条 URL 必须走长超时", () => {
    expect(isLlmCall(
      "http://localhost:8642/v1/chat/completions" +
      "?catfish_source=companion-advisor&catfish_skip_identity=1&catfish_internal=1",
    )).toBe(true);
  });

  it.each([
    ["http://127.0.0.1:8999/v1/chat/completions", "直连网关"],
    ["http://localhost:8642/v1/chat/completions", "无 query"],
    ["http://127.0.0.1:8999/v1/responses", "responses 协议"],
  ])("LLM 调用 %s (%s)", (url) => expect(isLlmCall(url)).toBe(true));

  it.each([
    ["http://127.0.0.1:8999/v1/catalog", "模型列表"],
    ["http://127.0.0.1:8999/api/v1/models", "models"],
    ["http://127.0.0.1:8999/healthz", "探活"],
    ["http://127.0.0.1:8999/api/advisory/feed.json", "公告"],
    ["http://localhost:8642/v1/models/catfish-private-vision", "单模型详情"],
  ])("普通请求保持 30 秒 %s (%s)", (url) => expect(isLlmCall(url)).toBe(false));

  it("\\b 边界: 前缀相同但不是同一个端点, 不能误判", () => {
    // 放宽超时的代价是"真挂了要等 10 分钟", 所以宁可漏判不可错判
    expect(isLlmCall("http://x/v1/chat/completions_fake")).toBe(false);
    expect(isLlmCall("http://x/v1/responsesX")).toBe(false);
  });
});

describe("超时链自检", () => {
  it("当前配置没有问题", async () => {
    const { checkTimeoutChain } = await import("./timeouts");
    expect(checkTimeoutChain()).toEqual([]);
  });

  it("LLM 超时必须大于普通请求超时", async () => {
    const t = await import("./timeouts");
    // 8/10 事故: LLM 调用被当普通请求限了 30 秒。分档失效必须能查出来。
    expect(t.LLM_TRANSPORT_TIMEOUT_MS).toBeGreaterThan(t.DEFAULT_TRANSPORT_TIMEOUT_MS);
  });

  it("race sentinel 不能小于传输层 —— 否则传输层那个数没机会生效", async () => {
    const t = await import("./timeouts");
    expect(t.LLM_RACE_TIMEOUT_MS).toBeGreaterThanOrEqual(t.LLM_TRANSPORT_TIMEOUT_MS);
  });

  it("界面文案的分钟数跟实际值同源", async () => {
    // 8/8 踩过: 文案硬编码 ">5min" 而实际 600s, 界面骗了员工一倍时间
    const t = await import("./timeouts");
    expect(t.LLM_TIMEOUT_MINUTES).toBe(Math.round(t.LLM_RACE_TIMEOUT_MS / 60_000));
  });
});
