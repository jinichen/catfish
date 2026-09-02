import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import {
  bootstrapViewStateFromEvent,
  HERMES_BOOTSTRAP_HIDDEN,
  retryingBootstrapState,
  type HermesBootstrapProgress,
  type HermesBootstrapViewState,
} from "../lib/hermesBootstrap";
import { getHermesBootstrapStatus, reinstallHermesAgent } from "../lib/tauri";

const SUCCESS_VISIBLE_MS = 4_500;

const PHASE_LABELS: Record<string, string> = {
  lock: "检查环境",
  check: "检查环境",
  checking: "检查环境",
  source: "准备文件",
  prepare: "准备文件",
  preparing: "准备文件",
  activate: "切换环境",
  extract: "解压组件",
  extracting: "解压组件",
  venv: "Python",
  "python-deps": "Hermes",
  python: "Python",
  "node-deps": "浏览器组件",
  node: "Node.js",
  chromium: "浏览器组件",
  browser: "浏览器组件",
  hermes: "Hermes",
  config: "本机配置",
  path: "启动入口",
  complete: "验证安装",
  verify: "验证安装",
  verifying: "验证安装",
  finalize: "即将完成",
  finalizing: "即将完成",
  retry: "重新尝试",
  core: "Hermes 核心",
  addons: "附加组件",
};

function phaseLabel(phase: string): string | null {
  if (!phase) return null;
  return PHASE_LABELS[phase.toLowerCase()] ?? null;
}

function SpinnerIcon() {
  return (
    <svg className="hermes-bootstrap__spinner" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" pathLength="100" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="m7.5 12.4 3 3.1 6.3-7" />
    </svg>
  );
}

function ErrorIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 7.5v5.2M12 16.3v.2" />
    </svg>
  );
}

/**
 * Small, global setup surface. It deliberately floats above the shell instead
 * of gating it: first-run installation can take a while, but the rest of the
 * app remains readable and interactive throughout.
 */
export default function HermesBootstrapStatus() {
  const [state, setState] = useState<HermesBootstrapViewState>(HERMES_BOOTSTRAP_HIDDEN);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;

    void listen<HermesBootstrapProgress>("hermes-bootstrap-progress", (event) => {
      if (disposed) return;
      setState(bootstrapViewStateFromEvent(event.payload));
      setRetrying(false);
    })
      .then((stopListening) => {
        if (disposed) stopListening();
        else {
          unlisten = stopListening;
          // setup can emit before React mounts. Fetch the latest snapshot only
          // after listening, so an event racing this request cannot be lost.
          void getHermesBootstrapStatus()
            .then((progress) => {
              if (!disposed && progress) {
                setState(bootstrapViewStateFromEvent(progress));
              }
            })
            .catch((error) => {
              console.debug("[hermes-bootstrap] status snapshot unavailable", error);
            });
        }
      })
      .catch((error) => {
        // Browser preview and unit tests do not provide Tauri's event bridge.
        // Setup UI is optional there, so this should never take down the app.
        console.debug("[hermes-bootstrap] progress listener unavailable", error);
      });

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (state.tone !== "success") return;
    const timer = window.setTimeout(() => setState(HERMES_BOOTSTRAP_HIDDEN), SUCCESS_VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [state.tone, state.message]);

  const retry = async () => {
    if (retrying) return;
    setRetrying(true);
    setState(retryingBootstrapState());
    try {
      await reinstallHermesAgent();
    } catch (error) {
      setState({
        tone: "error",
        phase: "retry",
        message: "重新准备仍未完成。你可以继续使用其他功能，稍后再试。",
        error: error instanceof Error ? error.message : String(error),
      });
      setRetrying(false);
    }
  };

  if (state.tone === "hidden") return null;

  const label = phaseLabel(state.phase);
  const isRunning = state.tone === "running";
  const isError = state.tone === "error";
  const title = isRunning
    ? "正在准备首次使用"
    : isError
      ? "首次设置未完成"
      : "准备完成";
  const diagnostic = state.error && state.error !== state.message ? state.error : null;

  return (
    <aside
      className={`hermes-bootstrap hermes-bootstrap--${state.tone}`}
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      aria-atomic="true"
      aria-label="运行环境安装状态"
    >
      <div className="hermes-bootstrap__icon">
        {isRunning ? <SpinnerIcon /> : isError ? <ErrorIcon /> : <CheckIcon />}
      </div>

      <div className="hermes-bootstrap__content">
        <div className="hermes-bootstrap__heading-row">
          <strong className="hermes-bootstrap__title">{title}</strong>
          {label && <span className="hermes-bootstrap__phase">{label}</span>}
        </div>
        <p className="hermes-bootstrap__message">{state.message}</p>

        {isRunning && (
          <div
            className={`hermes-bootstrap__progress${state.progress === undefined ? " hermes-bootstrap__progress--indeterminate" : ""}`}
            role="progressbar"
            aria-label={label ?? "准备运行环境"}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={state.progress}
          >
            <span
              style={state.progress === undefined ? undefined : { width: `${state.progress}%` }}
            />
          </div>
        )}

        {isRunning && (
          <p className="hermes-bootstrap__hint">你可以先浏览，完成后相关功能会自动就绪。</p>
        )}

        {diagnostic && (
          <details className="hermes-bootstrap__details">
            <summary>查看错误详情</summary>
            <code>{diagnostic}</code>
          </details>
        )}

        {isError && (
          <button
            type="button"
            className="hermes-bootstrap__retry"
            onClick={() => void retry()}
            disabled={retrying}
          >
            {retrying ? "正在重试…" : "重新尝试"}
          </button>
        )}
      </div>
    </aside>
  );
}
