import { describe, expect, it } from "vitest";

import { connectEntries } from "./connectLink";

describe("接入链接: 内网 IP + 域名 / 公网映射", () => {
  const code = "88DC-A0B2-B193-3921";

  it("内网 IP 跟以前一样: http 落地页, 网关 / 认证按端口推", () => {
    const [ip] = connectEntries(["https://10.0.5.20"], code, "5173");
    expect(ip.internal).toBe(true);
    expect(ip.page).toBe(`http://10.0.5.20:5173/connect.html?web=https%3A%2F%2F10.0.5.20&code=${code}`);
    expect(ip.app).toBe(`catfish://connect?web=https%3A%2F%2F10.0.5.20&code=${code}`);
  });

  it("域名 / 公网映射: 落地页和网关 / 认证全走那个 https 地址", () => {
    const [, dom, nat] = connectEntries(
      ["https://10.0.5.20", "https://catfish.example.com", "https://203.0.113.7:8443"], code, "5173",
    );
    expect(dom.internal).toBe(false);
    const u = new URL(dom.page);
    expect(u.origin).toBe("https://catfish.example.com");
    expect(u.searchParams.get("gw")).toBe("https://catfish.example.com");
    expect(u.searchParams.get("id")).toBe("https://catfish.example.com");
    const a = new URL(nat.app.replace("catfish://", "https://"));
    expect(a.searchParams.get("web")).toBe("https://203.0.113.7:8443");
    expect(a.searchParams.get("gw")).toBe("https://203.0.113.7:8443");
    expect(a.searchParams.get("code")).toBe(code);
  });
});
