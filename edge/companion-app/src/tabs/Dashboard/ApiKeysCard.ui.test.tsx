// @vitest-environment jsdom
/** 11/19 鸿波「加一个长期有效的钥匙给 Claude Code 用」: 员工自己生成 (只显示一次) / 列 / 吊销 (应用内确认框)。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), revoke: vi.fn(), confirm: vi.fn() }));
vi.mock("../../lib/apiKeys", async (orig) => ({
  ...(await orig<typeof import("../../lib/apiKeys")>()),
  listApiKeys: api.list, createApiKey: api.create, revokeApiKey: api.revoke,
}));
vi.mock("../../lib/env", () => ({ config: { gatewayUrl: "http://10.0.0.5:8999/" } }));
vi.mock("../../lib/me", () => ({ fetchWithAuth: vi.fn() }));
vi.mock("../../components/dialogs", () => ({ confirmDialog: api.confirm }));

import ApiKeysCard from "./ApiKeysCard";
import { claudeCodeEnv } from "../../lib/apiKeys";

const OLD = { id: "k1", name: "台式机", hint: "cfk_…AbCd", created_at: 1763500000, last_used_at: null };

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.list.mockResolvedValue([OLD]);
});
afterEach(cleanup);

describe("Claude Code 钥匙", () => {
  it("Claude Code 设置: 中央地址 + 钥匙 + 中央模型名", () => {
    expect(claudeCodeEnv("cfk_x", "http://10.0.0.5:8999/")).toBe(
      "export ANTHROPIC_BASE_URL=http://10.0.0.5:8999\nexport ANTHROPIC_AUTH_TOKEN=cfk_x\n" +
      "export ANTHROPIC_MODEL=catfish-auto\nexport ANTHROPIC_SMALL_FAST_MODEL=catfish-auto");
  });

  it("生成: 钥匙和设置只显示这一次, 点「我已保存」就收起", async () => {
    api.create.mockResolvedValue({ ...OLD, id: "k2", name: "笔记本", hint: "cfk_…9xYz", key: "cfk_SECRET9xYz" });
    await act(async () => { render(<ApiKeysCard />); });
    expect(screen.getByText("台式机")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("钥匙名称"), { target: { value: "笔记本" } });
    await act(async () => { fireEvent.click(screen.getByText("生成钥匙")); });
    expect(api.create).toHaveBeenCalledWith("笔记本");
    expect(screen.getByLabelText("新钥匙").textContent).toBe("cfk_SECRET9xYz");
    expect(screen.getByLabelText("Claude Code 设置").textContent).toContain("ANTHROPIC_AUTH_TOKEN=cfk_SECRET9xYz");
    fireEvent.click(screen.getByText("我已保存"));
    expect(screen.queryByText("cfk_SECRET9xYz")).toBeNull();
  });

  it("吊销要确认; 取消不吊销", async () => {
    api.revoke.mockResolvedValue({ revoked: "k1" });
    await act(async () => { render(<ApiKeysCard />); });
    api.confirm.mockResolvedValueOnce(false);
    await act(async () => { fireEvent.click(screen.getByText("吊销")); });
    expect(api.revoke).not.toHaveBeenCalled();
    api.confirm.mockResolvedValueOnce(true);
    await act(async () => { fireEvent.click(screen.getByText("吊销")); });
    expect(api.revoke).toHaveBeenCalledWith("k1");
  });

  it("中央没升级 / 出错时说清楚", async () => {
    api.list.mockRejectedValue(new Error("中央还没升级到支持长期钥匙的版本 (20261119 之后)"));
    await act(async () => { render(<ApiKeysCard />); });
    expect(screen.getByRole("alert").textContent).toContain("中央还没升级");
  });
});
