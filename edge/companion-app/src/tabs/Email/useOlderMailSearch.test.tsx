/**
 * @vitest-environment jsdom
 */
/** 11/19 鸿波「搜索框也改成能搜到更早的邮件」: 列表 (≤500 封) 以外的, 输入停一下去邮箱里搜。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ search: vi.fn() }));
vi.mock("../../lib/tauri", () => ({ emailSearch: api.search }));
vi.mock("./components/ListItem", () => ({
  default: ({ item, onClick }: { item: { subject: string }; onClick: () => void }) => <li onClick={onClick}>{item.subject}</li>,
}));

import { useState } from "react";
import MailSearchBox from "./components/MailSearchBox";
import OlderMailResults from "./components/OlderMailResults";
import { OLDER_SEARCH_DEBOUNCE_MS, useOlderMailSearch } from "./useOlderMailSearch";

const inList = { id: "apple_mail|C|3754", subject: "补充医疗保险理赔", date: "2026-11-17T07:32:54+00:00" };
const old1 = { id: "apple_mail|C|1199", subject: "关于开展期中固定资产盘点工作的通知", date: "2026-06-16T10:02:24+00:00" };

function Harness({ onSelect }: { onSelect: (id: string) => void }) {
  const [q, setQ] = useState("");
  const r = useOlderMailSearch(q, "Inbox", [inList as never]);
  return (
    <ul>
      <MailSearchBox value={q} onChange={setQ} shown={0} total={1} />
      <OlderMailResults result={r} search={q} selectedId={null} showRecipients={false} onSelect={(m) => onSelect(m.id)} />
    </ul>
  );
}

beforeEach(() => { vi.useFakeTimers(); api.search.mockReset(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("搜更早的邮件", () => {
  it("停一下才去邮箱里搜, 列表里已有的不重复, 点开走同一个选中", async () => {
    api.search.mockResolvedValue(JSON.stringify([inList, old1]));
    const pick = vi.fn();
    render(<Harness onSelect={pick} />);
    fireEvent.change(screen.getByLabelText("搜邮件"), { target: { value: "盘" } });
    fireEvent.change(screen.getByLabelText("搜邮件"), { target: { value: "盘点" } });
    expect(screen.getByText("正在搜更早的邮件…")).toBeTruthy();
    expect(api.search).not.toHaveBeenCalled();
    await act(async () => { vi.advanceTimersByTime(OLDER_SEARCH_DEBOUNCE_MS); });
    expect(api.search).toHaveBeenCalledTimes(1);
    expect(api.search).toHaveBeenCalledWith("盘点", "Inbox");
    expect(screen.getByText(/更早的邮件 · 1 封/)).toBeTruthy();
    expect(screen.queryByText("补充医疗保险理赔")).toBeNull();
    fireEvent.click(screen.getByText(old1.subject));
    expect(pick).toHaveBeenCalledWith(old1.id);
  });

  it("一个字不搜; 搜失败说清楚, 不挡列表", async () => {
    render(<Harness onSelect={() => {}} />);
    fireEvent.change(screen.getByLabelText("搜邮件"), { target: { value: "盘" } });
    await act(async () => { vi.advanceTimersByTime(OLDER_SEARCH_DEBOUNCE_MS * 2); });
    expect(api.search).not.toHaveBeenCalled();
    api.search.mockRejectedValue(new Error("「邮件」App 没开"));
    fireEvent.change(screen.getByLabelText("搜邮件"), { target: { value: "盘点" } });
    await act(async () => { vi.advanceTimersByTime(OLDER_SEARCH_DEBOUNCE_MS); });
    expect(screen.getByText(/更早的邮件没搜成: 「邮件」App 没开/)).toBeTruthy();
  });
});
