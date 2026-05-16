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

export default function HermesMemoryCard() {
  const [view, setView] = useState<HermesMemoryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);  // 哪条正在删
  // BL-MEMORY-EDIT-UI fix (5/16): Tauri webview 默认禁 native confirm(), 改 inline
  // 二次点击 — 点第 1 次 🗑 进 confirming 态 (按钮变红 ✓), 点第 2 次真删.
  const [confirming, setConfirming] = useState<string | null>(null);

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
      <div style={{ marginBottom: "var(--space-2)" }}>
        <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
          🧠 我的 hermes memory
        </h3>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 2 }}>
          跨 session 永久事实 · target=user (员工本人) + memory (项目/技术)
        </div>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          读取失败: {error}
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
