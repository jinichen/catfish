/** P3.5.1 (6/15 鸿波 Dream Engine): 仪表盘卡 — 员工主动触发 long-term 蒸馏.
 *
 * 设计 (方案 D, 6/15 鸿波拍):
 *   - 复用 hermes catfish-memory plugin 现有 distill 算法 + cooldown state
 *   - Model 用 companion picker (useChatStore.model), 不引入 Qwen3, 不读 yaml/env
 *   - 进度: progress bar + "chunk X / N"
 *   - 上次蒸馏: 时间 + 字节数
 *   - 跑完 plugin auto 24h 内自然 skip (共享 cooldown state, 零冲突)
 *
 * UI:
 *   - 标题 + 一句"做啥的"说明
 *   - 当前 model 显式 (let employee see)
 *   - 上次蒸馏时间 + distilled_facts.md 大小 + employee_journal.md 大小
 *   - "🌙 现在重蒸" 按钮 (在跑时 disable + 显进度)
 *   - 进度条 + chunk count
 *   - 跑完结果 (成功/失败 reason)
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  dreamRun,
  dreamStatus,
  formatAgo,
  formatBytes,
  listenDreamProgress,
  type DreamEvent,
  type DreamStatus,
} from "../../lib/dream";
import { useChatStore } from "../../store/chat";

// ─── 类型 ──────────────────────────────────────────────────────

type Phase =
  | { kind: "idle" }
  | { kind: "running"; done: number; total: number }
  | { kind: "done"; ok: boolean; reason?: string; chunks?: number; bytes?: number; took?: number }
  | { kind: "error"; msg: string };

/** P3.5.1.7 (6/15 鸿波 chunk 0/? 卡住排查): stderr 累积窗口, 诊断用. */
type StderrLine = { ts: number; msg: string };
const MAX_STDERR_LINES = 8;

// ─── 主组件 ────────────────────────────────────────────────────

