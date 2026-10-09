// @vitest-environment jsdom
/** 10/9: 中央地址变更被踢下线时, 登录页要说清楚原因, 而不是只有一个「登录」按钮。 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ANONYMOUS_AUTH, type AuthState } from "../types/auth";

let authState: AuthState = ANONYMOUS_AUTH;
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ state: authState, loading: false, error: null, login: vi.fn() }),
}));
vi.mock("../hooks/useServerReachable", () => ({
  useServerReachable: () => ({ checking: false, lastCheckedAt: 1, reachable: true }),
}));
vi.mock("./ServerSetupCard", () => ({ default: () => null }));

import LoginGate from "./LoginGate";

afterEach(cleanup);

describe("LoginGate notice", () => {
  it("shows the central-address-changed notice above the login button", () => {
    authState = { ...ANONYMOUS_AUTH, notice: "中央服务地址已变更（https://a → https://b），原登录已失效，请重新登录。" };
    render(<LoginGate>app</LoginGate>);
    expect(screen.getByRole("status").textContent).toContain("中央服务地址已变更");
    expect(screen.getByText("登录")).toBeTruthy();
  });

  it("shows no notice box on a normal logged-out start", () => {
    authState = ANONYMOUS_AUTH;
    render(<LoginGate>app</LoginGate>);
    expect(screen.queryByRole("status")).toBeNull();
  });
});
