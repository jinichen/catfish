// @vitest-environment jsdom
/** 11/09 页面顶上改分类 (「AAA信用等级证书」掉在「未分类」, 界面上改不回来)。数据全是虚构的。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WikiFileInfo } from "../../lib/tauri_wiki";
import { useWikiStore } from "../../store/wiki";

const t = vi.hoisted(() => ({ wikiReadFile: vi.fn(), wikiUpdateFile: vi.fn() }));
vi.mock("../../lib/tauri_wiki", async (orig) => ({ ...(await orig<typeof import("../../lib/tauri_wiki")>()), ...t }));

import WikiTypePicker from "./WikiTypePicker";

const info = (subtype: string | null) =>
  ({ rel_path: "wiki/entities/AAA.md", kind: "entity", subtype, title: "AAA信用等级证书" }) as unknown as WikiFileInfo;
const loadFiles = vi.fn(async () => {});
const selectFile = vi.fn(async () => {});

beforeEach(() => {
  t.wikiReadFile.mockResolvedValue({ content: "# AAA信用等级证书\n\n正文" });
  t.wikiUpdateFile.mockResolvedValue({ ok: true });
  useWikiStore.setState({
    files: [
      { kind: "entity", subtype: "cert" }, { kind: "entity", subtype: "cert" },
      { kind: "entity", subtype: "org" }, { kind: "concept", subtype: "rule" },
    ] as unknown as WikiFileInfo[],
    loadFiles, selectFile,
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("改分类", () => {
  it("未分类: 选一个在用的分类, 只改这一行, 刷新列表", async () => {
    render(<WikiTypePicker info={info(null)} />);
    fireEvent.click(screen.getByRole("button", { name: /未分类/ }));
    const sel = screen.getByRole("combobox", { name: "分类" }) as HTMLSelectElement;
    expect([...sel.options].map((o) => o.textContent)).toEqual(["未分类", "证书 (2)", "组织 (1)", "其他分类…"]);
    fireEvent.change(sel, { target: { value: "cert" } });
    await waitFor(() => expect(t.wikiUpdateFile).toHaveBeenCalledWith("wiki/entities/AAA.md",
      "---\nentity_type: cert\n---\n\n# AAA信用等级证书\n\n正文"));
    expect(loadFiles).toHaveBeenCalled();
    expect(selectFile).toHaveBeenCalledWith("wiki/entities/AAA.md");
  });

  it("其他分类: 填新名字; 带冒号的不收", async () => {
    t.wikiReadFile.mockResolvedValue({ content: "---\ntitle: x\nentity_type: strategy\n---\n正文" });
    render(<WikiTypePicker info={info("strategy")} />);
    fireEvent.click(screen.getByRole("button", { name: /战略/ }));
    fireEvent.change(screen.getByRole("combobox", { name: "分类" }), { target: { value: "__other__" } });
    const input = screen.getByRole("textbox", { name: "新分类" });
    fireEvent.change(input, { target: { value: "a: b" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/不能有换行、冒号/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "发展战略" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(t.wikiUpdateFile).toHaveBeenCalledWith("wiki/entities/AAA.md",
      "---\ntitle: x\nentity_type: 发展战略\n---\n正文"));
  });

  it("部门共享的页只读", () => {
    render(<WikiTypePicker info={info("cert")} readOnly />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("证书")).toBeTruthy();
  });
});
