import { describe, expect, it, vi } from "vitest";

let prefix = "asset://localhost/";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), convertFileSrc: (p: string) => prefix + encodeURIComponent(p) }));

import { assetUrlKeepingDirs, decodeBytes, trimPartialUtf8 } from "./tauri_artifacts";

describe("assetUrlKeepingDirs", () => {
  it("mac: 开头的 / 编成 %2F, 每段单独编码 (相对地址能解析到同目录)", () => {
    prefix = "asset://localhost/";
    const url = assetUrlKeepingDirs("/Users/x/周报 v2/a#1.html");
    expect(url).toBe("asset://localhost/%2FUsers/x/%E5%91%A8%E6%8A%A5%20v2/a%231.html");
    expect(new URL("chart.png", url).href).toBe("asset://localhost/%2FUsers/x/%E5%91%A8%E6%8A%A5%20v2/chart.png");
  });

  it("Windows: 盘符路径按段编码", () => {
    prefix = "http://asset.localhost/";
    expect(assetUrlKeepingDirs("C:\\Users\\x\\a.html")).toBe("http://asset.localhost/C%3A/Users/x/a.html");
  });
});

describe("decodeBytes", () => {
  it("UTF-8 (含 BOM) 直接解", () => {
    expect(decodeBytes(new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode("金额,日期")]))).toBe("金额,日期");
  });

  it("不是合法 UTF-8 的按 GB18030 解 (Windows Excel 另存的 CSV)", () => {
    // 「金额」的 GBK 编码
    expect(decodeBytes(new Uint8Array([0xbd, 0xf0, 0xb6, 0xee]))).toBe("金额");
  });
});

describe("trimPartialUtf8", () => {
  it("末尾半个汉字去掉, 完整的不动", () => {
    const full = new TextEncoder().encode("ab金");
    expect(trimPartialUtf8(full)).toEqual(full);
    expect(Array.from(trimPartialUtf8(full.subarray(0, 4)))).toEqual([0x61, 0x62]);
    expect(Array.from(trimPartialUtf8(full.subarray(0, 3)))).toEqual([0x61, 0x62]);
  });
});
