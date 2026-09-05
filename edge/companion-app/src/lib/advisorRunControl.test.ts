import { afterEach, describe, expect, it } from "vitest";

import {
  __resetAdvisorRunForTest,
  advisorRunIsActive,
  beginAdvisorRun,
  cancelBriefingAdvisor,
} from "./advisorRunControl";

afterEach(__resetAdvisorRunForTest);

describe("advisor run control", () => {
  it("停止幂等并真正 abort active signal", () => {
    const run = beginAdvisorRun();
    expect(advisorRunIsActive()).toBe(true);
    expect(cancelBriefingAdvisor()).toBe(true);
    expect(run.signal.aborted).toBe(true);
    expect(cancelBriefingAdvisor()).toBe(false);
  });

  it("旧 run finish 不会清掉新 run", () => {
    const oldRun = beginAdvisorRun();
    const newRun = beginAdvisorRun();
    oldRun.finish();
    expect(advisorRunIsActive()).toBe(true);
    expect(cancelBriefingAdvisor()).toBe(true);
    expect(newRun.signal.aborted).toBe(true);
  });

  it("正常 finish 清理 active state", () => {
    const run = beginAdvisorRun();
    run.finish();
    expect(advisorRunIsActive()).toBe(false);
  });
});
