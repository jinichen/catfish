// @vitest-environment jsdom
/** 专家包界面 (10/18): 安装前给看权限摘要, 有问题 / 冲突不让装; 升级多了权限标出来; 市场标「可更新」。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const install = vi.fn();
const market = vi.fn();
vi.mock("../../lib/tauri_experts", () => ({
  expertPackInstall: (p: unknown) => install(p),
  expertPackMarket: () => market(),
  expertPackMine: vi.fn(), expertPackPreview: vi.fn(), expertPackPublish: vi.fn(), expertPackWithdraw: vi.fn(),
  expertChatList: () => Promise.resolve([]),
}));

import { InstallDialog, MarketSection, PermissionSummary } from "./ExpertPackUi";
import type { PackInspect } from "../../lib/tauri_experts";

const PERMS = { read: ["查知识库 / 文档"], local: ["操作你的浏览器"], outward: ["推送到微信"], browser: true,
                sites: ["https://eis.example.com/login"], code: false, web: false };
const INFO: PackInspect = {
  id: "qual", version: "1.0.0", name: "资质专员", description: "处理资质申请", routes: [{ when: "审资质待办", use: "workflow:pack-qual/a" }],
  workflows: ["pack-qual/a"], skills: ["eis-login"], permissions: PERMS, sha256: "x", problems: [], state: "new",
  installed_version: null, expanded: [],
};

beforeEach(() => { install.mockReset(); market.mockReset(); });
afterEach(cleanup);

describe("PermissionSummary", () => {
  it("对外的标出会停下等确认, 网站列出来", () => {
    render(<PermissionSummary p={PERMS} />);
    expect(screen.getByText(/对外 \(跑到时会停下等你确认\)/)).toBeTruthy();
    expect(screen.getByText(/eis\.example\.com/)).toBeTruthy();
  });
});

describe("InstallDialog", () => {
  it("点安装才带 confirmed 去装; 从文件装提示没签名", async () => {
    install.mockResolvedValue({ ok: true, id: "qual", profile_id: "expert-qual", version: "1.0.0", upgraded: false, signature: { status: "unsigned" } });
    const done = vi.fn();
    render(<InstallDialog info={INFO} source={{ path: "/x/q.catfish-expert" }} onClose={() => {}} onDone={done} />);
    expect(screen.getByText(/没有中央签名/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "安装" })); });
    expect(install).toHaveBeenCalledWith({ path: "/x/q.catfish-expert", confirmed: true });
    expect(done.mock.calls[0][0]).toMatch(/装好了/);
  });

  it("没过检查 / 本机同 id 冲突 → 按钮灰掉", () => {
    const { rerender } = render(<InstallDialog info={{ ...INFO, problems: ["包里有员工个人数据"] }} source={{ path: "p" }} onClose={() => {}} onDone={() => {}} />);
    expect((screen.getByRole("button", { name: "安装" }) as HTMLButtonElement).disabled).toBe(true);
    rerender(<InstallDialog info={{ ...INFO, state: "conflict" }} source={{ path: "p" }} onClose={() => {}} onDone={() => {}} />);
    expect((screen.getByRole("button", { name: "安装" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("升级多了权限要标出来", () => {
    render(<InstallDialog info={{ ...INFO, state: "upgrade", installed_version: "0.9.0", expanded: ["新的对外能力: 发收集表"] }}
                          source={{ from: "hub", id: "qual", version: "1.0.0" }} onClose={() => {}} onDone={() => {}} />);
    expect(screen.getByText(/这一版多了: 新的对外能力: 发收集表/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "升级" })).toBeTruthy();
  });
});

describe("MarketSection", () => {
  it("已装旧版标可更新, 已装最新标已装", async () => {
    market.mockResolvedValue({ experts: [
      { id: "qual", version: "1.1.0", name: "资质专员", description: "", status: "approved", scope: "department", departments: ["安全部"],
        submitted_by: "a", submitted_at: "", reviewed_by: "boss", installed_version: "1.0.0", can_upgrade: true, expanded: [] },
      { id: "fin", version: "1.0.0", name: "报销助手", description: "", status: "approved", scope: "company", departments: [],
        submitted_by: "a", submitted_at: "", reviewed_by: "admin", installed_version: "1.0.0", can_upgrade: false },
    ] });
    const onInstall = vi.fn();
    render(<MarketSection onInstall={onInstall} />);
    expect(await screen.findByText(/可更新 \(本机 1\.0\.0\)/)).toBeTruthy();
    expect(screen.getByText("已装")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "升级" }));
    expect(onInstall.mock.calls[0][0].id).toBe("qual");
  });
});
