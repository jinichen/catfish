/**
 * hermes 活动文案中文化的判据。
 *
 * 重点是**认不出来的时候干什么** —— 策略是"原样显示 + warn 一次"。
 * P28 (插件侧翻微信审批提示) 踩过的教训: hermes 改了原文, 老 pattern silent
 * miss, 英文全条泄漏。silent 才是最糟的, 丑一行没关系。
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  TOOL_ZH,
  _resetWarnedForTest,
  translateActivityDescription,
  translateTool,
} from "./agentActivityI18n";

afterEach(() => {
  _resetWarnedForTest();
  vi.restoreAllMocks();
});

describe("translateTool", () => {
  it("认得的翻成中文", () => {
    expect(translateTool("read_file")).toBe("读文件");
    expect(translateTool("web_search")).toBe("联网搜索");
    expect(translateTool("execute_code")).toBe("跑代码");
  });

  it("认不得的原样返回，并 warn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(translateTool("brand_new_tool")).toBe("brand_new_tool");
    expect(warn).toHaveBeenCalledOnce();
    expect(String(warn.mock.calls[0][0])).toContain("brand_new_tool");
  });

  it("空值不 warn —— 那不是没认出来，是本来就没有", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(translateTool(null)).toBe("");
    expect(translateTool("")).toBe("");
    expect(translateTool("   ")).toBe("");
    expect(warn).not.toHaveBeenCalled();
  });
});

describe("translateActivityDescription", () => {
  it.each([
    ["API error recovery (attempt 3/3)", "上游报错, 正在重试 (第 3/3 次)"],
    ["starting API call #7", "发起第 7 次模型调用"],
    ["API call #7 completed", "第 7 次模型调用完成"],
    ["tool results posted, continuing iteration #2", "工具结果已回填, 继续第 2 轮"],
    ["executing tool: read_file", "正在执行 读文件"],
    ["receiving stream response", "正在接收模型输出"],
    ["waiting for non-streaming API response", "等模型返回 (非流式)"],
    ["context compression in progress", "正在压缩上下文"],
  ])("翻得对: %s", (raw, want) => {
    expect(translateActivityDescription(raw)).toBe(want);
  });

  it("并发工具那条把每个工具名都翻掉", () => {
    expect(
      translateActivityDescription("executing 2 tools concurrently: read_file, web_search"),
    ).toBe("并发执行 2 个工具: 读文件、联网搜索");
  });

  it("tool completed 带耗时", () => {
    expect(translateActivityDescription("tool completed: read_file (1.4s)")).toBe(
      "读文件 完成 (1.4 秒)",
    );
  });

  it("认不得的原样返回，并 warn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const raw = "some brand new hermes activity";
    expect(translateActivityDescription(raw)).toBe(raw);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("同一个串只 warn 一次 —— 探针 3 秒一轮，不去重会刷爆控制台", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (let i = 0; i < 20; i++) translateActivityDescription("unknown thing");
    expect(warn).toHaveBeenCalledOnce();
  });

  it("空描述不 warn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(translateActivityDescription(null)).toBe("");
    expect(translateActivityDescription("")).toBe("");
    expect(warn).not.toHaveBeenCalled();
  });

  it("规则顺序: 具体的要赢过宽泛的", () => {
    // "waiting for provider response" 是前缀规则, 不能把上面那条精确的吃掉
    expect(translateActivityDescription("waiting for non-streaming API response")).toBe(
      "等模型返回 (非流式)",
    );
    expect(translateActivityDescription("waiting for provider response (streaming)")).toBe(
      "等模型返回",
    );
  });
});

describe("工具表本身", () => {
  it("值都是中文，没有漏翻的占位", () => {
    for (const [en, zh] of Object.entries(TOOL_ZH)) {
      expect(zh, `${en} 的译名是空的`).toBeTruthy();
      expect(zh, `${en} 没翻`).not.toBe(en);
    }
  });

  it("覆盖网关实际下发的那批工具", () => {
    // 取自网关日志 tools_sanitizer 打印的 33 个里最常出现的一批
    for (const t of ["read_file", "write_file", "execute_code", "web_search", "terminal", "todo"]) {
      expect(TOOL_ZH[t], `${t} 不在表里`).toBeTruthy();
    }
  });
});
