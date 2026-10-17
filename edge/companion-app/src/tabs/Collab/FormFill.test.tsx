// @vitest-environment jsdom
/** 收件人填表 (10/17): 提交才发、问题当场说、「让小鲶先填」交给工作台且小鲶不能提交、不填要原因。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  formGet: vi.fn(),
  formDraft: vi.fn(() => Promise.resolve({ filled: 1 })),
  formSubmit: vi.fn(),
  formDecline: vi.fn(() => Promise.resolve({ ok: true })),
};
vi.mock("../../lib/tauri_forms", () => ({
  formGet: (...a: unknown[]) => api.formGet(...a),
  formDraft: (...a: unknown[]) => api.formDraft(...a),
  formSubmit: (...a: unknown[]) => api.formSubmit(...a),
  formDecline: (...a: unknown[]) => api.formDecline(...a),
}));
vi.mock("../../lib/formsStore", () => ({ refreshForms: vi.fn(() => Promise.resolve()) }));

import FormFill from "./FormFill";
import { prefillPrompt, summaryPrompt } from "../../lib/formPrompts";
import { useUIStore } from "../../store/ui";

const REC = {
  form_id: "f_20261017_ab12cd34", owner: "zhou@example.com", status: "open", received_at: "2026-10-17T08:00:00+08:00",
  form: {
    title: "10 月安全检查", note: "每个项目部一行", deadline: "2099-10-24",
    fields: [
      { key: "f1", label: "项目部", type: "text", required: true },
      { key: "f2", label: "检查次数", type: "number", required: true },
      { key: "f3", label: "最高隐患等级", type: "choice", options: ["无", "一般", "较大"] },
    ],
  },
  draft: { values: { f1: "一号项目部" }, sources: { f1: "邮件《10 月检查安排》" }, updated_at: "x" },
};

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockClear());
  api.formGet.mockResolvedValue({ role: "recipient", record: REC, state: "草稿" });
});
afterEach(cleanup);

async function open() {
  const onClose = vi.fn();
  render(<FormFill formId={REC.form_id} onClose={onClose} />);
  await screen.findByText("《10 月安全检查》");
  return onClose;
}

describe("FormFill", () => {
  it("带出草稿和小鲶找到的出处", async () => {
    await open();
    expect((screen.getByLabelText(/项目部/) as HTMLInputElement).value).toBe("一号项目部");
    expect(screen.getByText(/邮件《10 月检查安排》/)).toBeTruthy();
  });

  it("提交有问题当场说, 改对了才算交上", async () => {
    await open();
    api.formSubmit.mockResolvedValueOnce({ ok: false, problems: ["「检查次数」必填"] });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "提交" })); });
    expect(screen.getByText("「检查次数」必填")).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/检查次数/), { target: { value: "3" } });
    api.formSubmit.mockResolvedValueOnce({ ok: true, late: false, submitted_at: "x" });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "提交" })); });
    expect(api.formSubmit).toHaveBeenLastCalledWith(REC.form_id, { f1: "一号项目部", f2: "3" });
    expect(screen.getByText("交上去了")).toBeTruthy();
  });

  it("让小鲶先填: 先存草稿, 交给工作台, 关掉弹层", async () => {
    const start = vi.fn();
    useUIStore.setState({ startEmailChat: start });
    const onClose = await open();
    fireEvent.click(screen.getByRole("button", { name: /让小鲶先填/ }));
    expect(api.formDraft).toHaveBeenCalled();
    expect(start).toHaveBeenCalledWith(`form-prefill:${REC.form_id}`, expect.stringContaining(REC.form_id));
    expect(onClose).toHaveBeenCalled();
  });

  it("不填要写原因才发", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "不填" }));
    const send = screen.getByRole("button", { name: "告诉发起人" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/为什么不填/), { target: { value: "没有在建项目" } });
    await act(async () => { fireEvent.click(send); });
    await waitFor(() => expect(api.formDecline).toHaveBeenCalledWith(REC.form_id, "没有在建项目"));
  });

  it("结束收集的表只读, 没有提交按钮", async () => {
    api.formGet.mockResolvedValue({ role: "recipient", record: { ...REC, status: "closed" }, state: "已结束" });
    await open();
    expect(screen.queryByRole("button", { name: "提交" })).toBeNull();
    expect((screen.getByLabelText(/项目部/) as HTMLInputElement).disabled).toBe(true);
  });
});

describe("formPrompts", () => {
  it("预填: 只让存草稿、不许提交、找不到留空", () => {
    const p = prefillPrompt(REC as never);
    expect(p).toContain("action=draft");
    expect(p).toContain("不要替我提交");
    expect(p).toContain("不要编");
  });
  it("汇总: 读 responses, 不编数字", () => {
    const p = summaryPrompt({ form_id: "f_20261017_ab12cd34", title: "10 月安全检查" } as never);
    expect(p).toContain("action=responses");
    expect(p).toContain("不要编");
  });
});
