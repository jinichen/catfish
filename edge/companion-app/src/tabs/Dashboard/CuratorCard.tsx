/** Dashboard 卡 — "脚本整理" (BL-CR Curator 集成 步骤 4, 5/7).
 *
 * 显示 hermes 0.12 自带的 Curator daemon 状态:
 *   - 是否启用 / 暂停
 *   - 上次跑的时间 + 干了啥 (归档了几个 stale skill / 合并了几个相似)
 *   - 已跑过几次
 *   - 配置参数 (stale/archive 天数, 让员工随时调宽)
 *
 * "整理记录" 是给员工的**透明可控**承诺 — 你能看到鲶鱼帮你做了啥,
 * 跟 RelationCard ("鲶鱼对你的印象") 同思路.
 *
 * 30s polling 跟其他卡一致.
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface CuratorConfig {
  enabled: boolean;
  interval_hours: number;
  min_idle_hours: number;
  stale_after_days: number;
  archive_after_days: number;
}

interface CuratorStateView {
  never_run: boolean;
  last_run_at: string | null;
  last_run_duration_seconds: number | null;
  last_run_summary: string | null;
  paused: boolean;
  run_count: number;
}

function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (!t) return iso;
  const diff = Date.now() - t;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(t).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

/** BL-DASHBOARD-UI-CLEANUP (5/16): hermes Curator daemon 的 last_run_summary 是
 * raw 文本, 含 dev log ('auto: no changes; llm: skipped (no candidates)') +
 * 长 tag 分类列表 ('software-development 11个 ...'). 直接渲染太工程感.
 *
 * 友好化策略:
 *   1. 全是 "no changes / skipped / no candidates" 等无变化关键词 → 显示 "✓ 无变化"
 *   2. 否则截首 80 字符 + "展开" 按钮
 */
function FriendlySummary({ raw }: { raw: string }) {
  const [expanded, setExpanded] = useState(false);

  // case 1: 全是无变化的 dev 信号 — 显示友好版
  const lower = raw.toLowerCase();
  const noChangeSignals = [
    "no changes", "skipped", "no candidates", "nothing to do",
  ];
  const isNoChange =
    noChangeSignals.some((s) => lower.includes(s)) &&
    !lower.includes("archived") &&
    !lower.includes("merged");

  if (isNoChange) {
    return (
      <div style={{ color: "var(--catfish-text-muted)" }}>
        ✓ 无变化 (无 stale skill 需整理)
      </div>
    );
  }

  // case 2: 有内容, 截断 + 展开
  const SHORT_LIMIT = 80;
  const isLong = raw.length > SHORT_LIMIT;

  return (
    <div style={{ color: "var(--catfish-text)" }}>
      {expanded || !isLong ? raw : raw.slice(0, SHORT_LIMIT) + "…"}
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{
            marginLeft: 6,
            background: "transparent",
            border: "none",
            color: "var(--catfish-cyan)",
            cursor: "pointer",
            fontSize: 11,
            padding: 0,
          }}
        >
          {expanded ? "收起" : "展开"}
        </button>
      )}
    </div>
  );
}

