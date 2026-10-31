// @vitest-environment jsdom
// 10/31: 接入 MCP 对话框 —— 本机程序 / 远程网址两种, 环境变量和请求头的值按密钥交出去。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddMcpDialog } from "./SkillsMcpDialogs";

afterEach(cleanup);

const type = (placeholder: string | RegExp, value: string) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value } });

describe("AddMcpDialog", () => {
  it("本机程序: 命令 + 参数 + 环境变量 (值按密钥交出去)", () => {
    const onSubmit = vi.fn();
    render(<AddMcpDialog onClose={() => {}} onSubmit={onSubmit} />);
    type(/不能用 catfish/, "amap");
    type("npx", "npx");
    type("-y 12306-mcp@0.3.10", "-y @amap/amap-maps-mcp-server@1.0.0");
    fireEvent.click(screen.getByText("+ 添加"));
    type("如 AMAP_MAPS_API_KEY", "AMAP_MAPS_API_KEY");
    type("值 (按密钥保存)", "k123");
    fireEvent.click(screen.getByText("接入"));
    expect(onSubmit).toHaveBeenCalledWith({
      name: "amap",
      command: "npx",
      args: ["-y", "@amap/amap-maps-mcp-server@1.0.0"],
      env: [["AMAP_MAPS_API_KEY", "k123"]],
    });
  });

  it("远程网址: 网址必须 http(s), 请求头带上", () => {
    const onSubmit = vi.fn();
    render(<AddMcpDialog onClose={() => {}} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByText("远程网址"));
    type(/不能用 catfish/, "deepwiki");
    type("https://example.com/mcp", "mcp.deepwiki.com/mcp");
    expect((screen.getByText("接入") as HTMLButtonElement).disabled).toBe(true);
    type("https://example.com/mcp", "https://mcp.deepwiki.com/mcp");
    fireEvent.click(screen.getByText("+ 添加"));
    type("如 Authorization", "Authorization");
    type("值 (按密钥保存)", "Bearer x");
    fireEvent.click(screen.getByText("接入"));
    expect(onSubmit).toHaveBeenCalledWith({
      name: "deepwiki",
      url: "https://mcp.deepwiki.com/mcp",
      headers: [["Authorization", "Bearer x"]],
    });
  });

  it("远程网址 + 网页登录授权: 带上 oauth", () => {
    const onSubmit = vi.fn();
    render(<AddMcpDialog onClose={() => {}} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByText("远程网址"));
    type(/不能用 catfish/, "notion");
    type("https://example.com/mcp", "https://mcp.notion.com/mcp");
    fireEvent.click(screen.getByLabelText(/需要网页登录授权/));
    fireEvent.click(screen.getByText("接入"));
    expect(onSubmit).toHaveBeenCalledWith({ name: "notion", url: "https://mcp.notion.com/mcp", headers: [], oauth: true });
  });
});
