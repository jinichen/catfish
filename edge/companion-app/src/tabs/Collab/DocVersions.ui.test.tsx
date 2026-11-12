// @vitest-environment jsdom
/** 11/12 版本记录: 新一轮挂在它基于的那一版下面; 只有最新一版能发邮件; 发邮件带这一轮的人、合并说明、干净版附件。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ saved: vi.fn(async (_r: string, id: string) => ({ path: `/dl/${id}.pptx` })) }));
const V = [
  { version: 4, at: "2026-11-12T01:46:00", by: "chenhongbo", kind: "merged", msg_id: "m4", text: "《汇报》v4 合并版\n合并了 张三 14 处\n冲突 2 处已定",
    files: [{ id: "f4t", name: "汇报 v4 修订版.pptx", size: 30_000_000 }, { id: "f4", name: "汇报 v4.pptx", size: 1_000_000 }],
    rev_id: "r3", editors: [{ email: "me@x.cn", name: "我" }, { email: "zs@x.cn", name: "张三" }] },
  { version: 3, at: "2026-11-12T01:46:00", by: "chenhongbo", kind: "restart", msg_id: "s3b", files: [{ id: "f3b", name: "汇报 v3.pptx", size: 1 }],
    rev_id: "r3", editors: [{ email: "zs@x.cn", name: "张三" }] },
  { version: 3, at: "2026-11-12T01:18:00", by: "chenhongbo", kind: "merged", msg_id: "m3", text: "x\n合并了 张三 21 处",
    files: [{ id: "f3", name: "汇报 v3.pptx", size: 1 }], rev_id: "r2", editors: [] },
  { version: 1, at: "2026-11-11T19:31:00", by: "chenhongbo", kind: "start", msg_id: "s1", files: [{ id: "f1", name: "汇报.pptx", size: 1 }] },
];
vi.mock("../../lib/tauri_room_revise", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_room_revise")>()),
  reviseVersions: vi.fn(async () => ({ title: "汇报", versions: V })),
  reviseDiff: vi.fn(),
}));
vi.mock("../../lib/tauri_colleague_rooms", () => ({ roomFileSave: api.saved }));
vi.mock("../../lib/tauri_app", () => ({ openFile: vi.fn() }));
vi.mock("../Email/components/ComposeDialog", () => ({
  default: (p: { subject: string; body: string; initialTo: string; attachments: { name: string }[]; notes: React.ReactNode; ready: boolean }) => (
    <div data-testid="dlg">
      <span data-testid="meta">{p.subject}|{p.initialTo}|{p.attachments.map((a) => a.name).join(",")}|{String(p.ready)}</span>
      <pre data-testid="body">{p.body}</pre>
      {p.notes}
    </div>
  ),
}));

import DocVersions from "./DocVersions";

afterEach(cleanup);

describe("DocVersions", () => {
  it("新一轮挂在 v3 下面, 不再列成第二个 v3; 只有最新一版有发邮件", async () => {
    await act(async () => { render(<DocVersions roomId="r" docId="d" me="me@x.cn" onClose={() => undefined} />); });
    expect(screen.getAllByText("v3").length).toBe(1);
    expect(screen.getByText(/基于 v3 发起新一轮修改 \(请了 张三\)/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /发邮件/ }).length).toBe(1);
  });

  it("发邮件: 收件人是这一轮请的人 (不含我), 正文是合并说明, 默认只附干净版; 勾上才带修订版", async () => {
    await act(async () => { render(<DocVersions roomId="r" docId="d" me="me@x.cn" onClose={() => undefined} />); });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /发邮件/ })); });
    expect(screen.getByTestId("meta").textContent).toBe("《汇报》v4 合并版|zs@x.cn|汇报 v4.pptx|true");
    expect(screen.getByTestId("body").textContent).toBe("《汇报》v4 合并版, 请查收附件。\n\n这一版:\n· 合并了 张三 14 处\n· 冲突 2 处已定");
    expect(api.saved).toHaveBeenCalledWith("r", "f4");
    await act(async () => { fireEvent.click(screen.getByLabelText(/同时附修订版/)); });
    expect(screen.getByTestId("meta").textContent).toBe("《汇报》v4 合并版|zs@x.cn|汇报 v4.pptx,汇报 v4 修订版.pptx|true");
  });
});
