/** Dashboard 卡 — "鲶鱼学到的你的写作风格" (BL-MM8, 5/6 ship)
 *
 * 跟 UserProfileCard 配对:
 *   - UserProfileCard: 显式画像 (员工 confirm 过的 trait)
 *   - 本卡: 隐式特征 (从员工历史文档自动抽的统计)
 *
 * 设计立场:
 *   - 列基础统计 (扫了 N 个文档 / 平均句长 / Top 词 / 标点偏好)
 *   - "重新抽取" 按钮 (员工写完一份新汇报后想更新)
 *   - "清空" 按钮 (隐私逃生)
 *   - 调 catfish_style_fingerprint_* 工具 (走 tool-bridge)
 */

import { useEffect, useState, useCallback } from "react";

import { toolBridgeCallTool } from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";

interface FingerprintView {
  exists: boolean;
  stats?: {
    total_docs: number;
    total_chars: number;
    avg_sentence_length: number;
    sentence_count: number;
  };
  top_words?: Array<{ word: string; weight: number }>;
  punctuation_pref?: Record<string, number>;
  structure_pref?: {
    list_ratio: number;
    table_ratio: number;
    prose_ratio: number;
  };
  sample_sentences?: string[];
  last_refreshed?: number;
  source_count?: number;
  had_jieba?: boolean;
  hint?: string;
}

const REFRESH_MS = 60_000;

function humanTime(ts: number | undefined | null): string {
  if (!ts || ts <= 0) return "未抽取";
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

export default function StyleFingerprintCard() {
  const agentName = useAgentStore((s) => s.name);
  const [view, setView] = useState<FingerprintView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await toolBridgeCallTool("catfish_style_fingerprint_get", {});
      if (!r.ok) {
        setError(r.error || "调用 fingerprint_get 失败");
        return;
      }
      const inner = (r.result as { type: string; result: FingerprintView }) || {};
      setView(inner.result || { exists: false });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await toolBridgeCallTool("catfish_style_fingerprint_refresh", {});
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  };

  const clear = async () => {
    setRefreshing(true);
    try {
      await toolBridgeCallTool("catfish_style_fingerprint_clear", {});
      setConfirmingClear(false);
      await load();
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
        <h3 style={{ margin: 0 }}>✍️ 你的文书风格 (隐式)</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          从你历史文档抽的统计 · {agentName}写汇报时模仿
        </span>
        <button
          onClick={() => void refresh()}
          disabled={refreshing}
          style={{
            marginLeft: "auto",
            fontSize: 11,
            padding: "2px 8px",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: "pointer",
            color: "var(--catfish-text-muted)",
          }}
          title="重新扫描 ~/Documents/work/ + ~/.catfish/output/"
        >
          {refreshing ? "扫描中..." : "重新抽取"}
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: "var(--space-2)" }}>
          {error}
        </div>
      )}

      {!view?.exists && !error && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          {view?.hint || `还没抽过你的文书风格. 点 "重新抽取" 让${agentName}扫一下你的历史文档.`}
        </div>
      )}

      {view?.exists && view.stats && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8, fontSize: 13, marginBottom: "var(--space-3)" }}>
            <div>📚 来源文档: <strong>{view.source_count ?? 0}</strong></div>
            {/* 5/15 鸿波撞 white screen — view.stats={} 空对象通过 truthy 检查
                但内部字段 undefined → .toFixed/.toLocaleString 崩.
                每个字段独立 nullish 兜底 '-'. */}
            <div>📝 总字数: <strong>{view.stats.total_chars?.toLocaleString() ?? "-"}</strong></div>
            <div>📏 平均句长: <strong>{typeof view.stats.avg_sentence_length === "number" ? `${view.stats.avg_sentence_length.toFixed(1)} 字` : "-"}</strong></div>
            <div>📊 句子总数: <strong>{view.stats.sentence_count ?? "-"}</strong></div>
            <div>🕐 上次抽取: <strong>{humanTime(view.last_refreshed)}</strong></div>
            <div>🔧 jieba 分词: <strong>{view.had_jieba ? "✅" : "❌ (退 char fallback)"}</strong></div>
          </div>

          {view.top_words && view.top_words.length > 0 && (
            <div style={{ marginBottom: "var(--space-3)" }}>
              <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Top 高频词</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {view.top_words.map((w) => (
                  <span
                    key={w.word}
                    title={`权重 ${w.weight}`}
                    style={{
                      fontSize: 12,
                      padding: "2px 8px",
                      background: "var(--catfish-bg)",
                      border: "1px solid var(--catfish-border)",
                      borderRadius: 12,
                      color: "var(--catfish-text)",
                    }}
                  >
                    {w.word}
                  </span>
                ))}
              </div>
            </div>
          )}

          {view.structure_pref && (
            <div style={{ marginBottom: "var(--space-3)", fontSize: 12 }}>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>段落结构偏好</div>
              <div style={{ color: "var(--catfish-text-muted)" }}>
                列表 {(view.structure_pref.list_ratio * 100).toFixed(0)}% ·
                {" "}表格 {(view.structure_pref.table_ratio * 100).toFixed(0)}% ·
                {" "}散文 {(view.structure_pref.prose_ratio * 100).toFixed(0)}%
              </div>
            </div>
          )}

          {view.sample_sentences && view.sample_sentences.length > 0 && (
            <div style={{ marginBottom: "var(--space-3)" }}>
              <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>样本句 (LLM 模仿用)</div>
              {view.sample_sentences.map((s, i) => (
                <div key={i} style={{ fontSize: 12, color: "var(--catfish-text-muted)", padding: "2px 0", borderLeft: "2px solid var(--catfish-cyan-dim)", paddingLeft: 8, marginBottom: 2 }}>
                  {s}
                </div>
              ))}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            {!confirmingClear ? (
              <button
                onClick={() => setConfirmingClear(true)}
                style={{
                  fontSize: 11, padding: "4px 8px", background: "transparent",
                  border: "1px solid var(--catfish-border)", borderRadius: 3, cursor: "pointer",
                  color: "var(--catfish-text-muted)",
                }}
              >
                清空指纹
              </button>
            ) : (
              <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
                <span style={{ color: "var(--status-warn)" }}>确认清空? (下次写汇报又从零猜)</span>
                <button onClick={() => void clear()} disabled={refreshing}
                  style={{ background: "var(--status-err)", color: "white", border: "none", borderRadius: 3, padding: "2px 8px", cursor: "pointer" }}>
                  清
                </button>
                <button onClick={() => setConfirmingClear(false)}
                  style={{ background: "transparent", border: "1px solid var(--catfish-border)", borderRadius: 3, padding: "2px 8px", cursor: "pointer" }}>
                  取消
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
