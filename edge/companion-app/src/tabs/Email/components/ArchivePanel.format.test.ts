/** 档案体积的人话格式。档案会长到几个 GB, 显示字节数没人读得出来。 */
import { describe, expect, it } from "vitest";

import { formatBytes } from "./ArchivePanel";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [-1, "0 B"],
    [NaN, "0 B"],
    [512, "512 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [1024 * 1024 * 1.4, "1.4 MB"],
    [1024 * 1024 * 847, "847 MB"],
    [1024 ** 3 * 2.5, "2.5 GB"],
  ])("%p → %p", (n, want) => {
    expect(formatBytes(n)).toBe(want);
  });

  it("小数只在小数值上出现", () => {
    /** "1.4 GB" 比 "1 GB" 有用得多; "847.3 MB" 里那个 .3 是噪声。 */
    expect(formatBytes(1024 ** 3 * 1.44)).toBe("1.4 GB");
    expect(formatBytes(1024 * 1024 * 847.3)).toBe("847 MB");
  });
});
