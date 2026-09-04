import { describe, expect, it } from "vitest";

import {
  emailClientName,
  emailFailureHint,
  emailPlatformFromUserAgent,
} from "./emailPlatformHints";

describe("email platform hints", () => {
  it("detects Windows and guides users to Outlook or Foxmail", () => {
    expect(emailPlatformFromUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"))
      .toBe("windows");
    expect(emailClientName("windows")).toContain("Outlook");
    expect(emailClientName("windows")).toContain("Foxmail");
    expect(emailFailureHint("windows")).toContain("Foxmail");
    expect(emailFailureHint("windows")).not.toContain("Mail.app");
  });

  it("keeps macOS guidance for Mail.app", () => {
    expect(emailPlatformFromUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)"))
      .toBe("macos");
    expect(emailClientName("macos")).toBe("Mail.app");
    expect(emailFailureHint("macos")).toContain("自动化权限");
  });

  it("has neutral guidance for an unknown platform", () => {
    expect(emailPlatformFromUserAgent("Mozilla/5.0 (X11; Linux x86_64)"))
      .toBe("other");
    expect(emailFailureHint("other")).toContain("系统邮件客户端");
  });
});
