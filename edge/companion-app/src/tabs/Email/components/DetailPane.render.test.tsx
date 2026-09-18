/**
 * @vitest-environment jsdom
 */
/** 详情页渲染出来的东西, 得是邮件本身。
 *
 * # 复现的是什么
 *
 * 9/18 加「显示图片」提示条时, 这段从三元表达式变成了 JSX 子节点:
 *
 *     ) : msg.body_html ? (
 *   -    // 这里原来是 JS 表达式位置, C 风格注释就是注释
 *   +    <>
 *   +      {提示条}
 *   +      // 这里成了 JSX 子节点, C 风格注释是**字面文本**
 *          <iframe .../>
 *
 * 于是那段 "P3.5.31 (6/17): HTML 邮件 iframe srcdoc render…" 的源码注释,
 * 原样显在了邮件正文上面。鸿波截图逮到的。
 *
 * TypeScript 一声不吭 —— 语法完全合法, 就是一段文本。eslint 的
 * react/jsx-no-comment-textnodes 专治这个, 但这个 app 没配 lint, 现在引
 * 进来会翻出几百条历史违规。所以在这里直接断言"用户看到什么"。
 *
 * # 这个文件钉什么
 *
 *   ① 正文区域不许出现源码注释残渣
 *   ② 外链图默认不加载, 且**要给一句话**, 不留空洞
 *   ③ 「显示图片」是每封单独决定的, 换一封不许继承上一封的决定
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

vi.mock("../../../lib/tauri", () => ({
  emailDeleteMessage: vi.fn(async () => undefined),
  emailPhishingGet: vi.fn(async () => null),
  emailPoliticalScanNow: vi.fn(async () => null),
  emailExportAttachment: vi.fn(async () => ""),
  openFile: vi.fn(async () => undefined),
}));
vi.mock("../../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) =>
    sel({ name: "小鲶", personality: "" }),
}));

import DetailPane from "./DetailPane";

const base = {
  account: "Chinatelecom",
  folder: "Inbox",
  subject: "【网信安预警2026年第071期】请关闭技能自进化",
  sender: "信息安全中心 <ff_nic@chinatelecom.cn>",
  recipients: ["ff_allgroup@chinatelecom.cn"],
  cc: [],
  date: "2026-09-18T11:37:04",
  is_read: true,
  has_attachments: false,
  attachments: [],
  body_text: "各位领导同事，大家好！",
  message_id: "<x@chinatelecom.cn>",
  in_reply_to: null,
  references: null,
};

function mount(over: Record<string, unknown>) {
  return render(
    <DetailPane
      msg={{ ...base, ...over } as never}
      list={[]}
      onAskCatfish={() => {}}
      onDeleted={() => {}}
    />,
  );
}

afterEach(() => cleanup());

// ─────────────────────────────────────────────────────────────
// ① 别把源码显给员工
// ─────────────────────────────────────────────────────────────

describe("正文区域不许出现源码注释", () => {
  it("HTML 邮件: 渲染出来的文本里不该有注释残渣", () => {
    const { container } = mount({
      id: "apple_mail|Chinatelecom|2610",
      body_html: "<p>各位领导同事，大家好！</p>",
    });
    const text = container.textContent || "";
    // iframe 的内容不在 container.textContent 里, 所以这里看到的任何
    // "srcdoc"/"P3.5" 字样都只可能来自漏掉花括号的源码注释。
    expect(text).not.toContain("P3.5");
    expect(text).not.toContain("srcdoc");
    expect(text).not.toContain("/*");
  });

  it("纯文本邮件那条分支同理", () => {
    const { container } = mount({ id: "x|y|1", body_html: "" });
    const text = container.textContent || "";
    expect(text).not.toContain("P3.5");
    expect(text).not.toContain("/*");
  });
});

// ─────────────────────────────────────────────────────────────
// ②③ 外链图
// ─────────────────────────────────────────────────────────────

describe("外链图默认不加载", () => {
  const withRemote = {
    id: "apple_mail|Chinatelecom|2610",
    body_html: '<p>正文</p><img src="https://tracker.example.com/p.gif">',
  };

  it("有外链图就给一句话, 不留空洞", () => {
    mount(withRemote);
    expect(screen.getByText(/外部图片未加载/)).toBeTruthy();
    expect(screen.getByText("显示图片")).toBeTruthy();
  });

  it("没有外链图就不打扰", () => {
    mount({ id: "x|y|2", body_html: '<p>纯文字</p><img src="data:image/gif;base64,AA">' });
    expect(screen.queryByText(/外部图片未加载/)).toBeNull();
  });

  it("点了「显示图片」这封就放行, 提示条收起", () => {
    mount(withRemote);
    fireEvent.click(screen.getByText("显示图片"));
    expect(screen.queryByText(/外部图片未加载/)).toBeNull();
  });

  it("换一封邮件不许继承上一封的决定", () => {
    // 这是隐私红线: 对 A 点过"显示图片", 不该让 B 的跟踪像素也跟着发出去。
    // 组件里存的是"对哪一封点过"而不是一个布尔 —— 就是为了让这条在结构上
    // 不可能出错。
    const { rerender } = mount(withRemote);
    fireEvent.click(screen.getByText("显示图片"));
    expect(screen.queryByText(/外部图片未加载/)).toBeNull();

    rerender(
      <DetailPane
        msg={{ ...base, ...withRemote, id: "apple_mail|Chinatelecom|9999" } as never}
        list={[]}
        onAskCatfish={() => {}}
        onDeleted={() => {}}
      />,
    );
    expect(
      screen.queryByText(/外部图片未加载/),
      "换了一封邮件, 上一封的「显示图片」还生效着",
    ).toBeTruthy();
  });
});
