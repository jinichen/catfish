/** chat.ts 拆分后的守卫 (8/15)。
 *
 * # 病历
 *
 * chat.ts 934 行, 过了 CLAUDE.md §1 的 800 红线。**这是第二次拆同一个文件** ——
 * 5/20 已经把 wire format 抽到 chatWire.ts (805 → 550), 三个月又长回 934。
 *
 * 拆成:
 *     chat_types.ts    117  5 个类型声明 (编译后消失, 零运行时风险)
 *     chatRequest.ts   203  拼 URL / body / agentHeaders
 *     chat.ts          737  streamChat 主体 + computeIdleTimeoutMs
 *
 * # 这个文件钉两件今天真出过事的东西
 *
 * ## 1. `?catfish_source=companion-chat` 不能丢
 *
 * 8/15 早上百炼周配额 83 分钟烧空。查网关账本时最大的一块是「(无标记)」——
 * 10:30 之后 94% 的 token 落在那一栏, 因为员工聊天从来不带 source。
 * "钱花在哪"这个问题, 最大的一块答不上来。
 *
 * 当天给聊天加了这个 query 标记。它现在住在 chatRequest.ts 里, 而**它同时是
 * 一个跨仓约定**: plugin_memory_gate.py 的 P42 记忆闸判据是"有 source 就跳
 * 记忆", 所以加标记的同时必须把 companion-chat 加进那边的
 * INTERACTIVE_SOURCES 白名单, 否则**员工聊天从此不进记忆**。
 *
 * 按那个文件自己 docstring 的说法, 这种失效"现场根本看不出来"。
 *
 * 所以这条守卫不只是"别删标记", 是"删之前先想清楚另一头"。
 *
 * ## 2. markModelSent() 的位置
 *
 * `prepareChatRequest` 是个名字叫"拼请求"却会写 store 的函数。看着像该清理掉
 * 的副作用, 但**位置是有意的**: 原注释写着「放在最早 —— 即使 fetch 抛错,
 * 下次再发也能继续追踪」。
 *
 * 它服务的是 X-Catfish-Prev-Model 那条 soft handoff (BL-GATEWAY-SOFT-HANDOFF,
 * 5/18): 切模型后下一次请求要告诉网关上次用的是谁, 网关据此把历史 tool_calls
 * 转成 inline 文本, 防新模型不支持 tools 时炸。
 *
 * 挪到 fetch 之后 = "发失败的那次不算用过" = 下一次的对比基准就错了。
 * 而错了的表现是**偶发的 streaming error**, 不是稳定复现的 bug。
 *
 * # ⚠ streamChat 那 752 行仍然零覆盖
 *
 * chat.test.ts 只测 computeIdleTimeoutMs。这个文件也没有覆盖 streamChat ——
 * 它测的是拆出来的那一小块。整条流式链路该补测试, 那是另一件事的排期。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

// 三个 store 都要打桩 —— prepareChatRequest 会读它们, 其中 chat store 还会被写。
const markModelSent = vi.fn();
let agentState = { loaded: false, name: "", personality: "" };
let teachingOn = false;
let prevSentModel: string | null = null;

vi.mock("../store/agent", () => ({
  useAgentStore: { getState: () => agentState },
}));
vi.mock("../store/teaching", () => ({
  useTeachingStore: { getState: () => ({ on: teachingOn }) },
}));
vi.mock("../store/chat", () => ({
  useChatStore: { getState: () => ({ prevSentModel, markModelSent }) },
}));
vi.mock("./env", () => ({ config: { gatewayUrl: "http://gw:8999" } }));
const EMPTY_BUNDLE = {
  soul: "", soul_customer: "", soul_browser: "", soul_execute_code: "",
  user_memory: "", memory_dir: "",
};
let bundle = { ...EMPTY_BUNDLE };
vi.mock("./tauri", () => ({
  fetchIdentityBundle: vi.fn(async () => bundle),
}));
vi.mock("./chatWire", () => ({ toWire: (m: unknown) => m }));

import { prepareChatRequest } from "./chatRequest";

const BASE = {
  useHermes: false,
  hermesCfg: null,
  model: "catfish-public-deepseek-flash",
  messages: [] as never[],
};

describe("prepareChatRequest", () => {
  beforeEach(() => {
    markModelSent.mockClear();
    agentState = { loaded: false, name: "", personality: "" };
    teachingOn = false;
    prevSentModel = null;
    bundle = { ...EMPTY_BUNDLE };
  });

  it("★ 网关路径的 URL 带 companion-chat 来源标记", async () => {
    const r = await prepareChatRequest(BASE);
    expect(r.url).toBe(
      "http://gw:8999/v1/chat/completions?catfish_source=companion-chat",
    );
  });

  it("★ hermes 路径也带同一个标记", async () => {
    const r = await prepareChatRequest({
      ...BASE,
      useHermes: true,
      hermesCfg: { enabled: true, url: "http://127.0.0.1:8642", has_key: true },
    });
    expect(r.url).toBe(
      "http://127.0.0.1:8642/v1/chat/completions?catfish_source=companion-chat",
    );
  });

  it("★★★ markModelSent 一定被调 —— 即使这次请求后面会失败", async () => {
    await prepareChatRequest(BASE);
    expect(markModelSent, "markModelSent 没被调 —— X-Catfish-Prev-Model 的追踪断了")
      .toHaveBeenCalledTimes(1);
  });

  it("切了模型才发 X-Catfish-Prev-Model", async () => {
    prevSentModel = "catfish-public-gemini-pro";
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Prev-Model"]).toBe("catfish-public-gemini-pro");
  });

  it("同一个模型续聊不发那个 header (网关看相同也是 no-op, 省 header)", async () => {
    prevSentModel = BASE.model;
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Prev-Model"]).toBeUndefined();
  });

  it("从没发过消息也不发 (prevSentModel === null, 跟老行为一致)", async () => {
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Prev-Model"]).toBeUndefined();
  });

  it("默认名 / 默认人设不发 header —— 只有员工改过才发", async () => {
    agentState = { loaded: true, name: "小鲶", personality: "gentle" };
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Agent-Name"]).toBeUndefined();
    expect(r.agentHeaders["X-Catfish-Agent-Personality"]).toBeUndefined();
  });

  it("员工改过名字和人设才发 (BL-E11 命名权)", async () => {
    agentState = { loaded: true, name: "小福", personality: "sharp" };
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Agent-Name"]).toBe("小福");
    expect(r.agentHeaders["X-Catfish-Agent-Personality"]).toBe("sharp");
  });

  it("store 还没 loaded 时一个 agent header 都不发", async () => {
    agentState = { loaded: false, name: "小福", personality: "sharp" };
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Agent-Name"]).toBeUndefined();
  });

  it("教学模式开着才带 X-Catfish-Teaching-Mode", async () => {
    teachingOn = true;
    const r = await prepareChatRequest(BASE);
    expect(r.agentHeaders["X-Catfish-Teaching-Mode"]).toBe("1");
  });

  it("hermes 路径不传 tools —— hermes 自己管 tool calling", async () => {
    const tools = [{ type: "function" as const, function: { name: "t", description: "", parameters: {} } }];
    const r = await prepareChatRequest({
      ...BASE, useHermes: true, tools,
      hermesCfg: { enabled: true, url: "http://h", has_key: true },
    });
    expect(r.body.tools).toBeUndefined();
  });

  it("网关路径才传 tools", async () => {
    const tools = [{ type: "function" as const, function: { name: "t", description: "", parameters: {} } }];
    const r = await prepareChatRequest({ ...BASE, tools });
    expect(r.body.tools).toEqual(tools);
  });

  it("身份 bundle 全空时不挂 —— 让网关走 fs fallback", async () => {
    const r = await prepareChatRequest(BASE);
    expect(r.body._catfish_identity_bundle).toBeUndefined();
  });

  it("bundle 有内容才挂 (BL-IDENTITY-INJECT-DECOUPLE)", async () => {
    bundle = { ...bundle, soul: "我是鲶鱼" };
    const r = await prepareChatRequest(BASE);
    expect(r.body._catfish_identity_bundle).toBeTruthy();
  });

  it("★ hermes 路径不挂 bundle —— 它自己有 identity 注入层", async () => {
    bundle = { ...bundle, soul: "我是鲶鱼" };
    const r = await prepareChatRequest({
      ...BASE,
      useHermes: true,
      hermesCfg: { enabled: true, url: "http://h", has_key: true },
    });
    expect(
      r.body._catfish_identity_bundle,
      "hermes 路径挂了 bundle —— 会跟 hermes 自己的注入层撞车, 人格设定发两遍",
    ).toBeUndefined();
  });

  it("body 里 stream 恒为 true, model 用 effectiveModel", async () => {
    const r = await prepareChatRequest(BASE);
    expect(r.body.stream).toBe(true);
    expect(r.body.model).toBe(BASE.model);
    expect(r.effectiveModel).toBe(BASE.model);
  });
});
