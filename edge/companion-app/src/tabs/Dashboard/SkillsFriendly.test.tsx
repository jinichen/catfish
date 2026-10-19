// @vitest-environment jsdom
/** 10/19 技能页给办公人员看得懂: 定时说人话、步骤链标对外、失败说人话 + 让小鲶看看、场景例句、专家「让小鲶帮我填」。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const startEmailChat = vi.fn();
vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: unknown) => unknown) => sel({ startEmailChat, setActiveTab: vi.fn() }),
}));
const suggest = vi.fn();
vi.mock("../../lib/tauri_experts", () => ({
  expertSuggest: (n: string, d: string) => suggest(n, d),
  expertApply: vi.fn(), expertCatalog: vi.fn(), expertGet: vi.fn(), expertList: vi.fn(), expertPackExport: vi.fn(),
  expertPackInspect: vi.fn(), expertPackInstall: vi.fn(), expertPackPickFile: vi.fn(), expertRemove: vi.fn(), expertSave: vi.fn(),
  expertChatList: () => Promise.resolve([]),
}));
vi.mock("../../hooks/useIdentity", () => ({
  useInstalledSkillsAndMcp: () => ({
    skills: [
      { namespace: "🐟 catfish:department", skills: [{ name: "eis-login", path: "/r/eis-login", title: "登录 EIS 一站式信息门户",
        summary: "说「登录 EIS」「进 EIS」时用", description: "", isProtected: true }] },
      { namespace: "apple", skills: [{ name: "apple-notes", path: "/h/a", title: "apple-notes", summary: "", description: "", isProtected: false }] },
    ], mcps: [], error: null, reload: vi.fn(),
  }),
  useMySkills: () => ({ skills: [{ namespace: "personal", skills: [{}, {}] }], error: null, reload: vi.fn() }),
}));
vi.mock("../../lib/tauri_skill_names", () => ({ backfillNamesOnce: () => Promise.resolve(false), skillNameSet: vi.fn() }));
vi.mock("./SkillsMcpCard", () => ({ default: () => <div>技术清单</div> }));

import { ParamsLine, RunLine, StepChain, askXiaonianPrompt, cronText, toCron } from "./WorkflowParts";
import SkillsLibraryCard, { SCENES } from "./SkillsLibraryCard";
import { Editor } from "./ExpertsCard";
import type { RunSummary } from "../../lib/tauri_workflows";

beforeEach(() => { startEmailChat.mockReset(); suggest.mockReset(); });
afterEach(cleanup);

describe("定时", () => {
  it("选项 ↔ cron ↔ 中文", () => {
    expect(toCron("weekly", "09:30", 1, 1)).toBe("30 9 * * 1");
    expect(cronText("30 9 * * 1")).toBe("每周一 09:30");
    expect(cronText(toCron("daily", "08:00", 0, 1))).toBe("每天 08:00");
    expect(cronText(toCron("weekdays", "18:05", 0, 1))).toBe("工作日 18:05");
    expect(cronText(toCron("monthly", "10:00", 0, 15))).toBe("每月 15 号 10:00");
    expect(cronText("*/5 * * * *")).toBe("*/5 * * * *");
  });
});

describe("工作流卡片", () => {
  it("步骤链: 对外那步带 🔔; 参数一行", () => {
    render(<StepChain chain={[{ id: "a", label: "搜邮件", block: "action", outward: false, each: false },
                              { id: "b", label: "推送到我的微信", block: "action", outward: true, each: false }]} />);
    expect(screen.getByText("搜邮件")).toBeTruthy();
    expect(screen.getByText(/🔔 推送到我的微信/)).toBeTruthy();
    render(<ParamsLine params={[{ key: "kw", label: "搜索关键词", type: "list", default: ["资质", "认证", "审核", "证书"], required: false }]} />);
    expect(screen.getByText(/搜索关键词：资质、认证、审核…/)).toBeTruthy();
  });

  const failed: RunSummary = {
    run_id: "r_1", skill_path: "personal/digest", title: "每周资质邮件摘要 (试跑)", status: "failed", created_at: "2026-10-17T12:19:00+00:00",
    updated_at: "", failed_step: "task", failed_index: 3, error: "结果只有 0 项, 要求至少 1 项",
    error_human: "第 4 步「建任务」只拿到 0 条，要求至少 1 条。", error_fix: "conditions", trigger: "trial", pending: null,
  };

  it("失败: 说人话 + 怎么办 + 原始报错收起 + 两个按钮", () => {
    const onAsk = vi.fn();
    render(<RunLine r={failed} onResume={() => {}} onAsk={onAsk} />);
    expect(screen.getByText("每周资质邮件摘要")).toBeTruthy();
    expect(screen.getByText("试跑")).toBeTruthy();
    expect(screen.getByText(/第 4 步「建任务」只拿到 0 条/)).toBeTruthy();
    expect(screen.getByText(/可以放宽条件再跑/)).toBeTruthy();
    expect(screen.queryByText(/结果只有 0 项/)).toBeNull();
    fireEvent.click(screen.getByText(/原始报错/));
    expect(screen.getByText(/结果只有 0 项/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /让小鲶看看/ }));
    expect(onAsk).toHaveBeenCalled();
  });

  it("让小鲶看看的话带上情况、原始报错和运行记录", () => {
    const p = askXiaonianPrompt(failed);
    expect(p).toContain("工作流「每周资质邮件摘要」");
    expect(p).toContain("第 4 步「建任务」") && expect(p).toContain("结果只有 0 项") && expect(p).toContain("r_1");
  });

  it("跑完了说做成了什么", () => {
    render(<RunLine r={{ ...failed, status: "done", error_human: null, error: null, outcome: "建任务《10/13-10/19 摘要》" }}
                    onResume={() => {}} onAsk={() => {}} />);
    expect(screen.getByText(/✅ 建任务《10\/13-10\/19 摘要》/)).toBeTruthy();
  });
});

