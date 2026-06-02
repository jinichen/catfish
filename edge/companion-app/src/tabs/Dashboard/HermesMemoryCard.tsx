/** Dashboard 卡 — 我的 hermes memory (BL-DASHBOARD-HERMES-MEMORY-CARD, 5/16).
 *
 * 显示 hermes 0.13 真活的 memory 文件内容:
 *   ~/.hermes/memories/USER.md   (target=user 写的: 员工身份/关系/偏好)
 *   ~/.hermes/memories/MEMORY.md (target=memory 写的: 项目/技术事实)
 *
 * 跟 RelationCard / UserProfileCard 同 section ("鲶鱼对你的认识"):
 *   - RelationCard: 对话主题日记 (catfish 自家 summarizer 写的)
 *   - UserProfileCard: 9 字段结构化画像 (catfish_user_profile_* 工具写的)
 *   - HermesMemoryCard (本): hermes memory 工具写的自由文本事实
 *
 * 5/16 关联背景:
 *   - BL-MEMORY-BRIDGE-STORE 修通 hermes memory 工具, 落盘真活了
 *   - BL-SOUL-MEMORY-TARGET-ROUTING 教 LLM target=user/memory 二分
 *   - 但 Dashboard 之前没 UI 入口, 员工要 cat 文件才看到
 */

import { useEffect, useState } from "react";

import {
  hermesMemoryRead,
  toolBridgeCallTool,
  type HermesMemoryView,
} from "../../lib/tauri";

const REFRESH_MS = 30_000;  // 30s polling 跟其他卡一致

/** BL-MEMORY-EDIT-UI (5/16 P0): 删 hermes memory entry.
 * 走 toolBridgeCallTool memory(action=remove), 复用 BL-MEMORY-BRIDGE-STORE 全链路.
 * hermes memory_tool 内部 atomic_replace + file lock 保证安全. */
async function removeEntry(
  target: "user" | "memory",
  entryText: string,
): Promise<void> {
  await toolBridgeCallTool("memory", {
    action: "remove",
    target,
    old_text: entryText,
  });
}

/** BL-MEMORY-A3 (2026-06-03): replace hermes memory entry.
 * 用于 dedupe verdict=same 真合并 — 删 shorter + replace longer 内容. */
async function replaceEntry(
  target: "user" | "memory",
  oldText: string,
  newContent: string,
): Promise<void> {
  await toolBridgeCallTool("memory", {
    action: "replace",
    target,
    old_text: oldText,
    content: newContent,
  });
}

/** BL-MEMORY-A3: dedupe suggestion 真 schema (跟 memory_dedupe Python 真返一致). */
interface DedupeSuggestion {
  target: "user" | "memory";
  entries: [string, string];
  similarity: number;
  llm_verdict: "same" | "related" | null;
  llm_reason?: string;
  suggested_keep: string | null;
  suggested_remove: string | null;
  suggested_merge: string | null;
  hint?: string;
}

interface ProposeSkillHint {
  target: "user" | "memory";
  entry_text: string;
  proposed_skill_name: string;
  reason: string;
}

