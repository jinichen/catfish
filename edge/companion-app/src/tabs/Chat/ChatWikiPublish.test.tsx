// @vitest-environment jsdom
/** 11/08 发布到部门: 扫到敏感内容时对话里出「确认发布」按钮, 点了才带 acknowledge_warnings 真发。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ toolBridgeCallTool: vi.fn() }));
vi.mock("../../lib/tauri_services", () => api);

import { PublishConfirmButton } from "./ChatWikiPublish";

afterEach(cleanup);
beforeEach(() => api.toolBridgeCallTool.mockReset());

const result = JSON.stringify({ ok: false, scan_phase: "warnings",
  confirm_in_ui: { wiki_rel_path: "wiki/entities/客户机房.md", warnings: [{ kind: "pii", advice: "有手机号" }, "内网地址"] } });

describe("PublishConfirmButton", () => {
  it("列出警告, 点确认才调发布 (带 acknowledge_warnings)", async () => {
    api.toolBridgeCallTool.mockResolvedValue({ ok: true, result: { ok: true, namespace: "dept/研发部" } });
    render(<PublishConfirmButton resultText={result} />);
    expect(screen.getByText(/扫到 2 类要留意的内容/).textContent).toContain("有手机号; 内网地址");
    expect(api.toolBridgeCallTool).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("我看过了, 确认发布到部门"));
    await screen.findByText(/已发布到 dept\/研发部/);
    expect(api.toolBridgeCallTool).toHaveBeenCalledWith("catfish_wiki_publish", { wiki_rel_path: "wiki/entities/客户机房.md", acknowledge_warnings: true });
  });

  it("发失败说原因, 按钮还在", async () => {
    api.toolBridgeCallTool.mockResolvedValue({ ok: true, result: { ok: false, error: "没登录" } });
    render(<PublishConfirmButton resultText={result} />);
    fireEvent.click(screen.getByText("我看过了, 确认发布到部门"));
    await screen.findByText("没登录");
    await waitFor(() => expect(screen.getByText("我看过了, 确认发布到部门")).toBeTruthy());
  });

  it("没有 confirm_in_ui (发成功 / 别的错) 不显示", () => {
    const { container } = render(<PublishConfirmButton resultText={JSON.stringify({ ok: true, file_id: "f" })} />);
    expect(container.innerHTML).toBe("");
  });
});