describe("技能库默认页", () => {
  it("办公场景 + 部门技能用中文名; hermes 自带的不在这里; 技术清单收在高级里", async () => {
    render(<SkillsLibraryCard />);
    await act(async () => {});
    for (const sc of SCENES) expect(screen.getByText(`${sc.icon} ${sc.title}`)).toBeTruthy();
    expect(screen.getByText("登录 EIS 一站式信息门户")).toBeTruthy();
    expect(screen.queryByText("apple-notes")).toBeNull();
    expect(screen.getByText(/部门 \/ 团队给的技能 1 个 · 你教会的 2 个/)).toBeTruthy();
    expect(screen.queryByText("技术清单")).toBeNull();
    fireEvent.click(screen.getByText(/高级 · 给 IT 看/));
    expect(screen.getByText("技术清单")).toBeTruthy();
  });

  it("点例句 = 带到工作台发给小鲶", async () => {
    render(<SkillsLibraryCard />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /💬 登录 EIS$/ }));
    expect(startEmailChat).toHaveBeenCalledWith(expect.stringMatching(/^scene-/), "登录 EIS");
  });
});

describe("专家编辑", () => {
  const cat = {
    workflows: [{ skill_path: "personal/qual-approve", description: "", title: "资质审批流程", summary: "审 EIS 资质待办", source: "personal" }],
    skills: [{ skill_path: "department/eis-login", description: "", title: "登录 EIS 门户", summary: "说「登录 EIS」时用", source: "department", runnable: true },
             { skill_path: "external/research", description: "", title: "深度调研", summary: "", source: "external", runnable: false }],
    tool_groups: [{ id: "knowledge", label: "查知识库 / 文档" }],
  };
  const blank = { id: "", name: "", description: "", persona: "", quick_prompts: [], workflows: [], skills: [],
                  routes: [{ when: "", use: "" }], checks: [], tool_groups: [], allow_web: false, allow_code: false, model: "inherit", enabled: true };

  it("列表显示中文名 + 标签, 不显示路径", () => {
    render(<Editor initial={blank} isNew cat={cat} onClose={() => {}} onSaved={() => {}} />);
    expect(screen.getByText("资质审批流程")).toBeTruthy();
    expect(screen.getByText("登录 EIS 门户")).toBeTruthy();
    expect(screen.getByText("能自动跑")).toBeTruthy();
    expect(screen.getByText("照说明一步步做")).toBeTruthy();
    expect(screen.queryByText(/department\/eis-login/)).toBeNull();
    expect(screen.getByText(/它接哪些活 \(员工这么说/)).toBeTruthy();
  });

  it("让小鲶帮我填: 名字和简介都填了才能点; 起草结果填进表单", async () => {
    suggest.mockResolvedValue({ suggestion: {
      workflows: ["personal/qual-approve"], skills: ["department/eis-login"], tool_groups: ["knowledge"],
      routes: [{ when: "审资质待办", use: "workflow:personal/qual-approve", ask: ["哪个项目"] }],
      persona: "我是资质专员。", quick_prompts: ["这周有哪些待审批"] } });
    render(<Editor initial={blank} isNew cat={cat} onClose={() => {}} onSaved={() => {}} />);
    const btn = screen.getByRole("button", { name: /让小鲶帮我填/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByPlaceholderText(/名字, 例/), { target: { value: "资质专员" } });
    fireEvent.change(screen.getByPlaceholderText(/一句话: 它管什么/), { target: { value: "处理资质申请审批" } });
    await act(async () => { fireEvent.click(btn); });
    expect(suggest).toHaveBeenCalledWith("资质专员", "处理资质申请审批");
    expect(screen.getByDisplayValue("审资质待办")).toBeTruthy();
    expect((screen.getAllByRole("checkbox")[0] as HTMLInputElement).checked).toBe(true);
    expect(screen.getByDisplayValue("我是资质专员。")).toBeTruthy();
    expect(screen.getByText(/起草好了/)).toBeTruthy();
  });
});