export default function HermesMemoryCard() {
  const [view, setView] = useState<HermesMemoryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);  // 哪条正在删
  // BL-MEMORY-EDIT-UI fix (5/16): Tauri webview 默认禁 native confirm(), 改 inline
  // 二次点击 — 点第 1 次 🗑 进 confirming 态 (按钮变红 ✓), 点第 2 次真删.
  const [confirming, setConfirming] = useState<string | null>(null);
  // BL-MEMORY-A3 (2026-06-03): dedupe state
  const [dedupeSuggestions, setDedupeSuggestions] = useState<DedupeSuggestion[] | null>(null);
  const [proposeHints, setProposeHints] = useState<ProposeSkillHint[]>([]);
  const [dedupeRunning, setDedupeRunning] = useState(false);
  const [dedupeError, setDedupeError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);  // 哪条 suggestion 在 apply

  const load = async () => {
    try {
      const v = await hermesMemoryRead();
      setView(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleRemove = async (target: "user" | "memory", entry: string) => {
    // 第 1 次点 → 进 confirming 态
    if (confirming !== entry) {
      setConfirming(entry);
      // 3 秒后自动 reset confirming, 防误存
      setTimeout(() => {
        setConfirming((cur) => (cur === entry ? null : cur));
      }, 3000);
      return;
    }
    // 第 2 次点 → 真删
    setConfirming(null);
    setRemoving(entry);
    try {
      await removeEntry(target, entry);
      await load();  // 刷新
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRemoving(null);
    }
  };

  // BL-MEMORY-A3 (2026-06-03): 真触发 catfish_memory_dedupe + 解析 suggestions
  const runDedupe = async () => {
    setDedupeRunning(true);
    setDedupeError(null);
    setDedupeSuggestions(null);
    setProposeHints([]);
    try {
      const resp = await toolBridgeCallTool("catfish_memory_dedupe", {
        threshold: 0.6,
        prefilter_threshold: 0.4,
      });
      if (!resp.ok) {
        throw new Error(resp.error ?? "dedupe 真撞错");
      }
      // resp.result 真是 Python dict, type unknown — 真 cast
      const r = (resp.result ?? {}) as {
        suggestions?: DedupeSuggestion[];
        propose_skill_hints?: ProposeSkillHint[];
        suggestions_count?: number;
      };
      setDedupeSuggestions(r.suggestions ?? []);
      setProposeHints(r.propose_skill_hints ?? []);
    } catch (e) {
      setDedupeError(e instanceof Error ? e.message : String(e));
    } finally {
      setDedupeRunning(false);
    }
  };

  // BL-MEMORY-A3: 真 apply 1 条 suggestion (verdict=same): 删 shorter + replace longer 真合并
  const applySuggestion = async (s: DedupeSuggestion) => {
    if (s.llm_verdict !== "same" && s.llm_verdict !== null) return;  // related/different 不动
    if (!s.suggested_keep || !s.suggested_remove) return;
    const key = `${s.target}-${s.entries[0].slice(0, 30)}`;
    setApplying(key);
    try {
      // 1. 删 shorter
      await removeEntry(s.target, s.suggested_remove);
      // 2. replace longer (verdict=same 真 merge 就是 longer 不变)
      if (s.suggested_merge && s.suggested_merge !== s.suggested_keep) {
        await replaceEntry(s.target, s.suggested_keep, s.suggested_merge);
      }
      // 3. 刷新 entries + 真从 suggestions 移掉这条
      await load();
      setDedupeSuggestions(
        (cur) => cur?.filter((x) => x !== s) ?? null,
      );
    } catch (e) {
      setDedupeError(e instanceof Error ? e.message : String(e));
    } finally {
      setApplying(null);
    }
  };

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  // 计算总字符数 (估算 usage)
  const userChars = (view?.user_entries ?? []).reduce(
    (acc, e) => acc + e.length, 0,
  );
  const memoryChars = (view?.memory_entries ?? []).reduce(
    (acc, e) => acc + e.length, 0,
  );

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        // 跟 RelationCard / UserProfileCard 同 height (5/16 V4 终版)
        height: "min(480px, 50vh)",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          marginBottom: "var(--space-2)",
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div>
          <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
            🧠 我的 hermes memory
          </h3>
          {/* 6/1 鸿波 ABBB: 改成员工能懂的语言, 删 dev 术语 (target=user, memory). */}
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 2 }}>
            永久记住的关于你的事实
          </div>
        </div>
        {/* BL-MEMORY-A3 (2026-06-03): 整理记忆按钮 — 真调 catfish_memory_dedupe LLM 语义判定 */}
        <button
          type="button"
          onClick={() => void runDedupe()}
          disabled={dedupeRunning}
          title="LLM 真判语义是否重复, 不真改盘 — 你看了再决定要不要合并"
          style={{
            background: dedupeRunning ? "var(--catfish-bg)" : "var(--catfish-cyan)",
            color: dedupeRunning ? "var(--catfish-text-muted)" : "white",
            border: "none",
            borderRadius: "var(--radius-sm)",
            padding: "4px 10px",
            fontSize: 11,
            cursor: dedupeRunning ? "default" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {dedupeRunning ? "扫描中..." : "🧹 整理记忆"}
        </button>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          读取失败: {error}
        </div>
      )}

      {dedupeError && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          整理失败: {dedupeError}
        </div>
      )}

      {/* BL-MEMORY-A3 (2026-06-03): dedupe suggestions inline 展开 */}
      {dedupeSuggestions !== null && (
        <div
          style={{
            marginBottom: "var(--space-3)",
            padding: "8px 12px",
            background: "var(--catfish-bg)",
            border: "1px dashed var(--catfish-cyan)",
            borderRadius: 6,
            fontSize: 12,
            maxHeight: 240,
            overflowY: "auto",
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--catfish-cyan)", marginBottom: 6 }}>
            🧹 LLM 真找到 {dedupeSuggestions.length} 条可能重复
            {proposeHints.length > 0 ? ` + ${proposeHints.length} 条像 skill 流程` : ""}
          </div>
          {dedupeSuggestions.length === 0 && proposeHints.length === 0 ? (
            <div style={{ color: "var(--catfish-text-muted)", fontStyle: "italic" }}>
              没真找到重复 — 你的 memory 真干净 ✓
            </div>
          ) : (
            <>
              {dedupeSuggestions.map((s, i) => {
                const key = `${s.target}-${s.entries[0].slice(0, 30)}`;
                const isApplying = applying === key;
                const isSame = s.llm_verdict === "same" || s.llm_verdict === null;
                return (
                  <div
                    key={i}
                    style={{
                      padding: "6px 0",
                      borderBottom: "1px dotted var(--catfish-border)",
                      opacity: isApplying ? 0.4 : 1,
                    }}
                  >
                    <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginBottom: 2 }}>
                      {s.target.toUpperCase()} · 相似度 {(s.similarity * 100).toFixed(0)}%
                      {s.llm_verdict ? ` · LLM 判: ${s.llm_verdict}` : " · jaccard fallback"}
                      {s.llm_reason ? ` · ${s.llm_reason}` : ""}
                    </div>
                    <div style={{ fontSize: 11, marginBottom: 2 }}>
                      <strong>A:</strong> {s.entries[0].slice(0, 120)}
                      {s.entries[0].length > 120 ? "..." : ""}
                    </div>
                    <div style={{ fontSize: 11, marginBottom: 4 }}>
                      <strong>B:</strong> {s.entries[1].slice(0, 120)}
                      {s.entries[1].length > 120 ? "..." : ""}
                    </div>
                    {isSame ? (
                      <button
                        type="button"
                        onClick={() => void applySuggestion(s)}
                        disabled={isApplying}
                        style={{
                          background: "var(--catfish-cyan)",
                          color: "white",
                          border: "none",
                          borderRadius: 3,
                          padding: "2px 8px",
                          fontSize: 10,
                          cursor: isApplying ? "default" : "pointer",
                        }}
                      >
                        {isApplying ? "..." : "合并 (留长的, 删短的)"}
                      </button>
                    ) : (
                      <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", fontStyle: "italic" }}>
                        {s.hint ?? "主题相关但不重复 — 各留一条"}
                      </div>
                    )}
                  </div>
                );
              })}
              {proposeHints.map((h, i) => (
                <div
                  key={`hint-${i}`}
                  style={{
                    padding: "6px 0",
                    borderBottom: "1px dotted var(--catfish-border)",
                  }}
                >
                  <div style={{ fontSize: 10, color: "var(--catfish-warning, #d70)", marginBottom: 2 }}>
                    💡 {h.target.toUpperCase()} · 像 skill 流程 → 建议存成 skill
                  </div>
                  <div style={{ fontSize: 11, marginBottom: 2 }}>
                    {h.entry_text.slice(0, 150)}
                    {h.entry_text.length > 150 ? "..." : ""}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", fontStyle: "italic" }}>
                    {h.reason} · 跟{" "}
                    <code style={{ fontSize: 10 }}>{h.proposed_skill_name}</code>{" "}
                    说"存成 skill" 让鲶鱼帮你做
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {!view && !error && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
          读取中…
        </div>
      )}

      {view && (
        <div
          style={{
            flex: 1,
            minHeight: 0,  // 关键: flex item 默认 min-height:auto 撑爆, 显式 0
            overflowY: "auto",
            paddingRight: 4,
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-3)",
          }}
        >
          <EntrySection
            label="USER · 员工本人"
            entries={view.user_entries}
            chars={userChars}
            limit={view.user_char_limit}
            removing={removing}
            confirming={confirming}
            onRemove={(e) => void handleRemove("user", e)}
          />
          <EntrySection
            label="MEMORY · 项目 / 技术"
            entries={view.memory_entries}
            chars={memoryChars}
            limit={view.memory_char_limit}
            removing={removing}
            confirming={confirming}
            onRemove={(e) => void handleRemove("memory", e)}
          />
        </div>
      )}

      {view && (
        <div
          style={{
            marginTop: "var(--space-2)",
            paddingTop: "var(--space-2)",
            borderTop: "1px solid var(--catfish-border)",
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>共 {(view.total_bytes / 1024).toFixed(1)} KB</span>
          <span>30s 自动刷新</span>
        </div>
      )}
    </div>
  );
}

function EntrySection({
  label,
  entries,
  chars,
  limit,
  removing,
  confirming,
  onRemove,
}: {
  label: string;
  entries: string[];
  chars: number;
  limit: number;
  removing: string | null;
  confirming: string | null;
  onRemove: (entry: string) => void;
}) {
  const pct = limit > 0 ? Math.min((chars / limit) * 100, 100) : 0;
  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          fontSize: 12,
          marginBottom: 4,
        }}
      >
        <strong>{label}</strong>
        <span style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
          {entries.length} 条 · {chars}/{limit} chars ({pct.toFixed(0)}%)
        </span>
      </div>
      {/* usage 进度条 */}
      <div
        style={{
          height: 3,
          background: "var(--catfish-border)",
          borderRadius: 2,
          overflow: "hidden",
          marginBottom: 6,
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background:
              pct > 90
                ? "var(--catfish-danger, #d33)"
                : pct > 70
                  ? "var(--catfish-warning, #d70)"
                  : "var(--catfish-cyan)",
          }}
        />
      </div>
      {entries.length === 0 ? (
        <div
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            fontStyle: "italic",
            padding: "4px 0",
          }}
        >
          (空 — 还没记过)
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {entries.map((e, i) => {
            const isRemoving = removing === e;
            const isConfirming = confirming === e;
            return (
              <div
                key={i}
                style={{
                  fontSize: 12,
                  padding: "4px 8px",
                  background: isConfirming ? "var(--catfish-warn-bg, #fff3cd)" : "var(--catfish-bg)",
                  borderRadius: "var(--radius-sm)",
                  lineHeight: 1.5,
                  wordBreak: "break-word",
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 6,
                  opacity: isRemoving ? 0.4 : 1,
                  transition: "background 0.15s",
                }}
              >
                <span style={{ flex: 1 }}>{e}</span>
                <button
                  type="button"
                  onClick={() => onRemove(e)}
                  disabled={isRemoving}
                  title={
                    isConfirming
                      ? "再点一次真删"
                      : "删这条 memory (本机 hermes USER.md atomic 写)"
                  }
                  style={{
                    flexShrink: 0,
                    background: isConfirming ? "var(--status-err, #d33)" : "transparent",
                    border: "none",
                    color: isConfirming ? "white" : "var(--catfish-text-muted)",
                    cursor: isRemoving ? "default" : "pointer",
                    fontSize: 11,
                    padding: isConfirming ? "2px 8px" : "0 4px",
                    borderRadius: 3,
                    fontWeight: isConfirming ? 600 : 400,
                  }}
                >
                  {isRemoving ? "..." : isConfirming ? "确认删" : "🗑"}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
