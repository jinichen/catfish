/** 11/20 右侧栏打开时窗口向右长出一块 —— 长多少、要不要往左挪 (planGrow)。物理像素, 2 倍屏。 */
import { describe, expect, it } from "vitest";
import { planGrow } from "./windowGrow";

const screen = { x: 0, y: 50, width: 3024, height: 1800 };

describe("planGrow", () => {
  it("右边够: 原地向右长出整块", () => {
    expect(planGrow({ x: 100, y: 60, width: 1800, height: 1300 }, screen, 1120, 80)).toEqual({ grow: 1120, x: 100 });
  });

  it("右边不够: 往左挪, 宽度照样长足", () => {
    // 右边只剩 3024 - (800 + 1600) = 624, 再往左挪 496
    expect(planGrow({ x: 800, y: 60, width: 1600, height: 1300 }, screen, 1120, 80)).toEqual({ grow: 1120, x: 304 });
  });

  it("整块屏幕都不够: 长到占满屏宽, 贴左边", () => {
    expect(planGrow({ x: 500, y: 60, width: 2400, height: 1300 }, screen, 1120, 80)).toEqual({ grow: 624, x: 0 });
  });

  it("已经占满屏宽 / 只能长一点点: 不动窗口", () => {
    expect(planGrow({ x: 0, y: 60, width: 3024, height: 1300 }, screen, 1120, 80)).toBeNull();
    expect(planGrow({ x: 0, y: 60, width: 2980, height: 1300 }, screen, 1120, 80)).toBeNull();
  });

  it("副屏在左边 (坐标是负的) 也按那块屏算", () => {
    const left = { x: -1920, y: 0, width: 1920, height: 1080 };
    expect(planGrow({ x: -1200, y: 100, width: 1000, height: 700 }, left, 560, 40)).toEqual({ grow: 560, x: -1560 });
  });
});
