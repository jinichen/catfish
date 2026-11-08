import { describe, expect, it } from "vitest";

import { pickIssuer } from "./issuer";

const IP = "https://192.168.31.199";
const DOMAIN = "https://catfish.example.com";
const NAT = "https://203.0.113.7:8443";
const ALL = [IP, DOMAIN, NAT];

describe("pickIssuer (IP + 域名 / 公网映射同时用)", () => {
  it("门户从哪个地址打开就用哪个 issuer", () => {
    expect(pickIssuer(ALL, "https://192.168.31.199")).toBe(IP);
    expect(pickIssuer(ALL, "https://catfish.example.com")).toBe(DOMAIN);
    expect(pickIssuer(ALL, "https://203.0.113.7:8443")).toBe(NAT);
  });

  it("默认端口写不写都一样, 尾斜杠不影响", () => {
    expect(pickIssuer([IP, "https://catfish.example.com:443/"], "https://catfish.example.com")).toBe(
      "https://catfish.example.com:443",
    );
    expect(pickIssuer([`${IP}/`], "https://192.168.31.199:443")).toBe(IP);
  });

  it("端口不同就不是同一个来源", () => {
    expect(pickIssuer(ALL, "https://203.0.113.7")).toBe(IP);
    expect(pickIssuer(ALL, "http://catfish.example.com")).toBe(IP);
  });

  it("对不上 (dev / 没配) 用第一个, 只有一个时跟以前一样", () => {
    expect(pickIssuer(ALL, "http://localhost:5173")).toBe(IP);
    expect(pickIssuer(["http://127.0.0.1:8998"], "http://localhost:5173")).toBe("http://127.0.0.1:8998");
    expect(pickIssuer(["", " "], "https://x")).toBeUndefined();
    expect(pickIssuer(["not a url", IP], "https://192.168.31.199")).toBe(IP);
  });
});
