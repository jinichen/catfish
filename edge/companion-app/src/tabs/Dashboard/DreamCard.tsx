/** P3.5.1 (6/15 鸿波 Dream Engine): 仪表盘卡 — 员工主动触发长期记忆整理 (蒸馏).
 *
 * 设计 (方案 D, 6/15 鸿波拍):
 *   - 复用 hermes catfish-memory plugin 现有 distill 算法 + cooldown state
 *   - Model 用 companion picker (useChatStore.model), 不引入 Qwen3, 不读 yaml/env
 *   - 跑完 plugin auto 24h 内自然 skip (共享 cooldown state, 零冲突)
 *
 * 11/10: 文案全改中文、不露文件名 (员工不需要知道 employee_journal.md 叫什么);
 * 加 MemoryAlerts —— 日志被清空 / 新结果缩水时蒸馏会停下并记进 state, 这里报警。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  dreamRun,
  dreamStatus,
  formatAgo,
  formatBytes,
  listenDreamProgress,
  reasonText,
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
      setPhase({ kind: "error", msg: "还没选模型, 先去聊天页选一个" });
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
        setPhase({ kind: "error", msg: `没能启动整理: ${out.error ?? "未知原因"}` });
      } else if (out.exitCode !== 0 && out.exitCode !== null) {
        // CLI 内部失败 (theoretically done event 会先到, 这里兜底)
        console.warn("[DreamCard] dream_cli exit code 非零:", out.exitCode);
      }
    } catch (e) {
      setPhase({ kind: "error", msg: `整理出错: ${e}` });
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
        <strong style={{ fontSize: 16 }}>🌙 做梦 · 整理长期记忆</strong>
      </div>

      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 12 }}>
        小鲶每天把工作日志里新增的内容整理进长期记忆 (你是谁、在忙什么、和谁合作、有哪些约定),
        聊天和早安卡都靠它记住你。平时每 24 小时自动整理一次, 想马上整理就点下面的按钮。
        日志只追加不改写, 每天留一份快照; 长期记忆每次覆盖前都先备份。
      </div>

      <MemoryAlerts status={status} />

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
        <span style={{ color: "var(--catfish-text-muted)" }}>用的模型:</span>
        <code style={{ color: "var(--catfish-cyan-bright)" }}>{model || "还没选, 去聊天页选一个"}</code>

        <span style={{ color: "var(--catfish-text-muted)" }}>上次整理:</span>
        <span>
          {formatAgo(status?.lastRunTs ?? null)}
          {status?.lastRunIso && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginLeft: 6 }}>
              ({status.lastRunIso.replace("T", " ").slice(0, 16)})
            </span>
          )}
        </span>

        <span style={{ color: "var(--catfish-text-muted)" }}>长期记忆:</span>
        <span>{formatBytes(status?.distilledBytes ?? 0)}</span>

        <span style={{ color: "var(--catfish-text-muted)" }}>工作日志:</span>
        <span>
          {formatBytes(status?.journalBytes ?? 0)}
          {journalEmpty && (
            <span style={{ color: "var(--catfish-hint-amber-text)", marginLeft: 6, fontSize: 11 }}>
              还没有日志, 先跟小鲶聊几次
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
          {isRunning ? "整理中…" : "🌙 现在整理"}
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
              第 {phase.done} / {phase.total || "?"} 段
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
              {phase.reason
                ? <>✓ {reasonText(phase.reason)}</>
                : <>✓ 整理完成 · {phase.chunks} 段 · 长期记忆 {formatBytes(phase.bytes ?? 0)} · 用时 {(phase.took ?? 0).toFixed(0)} 秒</>}
            </>
          ) : (
            <>✗ 没有整理: {reasonText(phase.reason)}</>
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
          ⚠ {phase.msg}
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
            🔍 诊断信息 ({stderrLines.length} 行, 出问题时给开发看)
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

// ── 子组件: 记忆异常警告 (11/10) ─────────────────────────────────
//
// 11/09 日志被一行代码清空、长期记忆跟着被覆盖成 1.3KB, 当时这张卡只是平静地显示两个小数字。
// 现在蒸馏发现「日志短了一大半」或「新结果小了六成以上」会停下并记进 state, 这里大红字说出来。

const alertBox = {
  marginBottom: 12,
  padding: "8px 12px",
  background: "var(--catfish-hint-amber-bg)",
  border: "1px solid var(--status-error, #dc2626)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12,
  color: "var(--status-error, #dc2626)",
  lineHeight: 1.6,
} as const;

/** 快照文件名是 YYYY-MM-DD.md, 只露日期给员工看 */
function snapshotDay(path: string): string {
  const m = /(\d{4})-(\d{2})-(\d{2})\.md$/.exec(path);
  return m ? `${Number(m[2])} 月 ${Number(m[3])} 日` : "最近一次";
}

export function MemoryAlerts({ status }: { status: DreamStatus | null }) {
  const js = status?.journalShrunk;
  const dr = status?.distilledRefused;
  if (!js && !dr) return null;
  return (
    <>
      {js && (
        <div style={alertBox} role="alert">
          <strong>⚠ 工作日志少了一大半</strong>
          {js.at && <span style={{ marginLeft: 6, opacity: 0.8 }}>({js.at.replace("T", " ").slice(0, 16)} 发现)</span>}
          <div>
            上次整理时日志有 {(js.was_chars ?? 0).toLocaleString()} 字, 现在只剩 {(js.now_chars ?? 0).toLocaleString()} 字,
            多半是被误清空了。自动整理已经停下, 长期记忆没动。
          </div>
          <div>
            {js.snapshot
              ? <>已留有 {snapshotDay(js.snapshot)} 的日志快照, 请找开发恢复。</>
              : <>没有找到日志快照, 请找开发恢复。</>}
          </div>
        </div>
      )}
      {dr && (
        <div style={alertBox} role="alert">
          <strong>⚠ 这次整理结果异常, 没有覆盖长期记忆</strong>
          {dr.at && <span style={{ marginLeft: 6, opacity: 0.8 }}>({dr.at.replace("T", " ").slice(0, 16)})</span>}
          <div>
            新整理出来的只有 {formatBytes(dr.new_bytes ?? 0)}, 原来有 {formatBytes(dr.old_bytes ?? 0)}, 少得太多,
            所以保留了原来的。下次整理正常后这条提示会自动消失。
          </div>
        </div>
      )}
    </>
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
