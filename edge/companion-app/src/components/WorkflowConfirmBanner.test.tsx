// @vitest-environment jsdom
/** 工作流对外步骤确认 (10/17): 卡上写清要发什么; 确认 / 不发都走界面; 每次运行只通知一次。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = { pending: vi.fn(), confirm: vi.fn() };
const notify = vi.fn(() => Promise.resolve());
vi.mock("../lib/tauri_workflows", () => ({
  workflowPending: () => api.pending(),
  workflowConfirm: (...a: unknown[]) => api.confirm(...a),
}));
vi.mock("../lib/tauri_app", () => ({ sendNotification: (t: string, b: string) => notify(t, b) }));

import WorkflowConfirmBanner from "./WorkflowConfirmBanner";
import { __resetWorkflowStore, refreshPending } from "../lib/workflowStore";
import { sourcesBefore } from "../tabs/Dashboard/WorkflowEditor";

const RUN = {
  run_id: "r_20261017120000_abcdef", skill_path: "personal/qual", title: "personal/qual", status: "waiting_confirm",
  created_at: "2026-10-17T12:00:00+08:00", updated_at: "", failed_step: null, error: null,
  pending: { index: 4, step: "push", label: "推送到我的微信", preview: { text: "建好任务了: 整理资质附件 1 份" }, for_each_count: null },
};

beforeEach(() => {
  __resetWorkflowStore();
  api.pending.mockReset();
  api.confirm.mockReset();
  notify.mockClear();
});
afterEach(cleanup);

describe("WorkflowConfirmBanner", () => {
  it("写清要发什么, 点确认发送才继续", async () => {
    api.pending.mockResolvedValue({ runs: [RUN] });
    api.confirm.mockResolvedValue({ ok: true, status: "done", summary: "跑完了" });
    render(<WorkflowConfirmBanner />);
    await screen.findByText(/第 5 步「推送到我的微信」要对外发送/);
    expect(screen.getByText("建好任务了: 整理资质附件 1 份")).toBeTruthy();
    api.pending.mockResolvedValue({ runs: [] });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "确认发送" })); });
    expect(api.confirm).toHaveBeenCalledWith(RUN.run_id, true);
    await waitFor(() => expect(screen.queryByText(/要对外发送/)).toBeNull());
  });

  it("不发 = approve false", async () => {
    api.pending.mockResolvedValue({ runs: [RUN] });
    api.confirm.mockResolvedValue({ ok: true, status: "declined" });
    render(<WorkflowConfirmBanner />);
    await screen.findByText(/要对外发送/);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "不发" })); });
    expect(api.confirm).toHaveBeenCalledWith(RUN.run_id, false);
  });

  it("同一次运行只发一次系统通知", async () => {
    api.pending.mockResolvedValue({ runs: [RUN] });
    await refreshPending();
    await refreshPending();
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][1]).toContain("推送到我的微信");
  });

  it("没有等确认的就不显示", async () => {
    api.pending.mockResolvedValue({ runs: [] });
    const { container } = render(<WorkflowConfirmBanner />);
    await act(async () => {});
    expect(container.textContent).toBe("");
  });
});

describe("WorkflowEditor sourcesBefore", () => {
  it("只列前面步骤声明过的输出 + 参数; for_each 的步骤多一个 item", () => {
    const cat = { actions: [{ name: "email_search", label: "搜邮件", level: "read", group: "查", params: [], required: [], outputs: ["matches", "count"] }], skills: [] } as never;
    const wf = {
      kind: "workflow", params: { keyword: { label: "关键词" } },
      steps: [{ id: "mails", action: "email_search" }, { id: "pick", xiaonian: "x", returns: { ids: "list" } },
              { id: "files", action: "email_search", for_each: "${steps.pick.ids}" }],
    } as never;
    const at2 = sourcesBefore(wf, 2, cat).map((s) => s.value);
    expect(at2).toContain("${params.keyword}");
    expect(at2).toContain("${steps.mails.matches}");
    expect(at2).toContain("${steps.pick.ids}");
    expect(at2).toContain("${item}");
    expect(at2.some((v) => v.includes("steps.files"))).toBe(false);
    expect(sourcesBefore(wf, 0, cat).map((s) => s.value)).toEqual(["${params.keyword}"]);
  });
});
