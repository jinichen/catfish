// @vitest-environment jsdom
/** MCP 试用订阅 (11/08): 哪些订阅算试用、不在待审了自动退订; 没测试账号的不能试; 403 原话显示。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchWithAuth = vi.hoisted(() => vi.fn());
vi.mock("../../lib/me", async (orig) => ({ ...(await orig<typeof import("../../lib/me")>()), fetchWithAuth }));
vi.mock("../../lib/env", () => ({ config: { backendUrl: "http://gw", gatewayUrl: "http://gw" } }));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import { PendingMcpSection, rememberTrialSub, syncMcpTrials, trialSubscriptions } from "./McpTrialUi";

const SUB = { id: "jira-test", name: "Jira (测试)", version: "1.0.0", description: "看工单", auth_type: "api_key", tools: ["jira_search"],
              scope: "department" as const, departments: ["研发部"], trial_credentials: "测试账号找 IT 要: it@x", submitted_by: "bob@x",
              submitted_at: "", status: "pending" };

beforeEach(() => { fetchWithAuth.mockReset(); installMemoryLocalStorage(); });
afterEach(cleanup);

describe("trialSubscriptions / syncMcpTrials", () => {
  it("本机记过的、或不在已上架列表里的订阅算试用; 对应提交不在待审了就退订", async () => {
    rememberTrialSub("jira-test", "s1");
    const subs = [
      { id: "s1", connector_id: "jira-test", status: "active" },
      { id: "s2", connector_id: "gitlab", status: "active" },
      { id: "s3", connector_id: "unknown-pending", status: "active" },
    ];
    const registry = new Set(["gitlab", "jira-test"]);
    expect(trialSubscriptions(subs, registry).map((s) => s.id)).toEqual(["s1", "s3"]);
    const unsub = vi.fn(async () => true);
    const gone = await syncMcpTrials(subs, registry, new Set(["unknown-pending"]), unsub);
    expect(gone).toEqual(["jira-test"]);
    expect(unsub).toHaveBeenCalledWith("s1");
    expect(trialSubscriptions(subs, registry).map((s) => s.id)).toEqual(["s3"], "退掉的不再记成试用 (jira-test 已上架)");
  });
});

describe("PendingMcpSection", () => {
  it("有测试账号的能试用订阅, 记下订阅号; 没写的按钮灰掉", async () => {
    fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ next_step: "ready", subscription: { id: "s9" } }) });
    const changed = vi.fn();
    render(<PendingMcpSection items={[SUB, { ...SUB, id: "no-cred", name: "没账号的", trial_credentials: "" }]} trialSubs={new Map()} onChanged={changed} />);
    expect(screen.getByText(/测试账号: 测试账号找 IT 要/)).toBeTruthy();
    expect(screen.getByText(/提交人没写测试账号怎么拿/)).toBeTruthy();
    const btns = screen.getAllByRole("button", { name: "试用订阅" }) as HTMLButtonElement[];
    expect(btns[1].disabled).toBe(true);
    await act(async () => { fireEvent.click(btns[0]); });
    expect(fetchWithAuth.mock.calls[0][0]).toBe("http://gw/v1/mcp/subscribe");
    expect(JSON.parse(fetchWithAuth.mock.calls[0][1].body)).toEqual({ connector_id: "jira-test", trial: true });
    expect(changed.mock.calls[0][0]).toMatch(/已试用订阅 Jira \(测试\)/);
    expect(JSON.parse(localStorage.getItem("catfish.mcpTrialSubs") || "{}")).toEqual({ "jira-test": "s9" });
  });

  it("中央 403 (没测试账号 / 不是审批人) 原话显示", async () => {
    fetchWithAuth.mockResolvedValue({ ok: false, status: 403, json: async () => ({ detail: "这个连接器不能试用: 提交人没写测试账号怎么拿" }) });
    render(<PendingMcpSection items={[SUB]} trialSubs={new Map()} onChanged={() => {}} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试用订阅" })); });
    expect(screen.getByText(/不能试用: 这个连接器不能试用: 提交人没写测试账号怎么拿/)).toBeTruthy();
  });

  it("已在试用的带「试用」标, 不再给按钮", () => {
    render(<PendingMcpSection items={[SUB]} trialSubs={new Map([["jira-test", { id: "s1", connector_id: "jira-test", status: "active" }]])}
                              onChanged={() => {}} />);
    expect(screen.getByText("试用")).toBeTruthy();
    expect(screen.getByText("试用订阅中")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "试用订阅" })).toBeNull();
  });
});
