// @vitest-environment jsdom
/** 11/3: 客户跟进收进问候行 + 「+ 加客户」。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  add: vi.fn(async (..._args: unknown[]) => ({ ok: true, created: true })),
}));
vi.mock("../../lib/tauri_sales", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_sales")>()),
  salesTemplateGet: vi.fn(async () => ({ name: "默认", stages: [{ name: "线索", cold_days: 14 }, { name: "需求确认", cold_days: 7 }],
    quoted_stage: "报价", reply_wait_days: 1, quote_followup_days: 3 })),
  salesCustomerAdd: api.add,
}));
vi.mock("../../store/ui", () => ({ useUIStore: (sel: (s: unknown) => unknown) => sel({ startEmailChat: vi.fn() }) }));

import CustomerAddForm from "./CustomerAddForm";
import CustomerChip from "./CustomerChip";
import CustomerFollowupStrip from "./CustomerFollowupStrip";
import type { CustomerRadarState } from "./useCustomerRadar";

const radar = (over: Partial<CustomerRadarState>): CustomerRadarState => ({
  cards: [], customers: 1, error: null, load: vi.fn(async () => undefined), setError: vi.fn(), ...over,
});
const card = { uid: "u1", rule: "cold", label: "客户有段时间没动静了", customer: "达华智能", stage: "线索",
  rel_path: "wiki/entities/达华智能.md", quote: "建档后客户还没来过消息", age_days: 22 } as const;

afterEach(cleanup);

describe("客户跟进", () => {
  it("没有要跟的: 问候行一个小标签, 不出整条", () => {
    render(<><CustomerFollowupStrip radar={radar({})} onAdd={() => {}} /><CustomerChip radar={radar({})} onAdd={() => {}} /></>);
    expect(screen.getByText("🤝 1 个客户, 今天没有要跟的 ✓")).toBeTruthy();
    expect(screen.queryByText("🤝 客户跟进")).toBeNull();
  });

  it("还没有客户: 也给「+ 加客户」", () => {
    const onAdd = vi.fn();
    render(<CustomerChip radar={radar({ customers: 0 })} onAdd={onAdd} />);
    expect(screen.getByText("🤝 还没有客户")).toBeTruthy();
    fireEvent.click(screen.getByText("+ 加客户"));
    expect(onAdd).toHaveBeenCalled();
  });

  it("有要跟的: 出整条 (带 + 加客户), 小标签不重复", () => {
    const r = radar({ cards: [card] as never });
    render(<><CustomerFollowupStrip radar={r} onAdd={() => {}} /><CustomerChip radar={r} onAdd={() => {}} /></>);
    expect(screen.getByText("1 件要跟 · 1 个客户")).toBeTruthy();
    expect(screen.getAllByText("+ 加客户")).toHaveLength(1);
  });

  it("雷达出错 (tool-bridge 没起来): 都不显示", () => {
    const r = radar({ error: "down" });
    const { container } = render(<><CustomerFollowupStrip radar={r} onAdd={() => {}} /><CustomerChip radar={r} onAdd={() => {}} /></>);
    expect(container.textContent).toBe("");
  });

  it("加客户表单: 阶段取模板, 联系人选填, 同名报错原样显示", async () => {
    const onDone = vi.fn();
    await act(async () => { render(<CustomerAddForm onDone={onDone} onCancel={() => {}} />); });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(screen.getByText("先填客户公司名")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("客户公司名"), { target: { value: " 华为云 " } });
    fireEvent.change(screen.getByLabelText("阶段"), { target: { value: "需求确认" } });
    fireEvent.change(screen.getByLabelText("联系人"), { target: { value: "李工" } });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(api.add).toHaveBeenCalledWith("华为云", "需求确认", [{ name: "李工", email: "", wechat: "" }]);
    expect(onDone).toHaveBeenCalledWith("华为云");
    api.add.mockRejectedValueOnce("「华为云」已经是客户了");
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(screen.getByText("「华为云」已经是客户了")).toBeTruthy();
  });
});
