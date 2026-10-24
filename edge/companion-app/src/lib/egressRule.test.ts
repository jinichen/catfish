import { describe, expect, it } from "vitest";
import { centralSet, classifyEgress, egressHost, egressPurpose } from "./egressRule";

const central = centralSet(["http://10.0.0.5:8999", "https://portal.example.com"]);

describe("数据外发记录标准 (10/24)", () => {
  it("本机不记; 中央只记带内容的; 第三方都记", () => {
    expect(classifyEgress("GET", "http://127.0.0.1:8642/api/catfish/room-link/pending", 0, central)).toBeNull();
    expect(classifyEgress("POST", "http://127.0.0.1:8642/v1/chat/completions", 500, central)).toBeNull();
    expect(classifyEgress("GET", "http://10.0.0.5:8999/api/quota/me", 0, central)).toBeNull();
    expect(classifyEgress("POST", "http://10.0.0.5:8998/token", 80, central)).toBeNull();
    expect(classifyEgress("POST", "http://10.0.0.5:8999/api/proactive/contextual", 120, central)).toBe("content");
    expect(classifyEgress("POST", "http://10.0.0.5:8997/v1/hub/skills/x", 9000, central)).toBe("content");
    expect(classifyEgress("POST", "http://10.0.0.5:8999/v1/chat/completions?catfish_direct=1", 0, central)).toBeNull();
    expect(classifyEgress("GET", "https://wttr.in/Fuzhou", 0, central)).toBe("third_party");
  });

  it("开发机网关就在本机: 本机的中央端口照样算中央, 本机 hermes 不算", () => {
    const dev = centralSet(["http://127.0.0.1:8999"]);
    expect(classifyEgress("POST", "http://127.0.0.1:8999/api/proactive/contextual", 10, dev)).toBe("content");
    expect(classifyEgress("POST", "http://127.0.0.1:8642/v1/chat/completions", 10, dev)).toBeNull();
  });

  it("用途: catfish_source 优先, 其次按路径", () => {
    expect(egressPurpose("http://x:8999/v1/chat/completions?catfish_direct=1&catfish_source=companion-profile")).toBe("员工画像");
    expect(egressPurpose("http://x:8999/api/proactive/contextual")).toBe("主动提醒");
    expect(egressPurpose("http://x:8997/v1/hub/skills/dept")).toBe("发布技能");
    expect(egressHost("https://wttr.in/Fuzhou")).toBe("wttr.in");
  });
});

describe("describeBody: 要存的请求内容", () => {
  it("文本原样, 表单逐项, 二进制只记大小和哈希", async () => {
    const { describeBody } = await import("./egressRule");
    expect(await describeBody('{"a":1}')).toBe('{"a":1}');
    expect(await describeBody(undefined)).toBeUndefined();
    const bin = new Uint8Array([0xff, 0xfe, 0x00]);
    expect(await describeBody(bin)).toMatch(/^\[二进制内容 3 字节, sha256=[0-9a-f]{64}; 未保存原件\]$/);
    const fd = new FormData();
    fd.append("title", "周报");
    fd.append("file", new File([bin], "a.pdf"));
    const out = await describeBody(fd);
    expect(out).toContain("title=周报");
    expect(out).toMatch(/file=\[文件 a\.pdf 3 字节, sha256=/);
  });
});
