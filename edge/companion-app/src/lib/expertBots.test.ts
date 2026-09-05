import { describe, expect, it } from "vitest";

import { advisorRequestUrlFromStatus, buildHermesProfileUrl } from "./expertBots";

describe("Hermes expert profile URL", () => {
  it("安全编码 profile 并保留 query", () => {
    expect(
      buildHermesProfileUrl(
        "http://127.0.0.1:8642/",
        "advisor team",
        "/v1/chat/completions",
        "?catfish_source=companion-advisor",
      ),
    ).toBe(
      "http://127.0.0.1:8642/p/advisor%20team/v1/chat/completions" +
        "?catfish_source=companion-advisor",
    );
  });

  it("开关关闭或未就绪时沿用 default profile", () => {
    const url = advisorRequestUrlFromStatus({
      baseUrl: "http://127.0.0.1:8642/",
      useHermes: true,
      query: "?catfish_source=companion-advisor",
      status: {
        enabled: false,
        ready: false,
        advisorProfile: "catfish-advisor",
        reason: "功能开关未开启",
      },
    });
    expect(url).toBe(
      "http://127.0.0.1:8642/v1/chat/completions?catfish_source=companion-advisor",
    );
  });

  it("只有 Hermes 启用且 profile ready 才走命名 profile", () => {
    const status = {
      enabled: true,
      ready: true,
      advisorProfile: "catfish-advisor",
      reason: "后台工作参谋已就绪",
    };
    expect(
      advisorRequestUrlFromStatus({
        baseUrl: "http://127.0.0.1:8642",
        useHermes: true,
        query: "?x=1",
        status,
      }),
    ).toBe("http://127.0.0.1:8642/p/catfish-advisor/v1/chat/completions?x=1");
    expect(
      advisorRequestUrlFromStatus({
        baseUrl: "http://127.0.0.1:8999",
        useHermes: false,
        query: "?x=1",
        status,
      }),
    ).toBe("http://127.0.0.1:8999/v1/chat/completions?x=1");
  });
});
