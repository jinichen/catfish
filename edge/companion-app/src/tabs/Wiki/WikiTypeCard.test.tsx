// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { WikiTypeEntry } from "../../lib/tauri_wiki_types";

const startChat = vi.fn();
const applied: unknown[] = [];
let entry: WikiTypeEntry;

vi.mock("../../lib/tauri_wiki_types", () => ({
  wikiTypeEntry: vi.fn(async () => entry),
  wikiPropsApply: vi.fn(async (items: unknown) => { applied.push(items); return { ok: true, written: 1, errors: [], backup_dir: "/b" }; }),
}));
vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: { startEmailChat: typeof startChat }) => unknown) => sel({ startEmailChat: startChat }),
}));
vi.mock("../../store/wiki", () => ({
  useWikiStore: (sel: (s: { loadFiles: () => Promise<void> }) => unknown) => sel({ loadFiles: async () => {} }),
}));
vi.mock("../../lib/expertChat", () => ({ selectExpert: vi.fn() }));

import WikiTypeCard from "./WikiTypeCard";

describe("WikiTypeCard (11/07 证书属性卡片)", () => {
  beforeEach(() => {
    cleanup();
    startChat.mockReset();
    applied.length = 0;
    entry = {
      ok: true, rel_path: "wiki/entities/c.md", typed: true, type: "cert", label: "资质证书",
      properties: [
        { key: "cert_no", label: "证书编号", type: "text", hint: "", value: "CCRC-1" },
        { key: "valid_until", label: "有效期至", type: "date", hint: "", value: "2027-03-01" },
        { key: "review_on", label: "复审日期", type: "date", hint: "", value: "" },
      ],
      status: { code: "warn", label: "状态", value: "即将到期", days_left: "114" },
      actions: [
        { id: "type.cert.expiry-reminder", label: "到期提醒", description: "", skill_path: "", instruction: "建提醒 2027-03-01",
          available: true, reason: null, skill_dir: null, runnable: false },
        { id: "type.cert.verify-official", label: "官网核查", description: "", skill_path: "personal/x", instruction: "核对",
          available: false, reason: "本机没装这个技能", skill_dir: null, runnable: false },
      ],
    };
  });

  it("显示属性、状态, 没填的写未填; 没装技能的行动灰掉", async () => {
    render(<WikiTypeCard relPath="wiki/entities/c.md" title="证书C" readOnly={false} />);
    await screen.findByText("资质证书");
    expect(screen.getByText("CCRC-1")).toBeTruthy();
    expect(screen.getByText(/即将到期 · 还剩 114 天/)).toBeTruthy();
    expect(screen.getByText("未填")).toBeTruthy();
    expect(screen.getByText("本机没装这个技能")).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: /交给小鲶/ }) as HTMLButtonElement[];
    expect(buttons[1].disabled).toBe(true);
  });

  it("点行动交给小鲶, 属性和要做的事一起带过去", async () => {
    render(<WikiTypeCard relPath="wiki/entities/c.md" title="证书C" readOnly={false} />);
    await screen.findByText("资质证书");
    fireEvent.click(screen.getAllByRole("button", { name: /交给小鲶/ })[0]);
    expect(startChat).toHaveBeenCalledTimes(1);
    const [key, prompt] = startChat.mock.calls[0];
    expect(key).toBe("wiki-type-action:type.cert.expiry-reminder:wiki/entities/c.md");
    expect(prompt).toContain("证书编号: CCRC-1");
    expect(prompt).toContain("要做的事：建提醒 2027-03-01");
  });

  it("改属性只提交改了的那几项", async () => {
    render(<WikiTypeCard relPath="wiki/entities/c.md" title="证书C" readOnly={false} />);
    await screen.findByText("资质证书");
    fireEvent.click(screen.getByRole("button", { name: /改属性/ }));
    fireEvent.change(screen.getByLabelText("复审日期"), { target: { value: "2027-05-01" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(applied.length).toBe(1));
    expect(applied[0]).toEqual([{ rel_path: "wiki/entities/c.md", props: { review_on: "2027-05-01" } }]);
  });

  it("没有类型定义的条目不显示卡片; 部门共享的只读", async () => {
    entry = { ok: true, rel_path: "x", typed: false };
    const { container } = render(<WikiTypeCard relPath="x" title="x" readOnly={false} />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });
});
