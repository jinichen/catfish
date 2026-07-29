export interface HermesBootstrapProgress {
  phase: string;
  /** Older builds emitted `status`; bootstrap v2 emits `state`. */
  status?: string;
  state?: string;
  message: string;
  progress?: number;
  completedSteps?: number;
  totalSteps?: number;
  error?: string;
}

export type HermesBootstrapTone = "hidden" | "running" | "success" | "error";

export interface HermesBootstrapViewState {
  tone: HermesBootstrapTone;
  phase: string;
  message: string;
  progress?: number;
  error?: string;
}

export const HERMES_BOOTSTRAP_HIDDEN: HermesBootstrapViewState = {
  tone: "hidden",
  phase: "",
  message: "",
};

const SUCCESS_STATUSES = new Set(["success", "succeeded", "complete", "completed", "ready"]);
const ERROR_STATUSES = new Set(["error", "failed", "failure"]);
const HIDDEN_STATUSES = new Set(["idle", "skipped", "not-needed", "not_needed"]);

/**
 * Rust normally emits whole percentages. Accept 0..1 fractions too so a backend
 * progress implementation cannot accidentally overflow the visual indicator.
 */
export function normalizeBootstrapProgress(progress?: number): number | undefined {
  if (typeof progress !== "number" || !Number.isFinite(progress)) return undefined;
  const percentage = progress > 0 && progress < 1 ? progress * 100 : progress;
  return Math.round(Math.min(100, Math.max(0, percentage)));
}

/** Keep event vocabulary handling in one tested place; the UI only renders tones. */
export function bootstrapViewStateFromEvent(
  payload: HermesBootstrapProgress,
): HermesBootstrapViewState {
  const status = (payload.status ?? payload.state ?? "running").trim().toLowerCase();
  const phase = payload.phase.trim();
  const stepProgress =
    typeof payload.completedSteps === "number" &&
    Number.isFinite(payload.completedSteps) &&
    typeof payload.totalSteps === "number" &&
    Number.isFinite(payload.totalSteps) &&
    payload.totalSteps > 0
      ? (payload.completedSteps / payload.totalSteps) * 100
      : undefined;
  const progress = normalizeBootstrapProgress(
    payload.progress !== undefined ? payload.progress : stepProgress,
  );

  if (HIDDEN_STATUSES.has(status)) return HERMES_BOOTSTRAP_HIDDEN;

  if (ERROR_STATUSES.has(status) || payload.error) {
    return {
      tone: "error",
      phase,
      message: payload.message.trim() || "运行环境没有准备完成。",
      progress,
      error: payload.error?.trim() || undefined,
    };
  }

  if (SUCCESS_STATUSES.has(status)) {
    return {
      tone: "success",
      phase,
      message: payload.message.trim() || "运行环境已准备完成。",
      progress: 100,
    };
  }

  return {
    tone: "running",
    phase,
    message: payload.message.trim() || "正在准备首次使用所需的运行环境…",
    progress,
  };
}

export function retryingBootstrapState(): HermesBootstrapViewState {
  return {
    tone: "running",
    phase: "retry",
    message: "正在重新准备运行环境…",
  };
}
