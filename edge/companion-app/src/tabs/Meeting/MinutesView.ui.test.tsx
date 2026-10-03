// @vitest-environment jsdom
/** 纪要卡片 / 模版弹窗的界面 (10/3 UI 调整): 后端调用全换成假的, 只验渲染和交互。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  minutes: null as unknown,
  templates: [] as unknown[],
  generate: vi.fn(async () => ({})),
}));

vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingMinutes: vi.fn(async () => api.minutes),
  meetingTemplatesList: vi.fn(async () => api.templates),
  meetingMinutesGenerate: api.generate,
  meetingMinutesOpenFile: vi.fn(async () => undefined),
}));
vi.mock("../../lib/tauri_services", () => ({ toolBridgeCallTool: vi.fn(async () => ({ ok: true, result: { ok: true } })) }));

import MinutesView from "./MinutesView";

const meta = { id: "mtg_1", title: "周会", created_at: "2026-10-01T10:00:00", attendees: 2 } as never;
const base = { meeting_id: "mtg_1", summary: "讨论了二期。", decisions: [], action_items: [], open_questions: [] };

async function mount() {
  await act(async () => { render(<MinutesView meta={meta} />); });
}

beforeEach(() => {
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) },
  });
  api.minutes = null;
  api.templates = [];
  api.generate.mockClear();
});
afterEach(cleanup);

describe("纪要卡片", () => {
  it("没生成: 头上就有模版选择 + 生成纪要, 生成用选中的模版", async () => {
    api.templates = [{ id: "tpl_w", name: "公司纪要表", body: "", updated_at: "", kind: "docx", slots: [] }];
    await mount();
    const select = screen.getByLabelText("纪要模版") as HTMLSelectElement;
    expect([...select.options].map((o) => o.text)).toEqual(["标准纪要", "Word · 公司纪要表"]);
    fireEvent.change(select, { target: { value: "tpl_w" } });
    await act(async () => { fireEvent.click(screen.getByText("生成纪要")); });
    expect(api.generate).toHaveBeenCalledWith("mtg_1", "tpl_w");
  });

  it("空的几节并成一行; 没待办就不出「加入任务库」", async () => {
    api.minutes = { json: base, markdown: "# 周会", path: "/x" };
    await mount();
    expect(screen.getByText("无决议 · 无待办 · 无待定问题")).toBeTruthy();
    expect(screen.queryByText(/加入任务库/)).toBeNull();
    expect(screen.getByText("重新生成")).toBeTruthy();
    expect(screen.getByText("存进知识库")).toBeTruthy();
  });

  it("有待办: 按钮写明勾了几项", async () => {
    api.minutes = { json: { ...base, action_items: [{ task: "交材料", owner: "张三", due: "" }, { task: "约会", owner: "", due: "" }] }, markdown: "", path: "" };
    await mount();
    expect(screen.getByText("勾选的 2 项待办加入任务库")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    expect(screen.getByText("勾选的 1 项待办加入任务库")).toBeTruthy();
  });

  it("Word 模版生成的: 顶上有打开 Word", async () => {
    api.minutes = { json: { ...base, template: { id: "tpl_w", name: "公司纪要表", kind: "docx" }, output_file: "/m/周会 会议纪要.docx" }, markdown: "", path: "" };
    await mount();
    expect(screen.getByText("打开 Word")).toBeTruthy();
    expect(screen.getByText(/周会 会议纪要\.docx/)).toBeTruthy();
    expect(screen.getByText("按「公司纪要表」")).toBeTruthy();
  });
});

describe("模版弹窗", () => {
  it("弹窗打开 / 切到上传 / 说明默认收起 / Esc 关", async () => {
    await mount();
    fireEvent.click(screen.getByText("管理模版"));
    expect(screen.getByRole("dialog", { name: "纪要模版" })).toBeTruthy();
    expect(screen.getByText("内置 · 摘要 / 决议 / 待办 / 待定问题")).toBeTruthy();
    fireEvent.click(screen.getByText("+ 上传 Word / Excel"));
    expect(screen.getByText("选择 .docx / .xlsx 文件")).toBeTruthy();
    const details = screen.getByText("能认出哪些地方? 认不准怎么办?").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
