// @vitest-environment jsdom
/** 11/12 纪要能改、能分享; 转写能改错字、改出的词能记成常用热词。后端全换成假的。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  minutes: null as unknown,
  save: vi.fn(async () => ({ ok: true, edited_at: "2026-11-12T19:00:00" })),
  generate: vi.fn(async () => ({})),
  docx: vi.fn(async () => ({ path: "/m/CC RC末次 会议纪要.docx", name: "CC RC末次 会议纪要.docx", size: 1200 })),
  stage: vi.fn(async () => ({ stage_id: "s_0123456789abcdef", name: "CC RC末次 会议纪要.docx" })),
  edit: vi.fn(async (_id: string, _i: number, text: string) => ({ spk: 0, start: 0, end: 1, text, orig_text: "我们找鸾总确认" })),
  remember: vi.fn(async (w: string[]) => w),
  roomSend: vi.fn(async () => ({ message: {}, sent_to: 3, failed: [] })),
  reveal: vi.fn(async () => undefined),
}));

vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingMinutes: vi.fn(async () => api.minutes),
  meetingTemplatesList: vi.fn(async () => []),
  meetingMinutesGenerate: api.generate,
  meetingMinutesOpenFile: vi.fn(async () => undefined),
  meetingMinutesSave: api.save,
  meetingMinutesDocx: api.docx,
  meetingMinutesStage: api.stage,
  meetingTranscriptEdit: api.edit,
  meetingHotwordsRemember: api.remember,
}));
vi.mock("../../lib/tauri_services", () => ({ toolBridgeCallTool: vi.fn(async () => ({ ok: true, result: { ok: true } })) }));
vi.mock("../../lib/tauri_colleague_rooms", () => ({
  roomList: vi.fn(async () => ({ me: "me@x.cn", rooms: [
    { room_id: "r1", name: "CCRC 迎审组", my_state: "joined", joined: 5 },
    { room_id: "r2", name: "已退出的群", my_state: "left", joined: 2 },
  ] })),
  roomSend: api.roomSend,
}));
vi.mock("../../lib/tauri_app", () => ({ revealInFinder: api.reveal, openFile: vi.fn(async () => undefined) }));
vi.mock("../../lib/tauri_briefing", () => ({ emailAccountsFetch: vi.fn(async () => JSON.stringify([{ name: "工作", address: "me@x.cn", is_default: true }])) }));
vi.mock("../Email/components/ComposeCore", () => ({
  default: (p: { initialSubject: string; initialBody: string; initialAttachments: { name: string }[]; account?: string }) => (
    <div data-testid="compose">{p.initialSubject}|{p.account}|{p.initialAttachments.map((a) => a.name).join(",")}|{p.initialBody.slice(0, 20)}</div>
  ),
}));

import MinutesView from "./MinutesView";
import TranscriptView from "./TranscriptView";

const meta = { id: "mtg_1", title: "CC RC末次", created_at: "2026-11-12T17:41:00", attendees: 12 } as never;
const json = { meeting_id: "mtg_1", summary: "审核方指出不符合项。", decisions: ["确认存在严重不符合项"],
               action_items: [{ task: "补充工具测试记录", owner: "", due: "" }], open_questions: [] };
const markdown = "# CC RC末次\n\n## 摘要\n\n审核方指出**不符合项**。\n\n## 待办\n\n- [ ] 补充工具测试记录\n";

async function mount() {
  await act(async () => { render(<MinutesView meta={meta} />); });
}

beforeEach(() => {
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) },
  });
  api.minutes = { json, markdown, path: "/m/minutes.md" };
  Object.values(api).forEach((f) => typeof f === "function" && "mockClear" in f && (f as { mockClear: () => void }).mockClear());
});
afterEach(cleanup);

describe("纪要: 编辑", () => {
  it("改摘要 / 删决议 / 改待办负责人 → 存回去的是逐项; 在单独的编辑框里改, 纪要原样留在后面", async () => {
    await mount();
    fireEvent.click(screen.getByText("编辑"));
    expect(screen.getByRole("dialog", { name: "编辑纪要" })).toBeTruthy();
    expect(screen.getByText("确认存在严重不符合项")).toBeTruthy();   // 后面的纪要还在
    fireEvent.change(screen.getByLabelText("摘要"), { target: { value: "审核方指出工具测试记录不符合项。" } });
    fireEvent.click(screen.getByLabelText("删掉决议 1"));
    fireEvent.change(screen.getByLabelText("待办 1 负责人"), { target: { value: "张三" } });
    fireEvent.click(screen.getByText("＋ 加一条待定问题"));
    fireEvent.change(screen.getByLabelText("待定问题 1"), { target: { value: "  " } });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(api.save).toHaveBeenCalledWith("mtg_1", {
      summary: "审核方指出工具测试记录不符合项。", decisions: [], open_questions: [], carried_over: [],
      action_items: [{ task: "补充工具测试记录", owner: "张三", due: "" }],
    }, null);
    expect(await screen.findByText(/纪要已保存/)).toBeTruthy();
  });

  it("按文字模版写的: 改全文", async () => {
    api.minutes = { json: { ...json, template: { id: "t", name: "公司模版" } }, markdown, path: "" };
    await mount();
    fireEvent.click(screen.getByText("编辑"));
    fireEvent.change(screen.getByLabelText("纪要全文"), { target: { value: "# 改过的全文" } });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(api.save).toHaveBeenCalledWith("mtg_1", { action_items: [{ task: "补充工具测试记录", owner: "", due: "" }] }, "# 改过的全文");
  });

  it("改过的纪要: 重新生成先提醒, 再点一次才生成", async () => {
    api.minutes = { json: { ...json, edited_at: "2026-11-12T19:00:00" }, markdown, path: "" };
    await mount();
    expect(screen.getByText("已人工修改")).toBeTruthy();
    fireEvent.click(screen.getByText("重新生成"));
    expect(api.generate).not.toHaveBeenCalled();
    expect(screen.getByText(/你改的内容会被覆盖/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("再点一次: 覆盖改过的内容")); });
    expect(api.generate).toHaveBeenCalled();
  });
});

describe("纪要: 分享", () => {
  it("复制: 纯文本进剪贴板", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("复制")); });
    expect(writeText).toHaveBeenCalledWith("【CC RC末次】\n\n【摘要】\n\n审核方指出不符合项。\n\n【待办】\n\n☐ 补充工具测试记录");
    expect(screen.getByText(/纪要已复制/)).toBeTruthy();
  });

  it("发邮件: 写信面板带好主题、默认账号、Word 附件、正文", async () => {
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("发邮件")); });
    const c = await screen.findByTestId("compose");
    const [subject, account, files, body] = (c.textContent ?? "").split("|");
    expect([subject, account, files]).toEqual(["CC RC末次 会议纪要 (2026-11-12)", "me@x.cn", "CC RC末次 会议纪要.docx"]);
    expect(body.startsWith("【CC RC末次】")).toBe(true);
  });

  it("发到协同群: 只列我在的群, 点发送才发, 带 Word", async () => {
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("发到协同群")); });
    const sel = (await screen.findByLabelText("选群")) as HTMLSelectElement;
    expect([...sel.options].map((o) => o.text)).toEqual(["CCRC 迎审组 (5 人)"]);
    expect(api.roomSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("同时附上纪要 Word 文件"));
    await act(async () => { fireEvent.click(screen.getByText("发送")); });
    expect(api.stage).toHaveBeenCalledWith("mtg_1");
    expect(api.roomSend).toHaveBeenCalledWith("r1", expect.stringContaining("☐ 补充工具测试记录"), [], null,
      [{ stage_id: "s_0123456789abcdef", name: "CC RC末次 会议纪要.docx" }]);
    expect(await screen.findByText(/已发到群「CCRC 迎审组」/)).toBeTruthy();
  });

  it("导出 Word: 生成后在文件夹里显示", async () => {
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("导出 Word")); });
    expect(api.reveal).toHaveBeenCalledWith("/m/CC RC末次 会议纪要.docx");
    expect(screen.getByText(/已存成 Word/)).toBeTruthy();
  });
});

describe("转写: 改错字", () => {
  it("改一句 → 标「改过」, 改出的词能记成常用热词", async () => {
    const t = { duration_secs: 10, speakers: 1, speakers_requested: 1, segments: [{ spk: 0, start: 0, end: 1, text: "我们找鸾总确认" }] };
    render(<TranscriptView meetingId="mtg_1" transcript={t} names={{}} colors={["#000"]} />);
    fireEvent.click(screen.getByLabelText("改第 1 句"));
    fireEvent.change(screen.getByLabelText("改这一句"), { target: { value: "我们找栾总确认" } });
    await act(async () => { fireEvent.click(screen.getByText("保存")); });
    expect(api.edit).toHaveBeenCalledWith("mtg_1", 0, "我们找栾总确认");
    expect(screen.getByText("改过")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("＋ 栾总")); });
    expect(api.remember).toHaveBeenCalledWith(["栾总"]);
    await waitFor(() => expect(screen.getByText(/已记成常用热词: 栾总/)).toBeTruthy());
  });
});
