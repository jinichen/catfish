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

// ── 304 / 204 这类不许带 body 的状态 (8/14) ──────────────────

describe("null body status 不能带 body 构造 Response", () => {
  /** 病历
   *
   * 8/14 macOS 实跑, 每次启动都刷:
   *
   *     [advisory] fetch feed exception:
   *     TypeError: Response cannot have a body with the given status.
   *
   * advisory.ts 用 If-None-Match 做条件请求, 服务端正常返 304。而 httpProxy
   * 当时是 `new Response(resp.body, {status: 304})` —— body 是字符串, 哪怕空串
   * 也算 non-null, 构造器当场抛。
   *
   * 后果不是"少刷新一次": 304 的含义本来是"你手上的缓存还新鲜", 现在变成
   * "拉取失败返 null", ETag fast path 整条废掉 —— 而日志里只有一行看着像
   * 网络问题的 TypeError。
   */
  it.each([204, 205, 304])("★★★ status=%i 时 body 必须是 null", (status) => {
    // 直接钉 Response 构造器的行为: 这就是当时抛的地方
    expect(() => new Response("", { status })).toThrow();
    expect(() => new Response("x", { status })).toThrow();
    expect(new Response(null, { status }).status).toBe(status);
  });

  it.each([101, 103])("★ 1xx (%i) 连 null body 都构造不出来 —— 所以不在名单里", (status) => {
    // 我第一版把 101/103 也放进 NULL_BODY_STATUS 了, 跑测试才发现:
    // init.status 必须在 200..599, 传 null 一样 RangeError。
    // 对 1xx 根本没有"正确的返回值"可给, 而它们也到不了这条代理
    // (hyper 把 1xx 当中间响应吃掉; 101 只在主动 upgrade 时才是终态)。
    expect(() => new Response(null, { status })).toThrow();
  });

  it("★★ 判据是状态码, 不是 body 空不空", () => {
    // 只判空串的话, 服务端在 304 上回了任何字节就又炸了 —— 而"304 带 body"
    // 完全合法 (代理 / CDN 塞点东西很常见)。
    expect(() => new Response("", { status: 304 })).toThrow();
  });

  it("200 带 body 照常", () => {
    expect(new Response("hi", { status: 200 }).status).toBe(200);
  });
});
