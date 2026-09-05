import { beforeEach, describe, expect, it, vi } from "vitest";

const { routeMock } = vi.hoisted(() => ({ routeMock: vi.fn() }));
vi.mock("./tauri", () => ({
  expertBotRoute: routeMock,
  expertBotsStatus: vi.fn(),
}));

import { resolveExpertBotRequest } from "./expertBots";

describe("expert bot scenario routing", () => {
  beforeEach(() => routeMock.mockReset());

  it("uses bound profile and resolved fixed model", async () => {
    routeMock.mockResolvedValue({
      enabled: true,
      ready: true,
      profileId: "finance-reviewer",
      model: "catfish-private-main",
      reason: "已就绪",
    });
    const result = await resolveExpertBotRequest({
      baseUrl: "http://127.0.0.1:8642",
      useHermes: true,
      scenario: "email.draft",
      pickerModel: "picker-model",
      query: "?source=email",
    });
    expect(routeMock).toHaveBeenCalledWith("email.draft", "picker-model");
    expect(result.url).toBe(
      "http://127.0.0.1:8642/p/finance-reviewer/v1/chat/completions?source=email",
    );
    expect(result.model).toBe("catfish-private-main");
    expect(result.usingExpertBot).toBe(true);
  });

  it("fails open to default profile and picker model", async () => {
    routeMock.mockResolvedValue({
      enabled: true,
      ready: false,
      profileId: "broken",
      model: "fixed",
      reason: "Profile 缺少 API_SERVER_KEY",
    });
    const result = await resolveExpertBotRequest({
      baseUrl: "http://127.0.0.1:8642/",
      useHermes: true,
      scenario: "briefing.advisor",
      pickerModel: "picker-model",
    });
    expect(result.url).toBe("http://127.0.0.1:8642/v1/chat/completions");
    expect(result.model).toBe("picker-model");
    expect(result.usingExpertBot).toBe(false);
  });
});
