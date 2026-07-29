import { describe, expect, it } from "vitest";

import {
  bootstrapViewStateFromEvent,
  normalizeBootstrapProgress,
  retryingBootstrapState,
} from "./hermesBootstrap";

describe("Hermes bootstrap progress", () => {
  it("maps an in-progress event to a non-blocking running state", () => {
    expect(
      bootstrapViewStateFromEvent({
        phase: "python",
        status: "running",
        message: "正在安装 Python",
        progress: 42,
      }),
    ).toEqual({
      tone: "running",
      phase: "python",
      message: "正在安装 Python",
      progress: 42,
    });
  });

  it("supports fractional progress and clamps invalid ranges", () => {
    expect(normalizeBootstrapProgress(0.625)).toBe(63);
    expect(normalizeBootstrapProgress(130)).toBe(100);
    expect(normalizeBootstrapProgress(-4)).toBe(0);
    expect(normalizeBootstrapProgress(Number.NaN)).toBeUndefined();
  });

  it("accepts bootstrap v2 state and derives progress from completed steps", () => {
    expect(
      bootstrapViewStateFromEvent({
        phase: "python-deps",
        state: "running",
        message: "正在安装 Hermes 核心组件",
        completedSteps: 2,
        totalSteps: 8,
      }),
    ).toEqual({
      tone: "running",
      phase: "python-deps",
      message: "正在安装 Hermes 核心组件",
      progress: 25,
    });
  });

  it("keeps an explicit zero progress instead of replacing it with step progress", () => {
    expect(
      bootstrapViewStateFromEvent({
        phase: "source",
        state: "running",
        message: "正在准备",
        progress: 0,
        completedSteps: 4,
        totalSteps: 8,
      }).progress,
    ).toBe(0);
  });

  it("keeps a friendly error message and separate diagnostic detail", () => {
    expect(
      bootstrapViewStateFromEvent({
        phase: "hermes",
        status: "failed",
        message: "运行环境没有准备完成。",
        error: "archive checksum mismatch",
      }),
    ).toEqual({
      tone: "error",
      phase: "hermes",
      message: "运行环境没有准备完成。",
      progress: undefined,
      error: "archive checksum mismatch",
    });
  });

  it("treats completion as 100 percent and hides skipped work", () => {
    expect(
      bootstrapViewStateFromEvent({
        phase: "complete",
        status: "completed",
        message: "准备完成",
      }),
    ).toMatchObject({ tone: "success", progress: 100 });

    expect(
      bootstrapViewStateFromEvent({
        phase: "check",
        state: "skipped",
        message: "已安装",
      }).tone,
    ).toBe("hidden");
  });

  it("creates an immediate optimistic state when retrying", () => {
    expect(retryingBootstrapState()).toMatchObject({
      tone: "running",
      phase: "retry",
    });
  });
});