export default function CuratorCard() {
  const [cfg, setCfg] = useState<CuratorConfig | null>(null);
  const [state, setState] = useState<CuratorStateView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const [c, s] = await Promise.all([
        invoke<CuratorConfig>("get_curator_config"),
        invoke<CuratorStateView>("get_curator_state"),
      ]);
      setCfg(c);
      setState(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 30_000);
    return () => clearInterval(id);
  }, []);

  const toggleEnabled = async () => {
    if (!cfg) return;
    setBusy(true);
    try {
      const next = await invoke<CuratorConfig>("set_curator_config", {
        enabled: !cfg.enabled,
        intervalHours: cfg.interval_hours,
        minIdleHours: cfg.min_idle_hours,
        staleAfterDays: cfg.stale_after_days,
        archiveAfterDays: cfg.archive_after_days,
      });
      setCfg(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        // BL-FIX22 (5/8): 鸿波反馈 '脚本整理也是同样的问题'. CuratorCard 是
        // section 第 5 张奇数, 2 列 grid 下变成单独半格旁边空白. 跟 SkillRevisionCard
        // FIX21 同款修法 — 整卡 gridColumn '1 / -1' 全宽.
        gridColumn: "1 / -1",
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
            🗂 脚本整理
          </h3>
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            老脚本自动归档 (永不真删 · 可恢复)
          </span>
        </div>
        <button
          type="button"
          onClick={refresh}
          title="刷新"
          style={{
            background: "transparent",
            border: "none",
            color: "var(--catfish-text-muted)",
            fontSize: 11,
            cursor: "pointer",
            padding: 4,
          }}
        >
          ↻
        </button>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginBottom: 8 }}>
          读取失败: {error}
        </div>
      )}

      {!cfg && !error && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>读取中…</div>
      )}

      {cfg && state && (
        <>
          {/* 状态行 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: "var(--space-3)",
              fontSize: 12,
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: cfg.enabled
                  ? state.paused
                    ? "#f59e0b"
                    : "#10b981"
                  : "#6b7280",
              }}
            />
            <strong>
              {!cfg.enabled
                ? "已关闭"
                : state.paused
                ? "暂停中"
                : state.never_run
                ? "等首次触发"
                : "运行中"}
            </strong>
            <span style={{ color: "var(--catfish-text-muted)" }}>
              · 已跑 {state.run_count} 次
            </span>
          </div>

          {/* 上次跑的状态 (BL-DASHBOARD-UI-CLEANUP 5/16): 友好化 + 截断长 summary) */}
          {!state.never_run && state.last_run_at && (
            <div
              style={{
                fontSize: 12,
                marginBottom: "var(--space-3)",
                lineHeight: 1.6,
                padding: "8px 10px",
                background: "var(--catfish-bg)",
                borderRadius: 6,
              }}
            >
              <div style={{ color: "var(--catfish-text-muted)", marginBottom: 4 }}>
                上次整理:{" "}
                <strong style={{ color: "var(--catfish-text)" }}>
                  {formatRelative(state.last_run_at)}
                </strong>
              </div>
              {state.last_run_summary && (
                <FriendlySummary raw={state.last_run_summary} />
              )}
            </div>
          )}

          {/* BL-DASHBOARD-UI-CLEANUP (5/16): 配置参数 3 行 → 折叠到 details, 默认收.
              鸿波反馈"信息密度过高". 员工不常调这些参数, 折叠减少视觉噪声. */}
          <details
            style={{
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              marginBottom: "var(--space-3)",
            }}
          >
            <summary style={{ cursor: "pointer", marginBottom: 4 }}>
              整理规则 (4 条)
            </summary>
            <div style={{ lineHeight: 1.7, paddingLeft: 16 }}>
              <div>· {cfg.stale_after_days} 天没用 → 标记"久未使用"</div>
              <div>· {cfg.archive_after_days} 天没用 → 归档到不可见 (能恢复)</div>
              <div>· 你 idle {cfg.min_idle_hours} 小时才开始整理</div>
              <div>· 每 {Math.round(cfg.interval_hours / 24)} 天最多跑一次</div>
            </div>
          </details>

          {/* 开关 */}
          <button
            type="button"
            onClick={toggleEnabled}
            disabled={busy}
            style={{
              width: "100%",
              padding: "6px 12px",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              background: cfg.enabled ? "transparent" : "var(--catfish-cyan)",
              color: cfg.enabled ? "var(--catfish-text)" : "var(--catfish-bg)",
              fontSize: 12,
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.5 : 1,
            }}
          >
            {busy ? "保存中…" : cfg.enabled ? "关闭自动整理" : "开启自动整理"}
          </button>

          <div
            style={{
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              textAlign: "center",
              marginTop: 6,
            }}
          >
            鲶鱼自带的 skill 不在整理范围内 (物理隔离)
          </div>
        </>
      )}
    </div>
  );
}
