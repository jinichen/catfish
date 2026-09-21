/**
 * @vitest-environment jsdom
 */
/** 没配好邮件来源时那张引导卡片。
 *
 * 9/21 鸿波: "WINDOWS 都不支持了, 为什么还要扫?" + "不支持滚动, 内容都被遮住了"
 *
 * 这些测试钉的是**信息顺序**, 不是像素。判据只有一条: 员工第一眼看到的,
 * 得是他真正该做的那件事。
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import EmailSourceSetup from "./EmailSourceSetup";

vi.mock("./ImapSetup", () => ({
  default: () => <div data-testid="imap-setup">邮箱直连 (IMAP)</div>,
}));

const OUTLOOK_FAIL =
  "Outlook COM 初始化失败 (hresult=0x800401F3)。这台机器装的是最新版 Outlook " +
  "(Microsoft.OutlookForWindows), 它不提供 COM 自动化接口, 开着也读不了。";
const EML_FAIL = "没有配置邮件目录。请在邮件客户端里把邮件导出为 .eml, 然后在界面上选择导出目录。";

function windowsDiscovery(over: Partial<any> = {}) {
  return {
    platform: "Windows",
    selected_client: null,
    sources: [
      { client: "outlook-win", status: "unavailable", reason: OUTLOOK_FAIL, accounts: [], root: null },
      { client: "eml-dir", status: "unavailable", reason: EML_FAIL, accounts: [], root: null },
    ],
    ...over,
  } as any;
}

const noop = () => {};
const props = {
  busy: false,
  error: null,
  onRescan: noop,
  onSelect: noop,
  onPickMailDirectory: noop,
};

beforeEach(() => vi.clearAllMocks());
// 同一个文件里多次 render 会叠在一起, 不清的话 getByText 撞多个节点
afterEach(cleanup);

describe("Windows 上的顺序", () => {
  it("IMAP 排在所有失败信息前面", () => {
    const { container } = render(
      <EmailSourceSetup discovery={windowsDiscovery()} {...props} />,
    );
    const text = container.textContent ?? "";
    const imapAt = text.indexOf("邮箱直连");
    expect(imapAt).toBeGreaterThanOrEqual(0);

    // 失败原因默认根本不在 DOM 里, 所以"排在后面"这件事是天然成立的;
    // 真正要钉的是它默认不出现。
    expect(text).not.toContain("hresult");
  });

  it("默认不展开 Outlook / Foxmail 的失败原因", () => {
    render(<EmailSourceSetup discovery={windowsDiscovery()} {...props} />);
    expect(screen.queryByText(/hresult/)).toBeNull();
    expect(screen.queryByText(/导出为 \.eml/)).toBeNull();
  });

  it("想看的人点一下能展开 —— 诊断信息没被删掉", () => {
    render(<EmailSourceSetup discovery={windowsDiscovery()} {...props} />);
    fireEvent.click(screen.getByText(/读不到本机 Outlook/));
    expect(screen.getByText(/hresult/)).toBeTruthy();
    expect(screen.getByText(/导出为 \.eml/)).toBeTruthy();
  });

  it("没有任何本地客户端可用时, 不说「暂时没有找到可用邮箱」", () => {
    /** IMAP 就在上面, 员工并没有无路可走。
     *  那句话的作用只是让人以为这软件读不了他的邮件。 */
    const { container } = render(
      <EmailSourceSetup discovery={windowsDiscovery()} {...props} />,
    );
    expect(container.textContent).not.toContain("暂时没有找到可用邮箱");
  });

  it("「重新扫描」不再占据主版面 —— Windows 上扫不出东西是常态", () => {
    /** 9/18 之后: Foxmail 本地解析删了, 新版 Outlook 没有 COM。
     *  把一个大概率扫不出结果的按钮摆在最显眼处, 是在邀请员工反复做无用功。 */
    render(<EmailSourceSetup discovery={windowsDiscovery()} {...props} />);
    expect(screen.queryByRole("button", { name: "重新扫描" })).toBeNull();

    fireEvent.click(screen.getByText(/读不到本机 Outlook/));
    expect(screen.getByRole("button", { name: "重新扫描" })).toBeTruthy();
  });

  it("真有可用的本地客户端时, 它要直接可见, 不藏在折叠里", () => {
    /** 经典 Outlook 还能用。这种机器上不该逼人去点「读不到...?」才发现能用。 */
    const d = windowsDiscovery({
      sources: [
        {
          client: "outlook-win",
          status: "ready",
          reason: null,
          accounts: ["a@x.com", "b@x.com"],
          root: null,
        },
      ],
    });
    render(<EmailSourceSetup discovery={d} {...props} />);
    expect(screen.getByText(/Outlook · 2 个账号/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "使用" })).toBeTruthy();
  });

  it("IMAP 不会在「其他来源」里重复出现一次", () => {
    /** ImapSetup 自己就有完整的已配置/未配置状态。
     *  discovery.sources 里那条 imap 再渲染一遍 = 同一件事说两遍, 而且两处
     *  状态可能不一致。 */
    const d = windowsDiscovery({
      sources: [
        { client: "imap", status: "unavailable", reason: "IMAP 没配置", accounts: [], root: null },
        { client: "outlook-win", status: "unavailable", reason: OUTLOOK_FAIL, accounts: [], root: null },
      ],
    });
    render(<EmailSourceSetup discovery={d} {...props} />);
    fireEvent.click(screen.getByText(/读不到本机 Outlook/));
    expect(screen.queryByText("IMAP 没配置")).toBeNull();
  });
});

describe("非 Windows", () => {
  it("直接就是 IMAP 配置, 没有多余的外壳", () => {
    const d = { platform: "Darwin", selected_client: null, sources: [] } as any;
    render(<EmailSourceSetup discovery={d} {...props} />);
    expect(screen.getByTestId("imap-setup")).toBeTruthy();
  });
});
