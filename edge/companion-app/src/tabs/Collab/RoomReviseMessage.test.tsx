// @vitest-environment jsdom
/** 群里一起改文档 (11/11): 卡片上每人状态 + 该点的按钮; 合并面板冲突没定完不能发。邮箱都是 example.com。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/colleagueRoomsStore", () => ({ refreshRooms: vi.fn(), refreshRevisePending: vi.fn(async () => undefined),
                                                   setActiveRoom: vi.fn(), useColleagueRooms: () => ({}) }));
vi.mock("./RoomsDelivery", () => ({ DeliveryLine: () => null, RoomFileList: () => null, stageFiles: vi.fn() }));
vi.mock("../../lib/tauri_app", () => ({ openFile: vi.fn(async () => undefined) }));
const api = vi.hoisted(() => ({
  reviseProgress: vi.fn(), reviseOpen: vi.fn(), reviseReturn: vi.fn(), reviseDecline: vi.fn(), reviseRemind: vi.fn(),
  reviseClose: vi.fn(), reviseSelfClear: vi.fn(), reviseMerge: vi.fn(), reviseDecide: vi.fn(), revisePublish: vi.fn(), reviseSuggest: vi.fn(),
}));
vi.mock("../../lib/tauri_room_revise", async (orig) => ({ ...(await orig<object>()), ...api }));

import type { RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";
import type { ReviseAnalysis, ReviseProgress } from "../../lib/tauri_room_revise";
import ReviseMergePanel from "./ReviseMergePanel";
import RoomReviseMessage from "./RoomReviseMessage";

const M = (email: string, name: string): RoomMember => ({ email, name, state: "joined", owner: false, me: false });
const members = [M("zhou@example.com", "周总"), M("zhang@example.com", "张三"), M("li@example.com", "李四")];
const card = { rev_id: "r_20261111_aaaaaaaa", doc_id: "d_0123456789abcdef", title: "实施方案", version: 1, fmt: "docx", name: "实施方案.docx",
               owner: "zhou@example.com", editors: ["zhang@example.com", "li@example.com"], deadline: "2026-11-20", note: "" };
const msg: RoomMessage = { msg_id: "m_c", kind: "revise", from: "zhou@example.com", at: "2026-11-11T10:00:00", text: "📝 请修改《实施方案》(v1, 11/20 截止)",
                           revise: card, files: [{ id: "f_1", name: "实施方案.docx", size: 1000, sha256: "x", n: 1, have: true }] };

function progress(over: Partial<ReviseProgress>): ReviseProgress {
  return { card, owner: false, me: "li@example.com", merged: null, closed: false, all_in: false, can_merge: false, my_state: "pending",
           local_path: null, msg_id: "m_c",
           people: [{ email: "zhang@example.com", name: "张三", state: "returned", at: "2026-11-11T12:00:00", late: false },
                    { email: "li@example.com", name: "李四", state: "pending", at: null, late: false }], ...over };
}
const props = { members, roomId: "r1", tick: 0, onChanged: vi.fn(), onNotice: vi.fn(), onMerge: vi.fn(), onVersions: vi.fn(), onRevise: vi.fn() };

beforeEach(() => { for (const f of Object.values(api)) f.mockReset(); });
afterEach(cleanup);

describe("RoomReviseMessage", () => {
  it("被请的人: 只有一个主按钮 —— 没下载「下载修改」, 下了之后「交回」; 别的收在「更多」里; 一点没改先问", async () => {
    api.reviseProgress.mockResolvedValue(progress({}));
    render(<RoomReviseMessage m={msg} me="li@example.com" {...props} />);
    await screen.findByText(/已交 1\/2/);
    expect(screen.queryByText("张三 交回了")).toBeNull();             // 每个人的状态点开才看
    fireEvent.click(screen.getByText(/已交 1\/2/));
    expect(screen.getByText("张三 交回了")).toBeTruthy();
    expect(screen.queryByText("交回")).toBeNull();
    expect(screen.queryByText("不改了")).toBeNull();
    fireEvent.click(screen.getByText("更多"));
    expect(screen.getByText("不改了")).toBeTruthy();
    api.reviseOpen.mockResolvedValue({ path: "/Users/x/Downloads/鲶鱼群文件/方案群/实施方案 (v1·待我修改).docx" });
    api.reviseProgress.mockResolvedValue(progress({ local_path: "/p.docx", my_state: "editing",
      people: [{ email: "zhang@example.com", name: "张三", state: "returned", at: "x", late: false }, { email: "li@example.com", name: "李四", state: "editing", at: "y", late: false }] }));
    fireEvent.click(screen.getByText("下载修改"));
    await screen.findByText("交回");
    expect(screen.queryByText("下载修改")).toBeNull();
    api.reviseReturn.mockResolvedValueOnce({ ok: false, need_confirm: "unchanged" }).mockResolvedValueOnce({ ok: true });
    fireEvent.click(screen.getByText("交回"));
    await screen.findByText(/一点没改/);
    fireEvent.click(screen.getByText("确定"));
    await waitFor(() => expect(api.reviseReturn).toHaveBeenLastCalledWith("r1", card.rev_id, { confirm_unchanged: true, confirm_open: false }));
    expect(screen.queryByText(/^合并/)).toBeNull();   // 不是发起人: 没有合并
  });

  it("文件还开着: 先问; 点「已经存好了, 交」再带上 confirm_open; 接着没改再问一次时两样都带", async () => {
    api.reviseProgress.mockResolvedValue(progress({ local_path: "/p.docx", my_state: "editing" }));
    render(<RoomReviseMessage m={msg} me="li@example.com" {...props} />);
    api.reviseReturn.mockResolvedValueOnce({ ok: false, need_confirm: "open" })
      .mockResolvedValueOnce({ ok: false, need_confirm: "unchanged" }).mockResolvedValueOnce({ ok: true });
    fireEvent.click(await screen.findByText("交回"));
    fireEvent.click(await screen.findByText("已经存好了, 交"));
    await waitFor(() => expect(api.reviseReturn).toHaveBeenLastCalledWith("r1", card.rev_id, { confirm_unchanged: false, confirm_open: true }));
    fireEvent.click(await screen.findByText("确定"));
    await waitFor(() => expect(api.reviseReturn).toHaveBeenLastCalledWith("r1", card.rev_id, { confirm_unchanged: true, confirm_open: true }));
  });

  it("用 Keynote 改的 (存成了 .key): 说清楚改动在哪、怎么导出, 不再问「就当没改」", async () => {
    api.reviseProgress.mockResolvedValue(progress({ local_path: "/d/方案 (v1·待我修改).pptx", my_state: "editing" }));
    render(<RoomReviseMessage m={msg} me="li@example.com" {...props} />);
    api.reviseReturn.mockResolvedValueOnce({ ok: false, need_confirm: "unchanged",
      saved_elsewhere: { app: "Keynote", office: "PowerPoint", saved: "/Users/x/Downloads/方案 (v1·待我修改).key", target: "/d/方案 (v1·待我修改).pptx" } });
    fireEvent.click(await screen.findByText("交回"));
    await screen.findByText("改动存在了 Keynote 文件里");
    expect(screen.getByText(/导出为 → PowerPoint/)).toBeTruthy();
    expect(screen.getByText(/方案 \(v1·待我修改\)\.key/)).toBeTruthy();
    expect(screen.getByText("不用了, 就当没改")).toBeTruthy();
  });

  it("发起人: 有人交了主按钮是「合并」, 催 / 结束在「更多」里; 卡片上没有「我也改」(自己改的在合并面板里选)", async () => {
    api.reviseProgress.mockResolvedValue(progress({ owner: true, me: "zhou@example.com", can_merge: true, my_state: null }));
    const onMerge = vi.fn();
    render(<RoomReviseMessage m={msg} me="zhou@example.com" {...props} onMerge={onMerge} />);
    fireEvent.click(await screen.findByText("合并 (1)"));
    expect(onMerge).toHaveBeenCalledWith(card.rev_id);
    expect(screen.queryByText("催一下")).toBeNull();
    expect(screen.queryByText("我也改")).toBeNull();
    fireEvent.click(screen.getByText("更多"));
    expect(screen.getByText("催一下")).toBeTruthy();
    expect(screen.getByText("结束")).toBeTruthy();
    expect(screen.queryByText("下载修改")).toBeNull();
  });

  it("发起人: 还没人交, 主按钮是「催一下」", async () => {
    api.reviseProgress.mockResolvedValue(progress({ owner: true, me: "zhou@example.com", my_state: null,
      people: [{ email: "zhang@example.com", name: "张三", state: "pending", at: null, late: false }, { email: "li@example.com", name: "李四", state: "editing", at: null, late: false }] }));
    render(<RoomReviseMessage m={msg} me="zhou@example.com" {...props} />);
    await screen.findByText("催一下");
    expect(screen.queryByText(/^合并/)).toBeNull();
    // 没人交回, 发起人自己改了: 「更多」里能进合并面板
    const onMerge = vi.fn();
    cleanup();
    render(<RoomReviseMessage m={msg} me="zhou@example.com" {...props} onMerge={onMerge} />);
    fireEvent.click(await screen.findByText("更多"));
    fireEvent.click(screen.getByText("我自己改了, 合并"));
    expect(onMerge).toHaveBeenCalledWith(card.rev_id);
  });

  it("合并面板: 还没人交回时不报红字, 让发起人选自己改的文件; 选了就能合", async () => {
    api.reviseMerge.mockRejectedValueOnce(new Error("还没有人交回修改"));
    render(<ReviseMergePanel roomId="r1" revId={card.rev_id} title="实施方案" onClose={vi.fn()} onDone={vi.fn()} />);
    await screen.findByText(/同事还没有交回修改/);
    expect(screen.getByText("选你改的文件, 一起合")).toBeTruthy();
  });

  it("合并版发出后卡片收成一行, 不再有按钮; 有人迟交才重新出现合并", async () => {
    api.reviseProgress.mockResolvedValue(progress({ owner: true, me: "zhou@example.com", my_state: null, can_merge: true,
      merged: { version: 2, at: "x", msg_id: "m_m" } }));
    const { unmount } = render(<RoomReviseMessage m={msg} me="zhou@example.com" {...props} />);
    await screen.findByText("📝 《实施方案》已出 v2");
    expect(screen.getByText("版本记录")).toBeTruthy();
    expect(screen.queryByText("更多")).toBeNull();
    unmount();
    api.reviseProgress.mockResolvedValue(progress({ owner: true, me: "zhou@example.com", my_state: null, can_merge: true,
      merged: { version: 2, at: "x", msg_id: "m_m" },
      people: [{ email: "zhang@example.com", name: "张三", state: "returned", at: "x", late: false },
               { email: "li@example.com", name: "李四", state: "returned", at: "y", late: false, after_merge: true }] }));
    render(<RoomReviseMessage m={msg} me="zhou@example.com" {...props} />);
    await screen.findByText("合并迟到的 1 份");
  });

  it("合并版那条: 有「版本记录」和「在这一版上再请大家改」", () => {
    const merged: RoomMessage = { msg_id: "m_m", kind: "revise_status", from: "zhou@example.com", at: "2026-11-12T10:00:00",
      text: "✅ 《实施方案》v2 合并版\n合并了 张三 1 处", revise_status: { rev_id: card.rev_id, doc_id: card.doc_id, title: "实施方案", state: "merged", version: 2 },
      files: [{ id: "f_t", name: "实施方案 v2 修订版.docx", size: 1, sha256: "a", n: 1, have: true }, { id: "f_c", name: "实施方案 v2.docx", size: 1, sha256: "b", n: 1, have: true }] };
    const onRevise = vi.fn();
    render(<RoomReviseMessage m={merged} me="li@example.com" {...props} onRevise={onRevise} />);
    fireEvent.click(screen.getByText("在这一版上再请大家改"));
    expect(onRevise).toHaveBeenCalledWith({ msg_id: "m_m", file_id: "f_c", name: "实施方案 v2.docx", doc_id: card.doc_id, version: 2 });
    expect(screen.getByText(/修订版」打开就能看到谁改了哪里/)).toBeTruthy();
  });
});

describe("ReviseMergePanel", () => {
  const an: ReviseAnalysis = {
    conflicts: [{ cid: "c_1", where: "「3 报价」下第 1 段", kind: "text", base: "总价 48 万", combined: "周期 4 个月, 总价 52 万", custom: true,
                  options: [{ key: "zhang@example.com", who: ["zhang@example.com"], names: ["张三"], text: "周期 4 个月, 总价 48 万" },
                            { key: "li@example.com", who: ["li@example.com"], names: ["李四"], text: "总价 52 万" }] }],
    changes: { "zhang@example.com": 2, "li@example.com": 1 }, same: [], warnings: ["王五 只调了格式"], late: false, decisions: {},
    names: { "zhang@example.com": "张三", "li@example.com": "李四" }, returned: ["zhang@example.com", "li@example.com"],
  };
  it("冲突没定完不能发; 选了拼合建议就能发", async () => {
    api.reviseMerge.mockResolvedValue(an);
    api.reviseDecide.mockResolvedValue({ decisions: {} });
    api.revisePublish.mockResolvedValue({ version: 2, warnings: [] });
    const onDone = vi.fn();
    render(<ReviseMergePanel roomId="r1" revId={card.rev_id} title="实施方案" onClose={vi.fn()} onDone={onDone} />);
    await screen.findByText(/自动合并: 张三 2 处、李四 1 处/);
    expect(screen.getByText("选你改的文件, 一起合")).toBeTruthy();   // 发起人自己也改了: 在这里选
    expect(screen.getByText("⚠ 王五 只调了格式")).toBeTruthy();
    expect(screen.getByText("还有 1 处没定")).toBeTruthy();
    expect((screen.getByText("发出合并版").closest("button") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText(/拼起来是这样/));
    expect(api.reviseDecide).toHaveBeenCalledWith("r1", card.rev_id, "c_1", "text", "周期 4 个月, 总价 52 万");
    const btn = screen.getByText("发出合并版").closest("button") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("《实施方案》v2 合并版已发到群里"));
  });
  it("小鲶不给建议时说为什么", async () => {
    api.reviseMerge.mockResolvedValue(an);
    api.reviseSuggest.mockResolvedValue({ text: null, reason: "几种改法意思相反, 小鲶不硬合 —— 请你定" });
    render(<ReviseMergePanel roomId="r1" revId={card.rev_id} title="实施方案" onClose={vi.fn()} onDone={vi.fn()} />);
    fireEvent.click(await screen.findByText("🐟 让小鲶给个兼顾的写法"));
    await screen.findByText(/意思相反/);
  });
});
