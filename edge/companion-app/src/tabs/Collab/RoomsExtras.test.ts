import { describe, expect, it } from "vitest";

import { daysLabel, highlight, retentionChoices } from "./RoomsExtras";

describe("RoomsExtras", () => {
  it("retentionChoices 只给比上限短的", () => {
    expect(retentionChoices(180)).toEqual([7, 14, 30, 60, 90]);
    expect(retentionChoices(7)).toEqual([]);
    expect(retentionChoices(3650).at(-1)).toBe(1825);
  });

  it("daysLabel 整年显示年", () => {
    expect(daysLabel(365)).toBe("1 年");
    expect(daysLabel(730)).toBe("2 年");
    expect(daysLabel(180)).toBe("180 天");
  });

  it("highlight 不分大小写, 多处命中", () => {
    expect(highlight("报价 Excel 和 excel 表", "EXCEL")).toEqual(["报价 ", { hit: "Excel" }, " 和 ", { hit: "excel" }, " 表"]);
    expect(highlight("没有", "x")).toEqual(["没有"]);
    expect(highlight("abc", "")).toEqual(["abc"]);
    expect(highlight("aa", "a")).toEqual([{ hit: "a" }, { hit: "a" }]);
  });
});
