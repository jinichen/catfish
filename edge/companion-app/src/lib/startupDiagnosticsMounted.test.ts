// @vitest-environment jsdom
/** 10/9: 界面起来之后的未捕获异常不许再弹全屏"界面没能启动起来"。 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { installStartupDiagnostics } from "./startupDiagnostics";

function rejection(reason: unknown) {
  const ev = new Event("unhandledrejection") as Event & { reason: unknown };
  ev.reason = reason;
  window.dispatchEvent(ev);
}

beforeAll(() => {
  vi.useFakeTimers();
  installStartupDiagnostics();
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("unhandled rejection after vs. before mount", () => {
  it("after the UI rendered: small dismissible toast, no full-screen overlay", () => {
    document.body.innerHTML = '<div id="root"><main>app</main></div>';
    rejection("Chrome (PID 18388) 45 秒内未就绪");
    expect(document.getElementById("catfish-startup-overlay")).toBeNull();
    const toast = document.getElementById("catfish-bg-error");
    expect(toast?.textContent).toContain("Chrome (PID 18388)");
    toast!.click();
    expect(document.getElementById("catfish-bg-error")).toBeNull();
  });

  it("toast disappears by itself", () => {
    document.body.innerHTML = '<div id="root"><main>app</main></div>';
    rejection(new Error("boom"));
    expect(document.getElementById("catfish-bg-error")).not.toBeNull();
    vi.advanceTimersByTime(12_001);
    expect(document.getElementById("catfish-bg-error")).toBeNull();
  });

  it("before anything rendered: keeps the full-screen startup panel", () => {
    document.body.innerHTML = '<div id="root"></div>';
    rejection(new Error("bootstrap failed"));
    expect(document.getElementById("catfish-startup-overlay")?.textContent).toContain("界面没能启动起来");
  });
});