export default function DreamCard() {
  const model = useChatStore((s) => s.model);
  const [status, setStatus] = useState<DreamStatus | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [stderrLines, setStderrLines] = useState<StderrLine[]>([]);
  const unlistenRef = useRef<(() => void) | null>(null);

  // mount: 拉一次 status
  const refreshStatus = useCallback(async () => {
    try {
      const s = await dreamStatus();
      setStatus(s);
    } catch (e) {
      console.warn("[DreamCard] dreamStatus 失败:", e);
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  // unmount cleanup
  useEffect(() => {
    return () => {
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
    };
  }, []);

  // ── 触发蒸馏 ──
  const handleRun = useCallback(async () => {
    if (phase.kind === "running") return;
    if (!model || !model.trim()) {
      setPhase({ kind: "error", msg: "当前没选 model (chat picker 是空的), 不跑" });
      return;
    }
    setPhase({ kind: "running", done: 0, total: 0 });
    setStderrLines([]);

    // 先挂监听, 再 invoke (防丢 start event)
    try {
      const un = await listenDreamProgress((evt: DreamEvent) => {
        if (evt.event === "start") {
          setPhase({ kind: "running", done: 0, total: evt.total });
        } else if (evt.event === "chunk") {
          setPhase({ kind: "running", done: evt.done, total: evt.total });
        } else if (evt.event === "done") {
          setPhase({
            kind: "done",
            ok: evt.ok,
            reason: evt.reason,
            chunks: evt.chunks,
            bytes: evt.bytes,
            took: evt.took_seconds,
          });
          void refreshStatus();
        } else if (evt.event === "error") {
          setPhase({ kind: "error", msg: evt.msg });
        } else if (evt.event === "stderr") {
          // P3.5.1.7 (6/15 鸿波 chunk 0/? 卡住排查): stderr 累积窗口, 诊断用.
          //   import error / httpx 异常 / gateway token 缺等都会从 stderr 出.
          //   不显示给员工心智干扰, 但 "蒸馏中" 卡 >10s 时折叠显出来给鸿波看错因.
          setStderrLines((prev) => {
            const next = [...prev, { ts: Date.now(), msg: evt.msg }];
            return next.length > MAX_STDERR_LINES ? next.slice(-MAX_STDERR_LINES) : next;
          });
        }
      });
      unlistenRef.current = un;

      const out = await dreamRun(model);
      // dreamRun await 直到 CLI 退出. exit_code 看是否正常.
      if (!out.spawnOk) {
        setPhase({ kind: "error", msg: `spawn 失败: ${out.error ?? "未知"}` });
      } else if (out.exitCode !== 0 && out.exitCode !== null) {
        // CLI 内部失败 (theoretically done event 会先到, 这里兜底)
        console.warn("[DreamCard] dream_cli exit code 非零:", out.exitCode);
      }
    } catch (e) {
      setPhase({ kind: "error", msg: `调用 dream_distill_run 异常: ${e}` });
    } finally {
      // 主路径 await 完才 unlisten — 防 done event 丢
      if (unlistenRef.current) {
        unlistenRef.current();
        unlistenRef.current = null;
      }
    }
  }, [model, phase.kind, refreshStatus]);

  // ─── render ───
  const isRunning = phase.kind === "running";
  const journalEmpty = (status?.journalBytes ?? 0) === 0;

  return (
    <div
      style={{
        padding: "16px 18px",
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        fontSize: 14,
        color: "var(--catfish-text)",
        lineHeight: 1.6,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <strong style={{ fontSize: 16 }}>🌙 Dream Engine</strong>
        <span style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
          重新蒸馏长期记忆 · 用 chat picker 当前模型
        </span>
      </div>

      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 12 }}>
        把累积的对话日志 (<code>employee_journal.md</code>) 重新蒸馏成长期画像
        (<code>distilled_facts.md</code>) — 早安卡 / chat 注入 system prompt 时读这个.
        平时由 hermes plugin 自动每 24h 跑, 这里让你**立即重蒸**, 跑完 plugin 自动 24h skip.
      </div>

      {/* 当前 model + 状态 */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "4px 12px",
          fontSize: 13,
          padding: "8px 12px",
          background: "var(--catfish-bg-cream)",
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-sm)",
          marginBottom: 12,
        }}
      >
        <span style={{ color: "var(--catfish-text-muted)" }}>当前 model:</span>
        <code style={{ color: "var(--catfish-cyan-bright)" }}>{model || "(空, 去 chat tab 选)"}</code>

        <span style={{ color: "var(--catfish-text-muted)" }}>上次蒸馏:</span>
        <span>
          {formatAgo(status?.lastRunTs ?? null)}
          {status?.lastRunIso && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
              ({status.lastRunIso})
            </span>
          )}
        </span>

        <span style={{ color: "var(--catfish-text-muted)" }}>distilled_facts.md:</span>
        <span>{formatBytes(status?.distilledBytes ?? 0)}</span>

        <span style={{ color: "var(--catfish-text-muted)" }}>employee_journal.md:</span>
        <span>
          {formatBytes(status?.journalBytes ?? 0)}
          {journalEmpty && (
            <span style={{ color: "var(--catfish-hint-amber-text)", marginLeft: 6, fontSize: 11 }}>
              ⚠ journal 是空, 没什么可蒸馏 (先跟 catfish 聊几次累积一下)
            </span>
          )}
        </span>
      </div>

      {/* 按钮 + 进度 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={handleRun}
          disabled={isRunning || !model || journalEmpty}
          style={{
            padding: "8px 16px",
            background: isRunning
              ? "var(--catfish-text-muted)"
              : "var(--catfish-cyan)",
            color: "white",
            border: "none",
            borderRadius: "var(--radius-sm)",
            fontSize: 13,
            cursor: isRunning || !model || journalEmpty ? "not-allowed" : "pointer",
            opacity: !model || journalEmpty ? 0.6 : 1,
          }}
        >
          {isRunning ? "蒸馏中..." : "🌙 现在重蒸"}
        </button>

        {isRunning && (
          <div style={{ flex: 1, minWidth: 200 }}>
            <ProgressBar done={phase.done} total={phase.total} />
            <div
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginTop: 4,
              }}
            >
              chunk {phase.done} / {phase.total || "?"}
            </div>
          </div>
        )}
      </div>

      {/* 终态显示 */}
      {phase.kind === "done" && (
        <div
          style={{
            marginTop: 12,
            padding: "8px 12px",
            background: phase.ok
              ? "var(--catfish-hint-blue-bg)"
              : "var(--catfish-hint-amber-bg)",
            border: `1px solid ${
              phase.ok ? "var(--catfish-hint-blue-border)" : "var(--catfish-hint-amber-border)"
            }`,
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            color: phase.ok
              ? "var(--catfish-hint-blue-text)"
              : "var(--catfish-hint-amber-text)",
          }}
        >
          {phase.ok ? (
            <>
              ✓ 蒸馏完成 · {phase.chunks} 段 · 写 {formatBytes(phase.bytes ?? 0)} ·{" "}
              耗时 {(phase.took ?? 0).toFixed(1)}s
            </>
          ) : (
            <>✗ 蒸馏未跑 — 原因: {phase.reason || "未知"}</>
          )}
        </div>
      )}

      {phase.kind === "error" && (
        <div
          style={{
            marginTop: 12,
            padding: "8px 12px",
            background: "var(--catfish-hint-amber-bg)",
            border: "1px solid var(--catfish-hint-amber-border)",
            borderRadius: "var(--radius-sm)",
            fontSize: 12,
            color: "var(--catfish-hint-amber-text)",
          }}
        >
          ⚠ 错误: {phase.msg}
        </div>
      )}

      {/* P3.5.1.7: stderr 折叠诊断 — 进度卡 >10s 或 error 时给鸿波看 import 错 / gateway token 缺等 */}
      {stderrLines.length > 0 && (
        <details
          style={{
            marginTop: 12,
            fontSize: 11,
            color: "var(--catfish-text-muted)",
          }}
        >
          <summary style={{ cursor: "pointer" }}>
            🔍 dream_cli stderr ({stderrLines.length} 行, 诊断用)
          </summary>
          <pre
            style={{
              marginTop: 6,
              padding: "6px 8px",
              background: "var(--catfish-bg)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              fontSize: 10,
              fontFamily: "var(--font-mono)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              maxHeight: 160,
              overflow: "auto",
            }}
          >
            {stderrLines.map((l) => `[${new Date(l.ts).toLocaleTimeString()}] ${l.msg}`).join("\n")}
          </pre>
        </details>
      )}
    </div>
  );
}

// ── 子组件: progress bar ────────────────────────────────────────

function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div
      style={{
        height: 8,
        background: "var(--catfish-bg-cream)",
        border: "1px solid var(--catfish-border)",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          width: `${pct}%`,
          height: "100%",
          background: "var(--catfish-cyan-bright)",
          transition: "width 0.3s ease",
        }}
      />
    </div>
  );
}
