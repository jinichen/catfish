// @vitest-environment jsdom
/** 纪要卡片 / 模版弹窗的界面 (10/3 UI 调整): 后端调用全换成假的, 只验渲染和交互。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  minutes: null as unknown,
  templates: [] as unknown[],
  generate: vi.fn(async () => ({})),
  revise: vi.fn(),
  save: vi.fn(async () => ({ ok: true, edited_at: "2026-11-17T10:00:00" })),
}));

vi.mock("../../lib/tauri_meeting", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_meeting")>()),
  meetingMinutes: vi.fn(async () => api.minutes),
  meetingTemplatesList: vi.fn(async () => api.templates),
  meetingMinutesGenerate: api.generate,
  meetingMinutesOpenFile: vi.fn(async () => undefined),
  meetingMinutesRevise: api.revise,
  meetingMinutesSave: api.save,
}));
const cards = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  meetingMinutesCards: cards,
}));
vi.mock("../../lib/tauri_services", () => ({
  toolBridgeCallTool: vi.fn(async () => ({ ok: true, result: { ok: true } })),
  fetchCatalog: vi.fn(async () => ({ models: [{ id: "catfish-public-qwen-flash", display_name: "qwen3.8-flash（公有云）" }] })),
}));

import MinutesView from "./MinutesView";
import { _clearReviseMemory } from "./reviseStore";

const meta = { id: "mtg_1", title: "周会", created_at: "2026-10-01T10:00:00", attendees: 2 } as never;
const base = { meeting_id: "mtg_1", summary: "讨论了二期。", decisions: [], action_items: [], open_questions: [] };

async function mount() {
  await act(async () => { render(<MinutesView meta={meta} />); });
}

beforeEach(() => {
  const data = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v),
             removeItem: (k: string) => void data.delete(k) },
  });
  api.minutes = null;
  api.templates = [];
  api.generate.mockClear();
  api.revise.mockReset();
  _clearReviseMemory();
  api.save.mockClear();
});
afterEach(cleanup);

describe("纪要卡片", () => {
  it("没生成: 头上就有模版选择 + 生成纪要, 生成用选中的模版", async () => {
    api.templates = [{ id: "tpl_w", name: "公司纪要表", body: "", updated_at: "", kind: "docx", slots: [] }];
    await mount();
    const select = screen.getByLabelText("纪要模版") as HTMLSelectElement;
    expect([...select.options].map((o) => o.text)).toEqual(["标准纪要", "Word · 公司纪要表", "管理模版…"]);
    fireEvent.change(select, { target: { value: "tpl_w" } });
    await act(async () => { fireEvent.click(screen.getByText("生成纪要")); });
    expect(api.generate).toHaveBeenCalledWith("mtg_1", "tpl_w");
  });

  it("11/3: 有上次遗留进展和参考的背景材料就显示; 老纪要没有这两项照常", async () => {
    api.minutes = {
      json: {
        ...base,
        carried_over: ["二期报价已定"],
        references: {
          wiki: [{ title: "李建国", rel_path: "wiki/entities/laoli.md" }],
          previous_meeting: { meeting_id: "mtg_0", title: "周会 10.27", date: "2026-10-27" },
        },
      },
      markdown: "# 周会", path: "/x",
    };
    await mount();
    expect(screen.getByText("上次遗留事项进展")).toBeTruthy();
    expect(screen.getByText("· 二期报价已定")).toBeTruthy();
    expect(screen.getByText("参考的背景材料: 知识库 「李建国」; 上次会议「周会 10.27」(2026-10-27)")).toBeTruthy();
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
    // 11/17: 「管理模版」并进模版下拉的最后一项; 选它只开弹窗, 不改选中的模版
    const select = screen.getByLabelText("纪要模版") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "__manage__" } });
    expect(select.value).toBe("default");
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

describe("让小鲶改纪要 (11/17)", () => {
  const minutes = {
    json: { ...base, decisions: ["二期 12 月上线"], action_items: [{ task: "交材料", owner: "说话人2", due: "" }] },
    markdown: "# 周会\n- [ ] 交材料 (说话人2)", path: "/x",
  };
  const proposal = {
    reply: "待办负责人改成了王总, 补了一条。", markdown: null, transcript_full: true,
    changed: ["action_items"], changed_labels: ["待办"],
    fields: { ...minutes.json, action_items: [{ task: "交材料", owner: "王总", due: "" }, { task: "约客户", owner: "", due: "" }] },
  };

  it("说一句 → 正文变改动预览 (只改负责人的一行带原来的, 新加的 ＋) → 采用才存", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce(proposal);
    await mount();
    expect(screen.getByText("比如")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("让小鲶改纪要"), { target: { value: "说话人2 是王总" } });
    await act(async () => { fireEvent.click(screen.getByText("让小鲶改")); });
    expect(api.revise).toHaveBeenCalledWith("mtg_1", "说话人2 是王总", null, []);
    expect(screen.getByText(/小鲶改了: 待办/)).toBeTruthy();
    expect(screen.getByText("待办负责人改成了王总, 补了一条。")).toBeTruthy();
    const row = screen.getByText(/· 交材料 \(王总\)/);
    expect(row.textContent).toBe("· 交材料 (王总)原来: 说话人2");
    expect(screen.getByText("＋ 约客户")).toBeTruthy();
    expect(screen.queryByText("存进知识库")).toBeNull();            // 预览时不出写回按钮
    expect((screen.getByText("重新生成").closest("button") as HTMLButtonElement).disabled).toBe(true);
    expect(api.save).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByText("采用")); });
    expect(api.save).toHaveBeenCalledWith("mtg_1", proposal.fields, null);
    expect(screen.getByText("已采用小鲶改的版本")).toBeTruthy();
  });

  it("接着说: 在上一版提议上改, 带上之前的要求; 「不要」原样不动", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce(proposal).mockResolvedValueOnce({ ...proposal, reply: "又改了" });
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "说话人2 是王总" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    fireEvent.change(box, { target: { value: "删掉约客户" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(api.revise).toHaveBeenLastCalledWith("mtg_1", "删掉约客户", { fields: proposal.fields, markdown: null }, ["说话人2 是王总"]);
    fireEvent.click(screen.getByText("不要"));
    expect(screen.queryByText(/小鲶改了/)).toBeNull();
    expect(screen.getByText("存进知识库")).toBeTruthy();
    expect(api.save).not.toHaveBeenCalled();
  });

  it("小鲶说没改: 不出预览, 只显示它的说明; 出错显示原因", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce({ ...proposal, changed: [], changed_labels: [], reply: "转写里没找到合同履约的讨论" })
      .mockRejectedValueOnce("小鲶没按格式回, 再说一次试试");
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "补上合同履约" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(screen.getByText("转写里没找到合同履约的讨论")).toBeTruthy();
    expect(screen.queryByText(/小鲶改了/)).toBeNull();
    fireEvent.change(box, { target: { value: "再试" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(screen.getByText("小鲶没按格式回, 再说一次试试")).toBeTruthy();
  });

  it("Word / Excel 模版填的表: 不给让小鲶改 (改不了表格)", async () => {
    api.minutes = { json: { ...base, template: { id: "tpl_w", name: "表", kind: "docx" }, output_file: "/m/a.docx" }, markdown: "", path: "" };
    await mount();
    expect(screen.queryByLabelText("让小鲶改纪要")).toBeNull();
  });

  it("11/17 标准格式装不下 (按议题分段): 整份 Markdown 预览, 采用存全文 + 待办", async () => {
    api.minutes = minutes;
    const md = "# 周会\n\n## 一、目前存在的问题\n- 金智达继续辅导";
    api.revise.mockResolvedValueOnce({ ...proposal, restructured: true, markdown: md, changed: ["markdown"], shrink: { before: 38, after: 5 },
      changed_labels: ["整份改成按你说的结构排"], fields: { action_items: proposal.fields.action_items } });
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "分两块写" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(screen.getByText(/按你说的结构整份重新排了/)).toBeTruthy();
    expect(screen.getByText("一、目前存在的问题")).toBeTruthy();
    expect(screen.getByText(/原来 38 条.*改后只看到 5 条/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("采用")); });
    expect(api.save).toHaveBeenCalledWith("mtg_1", { action_items: proposal.fields.action_items }, md);
  });
});

describe("纪要标出是哪个模型写的 (11/17)", () => {
  it("显示模型名; 老纪要没记不显示", async () => {
    api.minutes = { json: { ...base, model: "catfish-public-qwen-flash" }, markdown: "", path: "" };
    await mount();
    expect(await screen.findByText("qwen3.8-flash（公有云） 写的")).toBeTruthy();
    cleanup();
    api.minutes = { json: base, markdown: "", path: "" };
    await mount();
    expect(screen.queryByText(/写的$/)).toBeNull();
  });
});

describe("让小鲶改: 切走再回来不丢 (11/18)", () => {
  const minutes = {
    json: { ...base, action_items: [{ task: "交材料", owner: "说话人2", due: "" }] }, markdown: "# 周会", path: "/x",
  };
  const proposal = {
    reply: "负责人换成王总了。", markdown: null, transcript_full: true, changed: ["action_items"], changed_labels: ["待办"],
    fields: { ...minutes.json, action_items: [{ task: "交材料", owner: "王总", due: "" }] },
    sources: [{ kind: "知识库", title: "王总", where: "wiki/entities/王总.md" }],
  };

  it("没采用就切到别的 tab (整页卸载) 再回来: 提议、说过的话都还在; 查过的资料列出来", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce(proposal);
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "说话人2 是王总" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(screen.getByText(/依据 \(你电脑上的\): 知识库「王总」/)).toBeTruthy();
    cleanup();                                   // 切到工作台: 会议页整个卸载
    await mount();                               // 切回来
    expect(screen.getByText(/小鲶改了: 待办/)).toBeTruthy();
    expect(screen.getByText("负责人换成王总了。")).toBeTruthy();
  });

  it("上网查过 (11/18): 网页单独列出来 (标题 + 网站), 本机资料照旧一行", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce({
      ...proposal,
      sources: [{ kind: "网页", title: "信息安全服务资质认证实施规则", where: "https://www.isccc.gov.cn/a.pdf" },
        ...proposal.sources],
    });
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "去官网查下整改要求" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    const link = screen.getByText("《信息安全服务资质认证实施规则》");
    expect(link.getAttribute("href")).toBe("https://www.isccc.gov.cn/a.pdf");
    expect(screen.getByText(/isccc\.gov\.cn/)).toBeTruthy();
    expect(screen.getByText(/依据 \(你电脑上的\): 知识库「王总」/)).toBeTruthy();
    expect(screen.queryByText(/文件「信息安全服务资质认证实施规则」|网页「/)).toBeNull();
  });

  it("小鲶还在改时切走: 结果回来存下, 切回来就在", async () => {
    api.minutes = minutes;
    let finish: (v: unknown) => void = () => undefined;
    api.revise.mockReturnValueOnce(new Promise((r) => { finish = r; }));
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "说话人2 是王总" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    expect(screen.getByText(/小鲶在查资料.*对照转写改/)).toBeTruthy();
    cleanup();
    await act(async () => { finish(proposal); });   // 页面不在的时候结果回来了
    await mount();
    expect(screen.getByText(/小鲶改了: 待办/)).toBeTruthy();
  });

  it("纪要已经变了 (重新生成 / 手动改过): 旧提议作废, 不往新纪要上套; 打了一半的字留着", async () => {
    api.minutes = minutes;
    api.revise.mockResolvedValueOnce(proposal);
    await mount();
    const box = screen.getByLabelText("让小鲶改纪要");
    fireEvent.change(box, { target: { value: "说话人2 是王总" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    fireEvent.change(screen.getByLabelText("让小鲶改纪要"), { target: { value: "再补一条" } });
    cleanup();
    api.minutes = { ...minutes, json: { ...minutes.json, edited_at: "2026-11-18T09:00:00" } };
    await mount();
    expect(screen.queryByText(/小鲶改了/)).toBeNull();
    expect((screen.getByLabelText("让小鲶改纪要") as HTMLTextAreaElement).value).toBe("再补一条");
  });
});



describe("记住这几条 (11/18)", () => {
  it("纪要做成复习卡, 告诉员工去哪复习; 都有了也说清楚", async () => {
    api.minutes = { json: { ...base, decisions: ["一个月内交整改方案"] }, markdown: "# 周会", path: "/x" };
    cards.mockResolvedValueOnce({ added: 3, skipped: 0 }).mockResolvedValueOnce({ added: 0, skipped: 3 });
    await mount();
    await act(async () => { fireEvent.click(screen.getByText("记住这几条")); });
    expect(cards).toHaveBeenCalledWith("mtg_1");
    expect(screen.getByText(/做了 3 张复习卡, 明天开始在「学习 → 复习」里复习/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("记住这几条")); });
    expect(screen.getByText(/这些要点已经有复习卡了/)).toBeTruthy();
  });
});
