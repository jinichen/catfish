// @vitest-environment jsdom
/** 11/15 鲶鱼目录: 邮件卡 / 日程卡 / 待办项 / 文件卡 + List 模板 + Slider + 多按钮 画得出来; 邮件卡跳邮件页、
 *  待办勾选进数据模型 (按钮交的 context 里带着)、文件卡只开本机路径; card_update 追加的数据打到原来那张卡上。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const openFile = vi.fn(() => Promise.resolve());
vi.mock("../../lib/tauri_app", () => ({ openFile: (p: string) => openFile(p) }));

import catfish from "./__fixtures__/a2ui_card_catfish.json";
import A2uiCard, { _resetCardCacheForTest, buildCardForTest } from "./A2uiCard";
import { isLocalPath } from "./catfishCardComponents";
import { useUIStore } from "../../store/ui";

const ID = "c_20261115_120000_abcdef";
afterEach(() => { cleanup(); _resetCardCacheForTest(); openFile.mockClear(); });

const built = () => {
  const b = buildCardForTest(ID, catfish);
  if (!("processor" in b)) throw new Error(b.error);
  return b;
};
const data = (path: string) => built().surfaces[0].dataModel.get(path);

const CAT = "https://catfish.local/a2ui/catalogs/catfish/v1.json";
/** 自定义组件直接当根 (jsdom 看不进上游 Lit 组件的 shadow DOM, 整张混排的卡在浏览器里核对) */
const one = (component: Record<string, unknown>, dataModel: Record<string, unknown> = {}) =>
  [{ version: "1.0", createSurface: { surfaceId: "main", catalogId: CAT, components: [{ id: "root", ...component }], dataModel } }];
let n = 0;
const mount = (msgs: unknown[]) => {
  const id = `c_20261115_120000_${(++n).toString(16).padStart(6, "0")}`;
  const r = render(<A2uiCard cardId={id} messages={msgs} interactive submitted={false} />);
  expect(screen.queryByRole("note")).toBeNull();
  return { ...r, id };
};

describe("鲶鱼自定义组件", () => {
  it("SDK 编出来的整张卡 (邮件卡 / 待办 / List 模板 / 文件卡 / 日程 / 滑块 / 两个按钮) 建得起来, 数据模型就位", () => {
    render(<A2uiCard cardId={ID} messages={catfish} interactive submitted={false} />);
    expect(screen.queryByRole("note")).toBeNull();
    expect(data("/items/0/title")).toBe("写周报");
    expect(data("/f/score")).toBe(3);
  });

  it("邮件卡: 主题 / 发件人 / 时间; 「在邮件里打开」→ 切到邮件页并记下要选中的那封", () => {
    mount(one({ component: "EmailCard", emailId: "apple_mail|Google|2610", subject: "报价确认", sender: "王总", date: "11/15 09:30" }));
    expect(screen.getByText("报价确认")).toBeTruthy();
    expect(screen.getByText("王总")).toBeTruthy();
    fireEvent.click(screen.getByText(/在邮件里打开/));
    expect(useUIStore.getState().activeTab).toBe("email");
    expect(useUIStore.getState().consumeMailFocus()).toBe("apple_mail|Google|2610");
    expect(useUIStore.getState().pendingMailFocus).toBeNull();
  });

  it("日程卡: 标题 / 起止 / 地点 / 参会人", () => {
    mount(one({ component: "EventCard", title: "周例会", start: "11/17 10:00", end: "11:00", location: "3 楼", attendees: ["王总", "小李"] }));
    expect(screen.getByText("周例会")).toBeTruthy();
    expect(screen.getByText("11/17 10:00 – 11:00")).toBeTruthy();
    expect(screen.getByText(/王总、小李/)).toBeTruthy();
  });

  it("待办勾上 → 写进绑定的数据路径 (按钮交的 context 绑的就是它)", () => {
    const { id } = mount(one({ component: "TodoItem", title: "交发票", done: { "@path": "/f/invoice" }, due: "11/17" }, { f: { invoice: false } }));
    const box = screen.getByRole("checkbox") as HTMLInputElement;
    expect(box.checked).toBe(false);
    act(() => { fireEvent.click(box); });
    const b = buildCardForTest(id, []);
    if (!("processor" in b)) throw new Error(b.error);
    expect(b.surfaces[0].dataModel.get("/f/invoice")).toBe(true);
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(true);
  });

  it("文件卡点「打开」用系统 app 开本机文件; 网址 / 相对路径不给开", () => {
    mount(one({ component: "FileCard", path: "/Users/x/报价.xlsx" }));
    expect(screen.getByText("报价.xlsx")).toBeTruthy();
    fireEvent.click(screen.getByText("打开"));
    expect(openFile).toHaveBeenCalledWith("/Users/x/报价.xlsx");
    cleanup();
    mount(one({ component: "FileCard", path: "https://evil.example/a.exe", name: "发票" }));
    expect(screen.queryByText("打开")).toBeNull();
    expect(screen.getByText("不是本机文件")).toBeTruthy();
    expect(isLocalPath("C:\\Users\\a\\b.docx")).toBe(true);
    expect(isLocalPath("file:///etc/passwd")).toBe(false);
    expect(isLocalPath("报价.xlsx")).toBe(false);
  });

  it("card_update: 重读到更长的消息, 只把新加的 updateDataModel 打到原来那份上, 员工改过的别的值不丢", () => {
    const b = built();
    b.surfaces[0].dataModel.set("/f/score", 5);           // 员工拖过滑块
    const more = [...catfish, { version: "1.0", updateDataModel: { surfaceId: "main", path: "/f/invoice", value: true } }];
    const again = buildCardForTest(ID, more);
    if (!("processor" in again)) throw new Error(again.error);
    expect(again.processor).toBe(b.processor);
    expect(again.applied).toBe(more.length);
    expect(data("/f/invoice")).toBe(true);
    expect(data("/f/score")).toBe(5);
  });

  it("老卡 (1.2.189 前, 上游基础目录 id) 还画得出来", async () => {
    const meeting = (await import("./__fixtures__/a2ui_card_meeting.json")).default;
    render(<A2uiCard cardId="c_20261115_093012_a1b2c3" messages={meeting} interactive submitted={false} />);
    expect(screen.queryByRole("note")).toBeNull();
  });
});
