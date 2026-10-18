// @vitest-environment jsdom
/** 工作台「找谁」(10/18): 会话记住跟谁聊; 选了专家请求走 /p/<profile>/; 推荐按钮点了才切。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
vi.mock("../../lib/tauri_experts", () => ({ expertChatList: () => list() }));

import { ExpertPicker, ExpertSuggestButton } from "./ExpertPicker";
import {
  __resetExpertChat, currentExpertRoute, noteSessionCreated, noteSessionLoaded, refreshExperts, selectExpert,
} from "../../lib/expertChat";
import { buildHermesProfileUrl } from "../../lib/expertBots";

const QUAL = { profileId: "expert-qual", expertId: "qual", name: "资质专员", description: "管资质", quickPrompts: ["这周待审批"], fixedModel: null, ready: true };

let stored: Map<string, string>;
beforeEach(() => {
  __resetExpertChat();
  list.mockReset();
  stored = new Map();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: { getItem: (k: string) => stored.get(k) ?? null, setItem: (k: string, v: string) => void stored.set(k, v) },
  });
});
afterEach(cleanup);

describe("expertChat", () => {
  it("新会话记住跟谁聊, 打开旧会话切回那个人; 没记录 = 小鲶", async () => {
    list.mockResolvedValue([QUAL]);
    await refreshExperts();
    selectExpert("expert-qual");
    noteSessionCreated("s1");
    selectExpert(null);
    noteSessionCreated("s2");
    noteSessionLoaded("s1");
    expect(currentExpertRoute()).toEqual({ profile: "expert-qual", fixedModel: null, name: "资质专员" });
    noteSessionLoaded("s2");
    expect(currentExpertRoute()).toBeNull();
  });

  it("专家走 hermes 的 /p/<profile>/", () => {
    expect(buildHermesProfileUrl("http://127.0.0.1:8642/", "expert-qual", "/v1/chat/completions", "?x=1"))
      .toBe("http://127.0.0.1:8642/p/expert-qual/v1/chat/completions?x=1");
  });
});

describe("ExpertPicker", () => {
  it("没有专家就不显示", async () => {
    list.mockResolvedValue([]);
    const { container } = render(<ExpertPicker onSwitch={() => {}} />);
    await act(async () => {});
    expect(container.textContent).toBe("");
  });

  it("切到专家 = 选中 + 开新对话", async () => {
    list.mockResolvedValue([QUAL]);
    const onSwitch = vi.fn();
    render(<ExpertPicker onSwitch={onSwitch} />);
    const sel = (await screen.findByLabelText("找谁")) as HTMLSelectElement;
    await act(async () => {});
    fireEvent.change(sel, { target: { value: "expert-qual" } });
    expect(onSwitch).toHaveBeenCalled();
    expect(currentExpertRoute()?.profile).toBe("expert-qual");
  });

  it("推荐按钮点了才切, 并请求开新对话", () => {
    const fired = vi.fn();
    window.addEventListener("catfish:new-chat", fired);
    render(<ExpertSuggestButton resultText={JSON.stringify({ ok: true, suggestion: { profile_id: "expert-qual", name: "资质专员", reason: "它管资质" } })} />);
    expect(currentExpertRoute()).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "切到资质专员" }));
    expect(currentExpertRoute()?.profile).toBe("expert-qual");
    expect(fired).toHaveBeenCalled();
    window.removeEventListener("catfish:new-chat", fired);
  });

  it("不是推荐结果就不画按钮", () => {
    const { container } = render(<ExpertSuggestButton resultText='{"ok": true, "experts": []}' />);
    expect(container.textContent).toBe("");
  });
});
