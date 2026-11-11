// @vitest-environment jsdom
/** 协同事项里的「请你修改」(11/11): 没有就不显示; 快截止的标出来; 下载修改 / 去群里交回。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ revise: [] as unknown[], openRoom: vi.fn(), reviseOpen: vi.fn(), openFile: vi.fn(async () => undefined) }));
vi.mock("../../lib/colleagueRoomsStore", () => ({ useColleagueRooms: () => ({ revise: h.revise }), refreshRevisePending: vi.fn(async () => undefined) }));
vi.mock("../../lib/tauri_room_revise", () => ({ reviseOpen: h.reviseOpen }));
vi.mock("../../lib/tauri_app", () => ({ openFile: h.openFile }));
vi.mock("../../store/ui", () => ({ useUIStore: (sel: (s: { openRoom: typeof h.openRoom }) => unknown) => sel({ openRoom: h.openRoom }) }));

import ReviseTodoPanel from "./ReviseTodoPanel";

afterEach(cleanup);
const item = { room_id: "r1", room_name: "方案群", rev_id: "r_20261111_aaaaaaaa", title: "实施方案", version: 1, owner_name: "周总",
               deadline: "2026-11-12", days_left: 1, state: "pending", local_path: null, msg_id: "m", at: "x" };

describe("ReviseTodoPanel", () => {
  it("没有待办整块不显示", () => {
    h.revise = [];
    const { container } = render(<ReviseTodoPanel />);
    expect(container.textContent).toBe("");
  });
  it("列出请我改的, 明天截止标出来; 下载修改打开文件, 去群里交回跳到群", async () => {
    h.revise = [item];
    h.reviseOpen.mockResolvedValue({ path: "/p.docx" });
    render(<ReviseTodoPanel />);
    expect(screen.getByText("📝 请你修改 (1)")).toBeTruthy();
    expect(screen.getByText("明天截止")).toBeTruthy();
    fireEvent.click(screen.getByText("下载修改"));
    await waitFor(() => expect(h.openFile).toHaveBeenCalledWith("/p.docx"));
    fireEvent.click(screen.getByText("去群里交回"));
    expect(h.openRoom).toHaveBeenCalledWith("r1");
  });
});
