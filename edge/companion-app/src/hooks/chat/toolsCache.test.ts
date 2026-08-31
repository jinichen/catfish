import { beforeEach, describe, expect, it, vi } from "vitest";

const { listTools } = vi.hoisted(() => ({ listTools: vi.fn() }));

vi.mock("../../lib/tauri", () => ({
  toolBridgeListTools: listTools,
}));

import { _clearToolsCache, ensureTools } from "./toolsCache";

describe("toolsCache 只向模型暴露当前终端可用工具", () => {
  beforeEach(() => {
    _clearToolsCache();
    listTools.mockReset();
  });

  it("过滤 available=false，且不把本机诊断字段写入 LLM schema", async () => {
    listTools.mockResolvedValue([
      {
        name: "catfish_today_summary",
        description: "今日摘要",
        input_schema: { type: "object", properties: {} },
        emoji: "📋",
        toolset: "catfish_native",
        available: true,
        supported: true,
        reason_code: null,
      },
      {
        name: "catfish_list_reminders",
        description: "macOS 提醒事项",
        input_schema: { type: "object", properties: {} },
        emoji: "⏰",
        toolset: "catfish_native",
        available: false,
        supported: false,
        reason_code: "unsupported_platform",
      },
    ]);

    const tools = await ensureTools();

    expect(tools.map((tool) => tool.function.name)).toEqual([
      "catfish_today_summary",
    ]);
    expect(JSON.stringify(tools)).not.toContain("reason_code");
    expect(JSON.stringify(tools)).not.toContain("unsupported_platform");
  });
});
