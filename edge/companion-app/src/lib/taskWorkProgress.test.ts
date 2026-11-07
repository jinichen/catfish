import { describe, expect, it } from "vitest";
import { toolName, workText } from "./taskWorkProgress";

describe("workText (11/07 小鲶后台进度)", () => {
  it("还没开始 / 做完 / 刚开始", () => {
    expect(workText(null)).toBe("小鲶准备开工");
    expect(workText({ running: false, steps: 5, tool: "x", detail: "", updated_at: null })).toBe("小鲶做完了, 在存结果");
    expect(workText({ running: true, steps: 0, tool: "", detail: "", updated_at: null })).toBe("小鲶在看任务");
  });
  it("第几步 + 中文工具名 + 参数摘要 (长的截断)", () => {
    expect(workText({ running: true, steps: 3, tool: "mcp__catfish_tools__catfish_email_search", detail: "拜访记录", updated_at: null }))
      .toBe("小鲶在做 · 第 3 步 搜邮件「拜访记录」");
    expect(workText({ running: true, steps: 1, tool: "read_file", detail: "/Users/x/一个很长很长很长很长很长的文件名.docx", updated_at: null }))
      .toMatch(/^小鲶在做 · 第 1 步 .+「.{20}…」$/);
  });
  it("工具名: hermes 自带 / 鲶鱼 mcp 两种前缀 / 认不出的", () => {
    expect(toolName("mcp_catfish_local_search_local_search")).toBe("搜本机文件");
    expect(toolName("mcp_catfish_tools_catfish_wiki_search")).toBe("查知识库");
    expect(toolName("catfish_form")).toBe("存结果");
    expect(toolName("some_new_tool")).toBe("用 some_new_tool");
  });
});
