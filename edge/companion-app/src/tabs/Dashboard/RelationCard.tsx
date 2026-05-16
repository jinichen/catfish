/** Dashboard 卡 — "鲶鱼对你的印象" (BL-E16 五一 sprint 5/3 晚)
 *
 * 设计立场: 鲶鱼"记得"你的事, 员工**必须**能看到 + 删除, 否则就 creepy.
 *   - 显示最近 5 条 employee_journal 总结 (LLM 已经在每次 chat 看到这些)
 *   - 显示"今天第 N 次 / 距上次 N 天 N 小时"
 *   - "清空印象" 按钮 (rm journal + meta) — 隐私逃生口
 *
 * 不调 gateway, 直接读本机文件 (Tauri command).
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useAgentStore } from "../../store/agent";

interface JournalEntry {
  title: string;
  body: string;
}

interface RelationView {
  recent_entries: JournalEntry[];
  last_chat_human: string | null;
  today_count: number | null;
  journal_size_bytes: number;
}

export default function RelationCard() {
  // BL-E11 后续: 标题 + 提示语用员工自定义名
  const agentName = useAgentStore((s) => s.name);
  const [view, setView] = useState<RelationView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const v = await invoke<RelationView>("relation_summary");
      setView(v);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    // 5/5 鸿波报"不会立即更新最新数据" 修: 加 30s polling.
    // gateway 后台 summarizer 每聊几句异步 append journal, 不刷一直显旧的.
    void refresh();
    const id = setInterval(() => void refresh(), 30_000);
    return () => clearInterval(id);
    // refresh 是普通 closure (不 stale, 因为内部 setView/setError 用 setter),
    // deps 空数组, 跟 mount/unmount 同周期
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const forget = async () => {
    setBusy(true);
    try {
      await invoke("relation_forget");
      setConfirming(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        // BL-RELATION-CARD-FILL-HEIGHT (5/16 V4 终): 固定卡 height 480 (响应式
        // 不超过 50vh). UserProfileCard 同高. 三卡 row 整齐. 内部 entries 区
        // flex:1 撑满剩余 + overflow auto 滚动.
        // V3 错: height:100% + flex:1 没 row 高度限制, entries 无限撑高.
        height: "min(480px, 50vh)",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
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
            <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
            {agentName}对你的印象
          </h3>
          {/* 5/5 鸿波: '印象' vs '硬事实' 区分不清, 加副标题让员工一眼看懂 */}
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", paddingLeft: 28 }}>
            我帮你总结过的对话主题 (像日记)
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

      {!view && !error && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>读取中…</div>}

      {view && (
        <>
          {/* 时间感.
              5/5 鸿波拍板 fix: 之前 `(a || view.today_count) && (...)`, 当 today_count=0
              时 (a || 0) = 0, React 把字面量 "0" 渲染到 UI 上 (用户看到只有一个孤零零的"0").
              改 explicit boolean 计算, 防 React 渲染 falsy 数字. */}
          {(() => {
            const hasLast = !!view.last_chat_human;
            const hasCount = view.today_count !== null && view.today_count > 0;
            if (!hasLast && !hasCount) return null;
            return (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-3)",
                  lineHeight: 1.6,
                }}
              >
                {hasLast && (
                  <div>距上次找我: <strong style={{ color: "var(--catfish-text)" }}>{view.last_chat_human}前</strong></div>
                )}
                {hasCount && (
                  <div>今天第 <strong style={{ color: "var(--catfish-text)" }}>{view.today_count}</strong> 次找我</div>
                )}
              </div>
            );
          })()}

          {/* 最近条目 */}
          {view.recent_entries.length === 0 ? (
            <div
              style={{
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                fontStyle: "italic",
                padding: "var(--space-3) 0",
              }}
            >
              还没有印象 — 多跟我聊几次, 我会记住你的工作.
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 8,
                // BL-RELATION-CARD-FILL-HEIGHT (5/16 V4 fix): flex:1 撑满卡内剩余高度,
                // overflowY auto 内部滚动. minHeight:0 关键! flex item 默认 min-height:
                // auto (= min-content), entries 内容 4500px 撑不出去导致 footer 被切.
                // 显式 0 让 flex 能真压缩内容到剩余 slot, overflow 才生效.
                flex: 1,
                minHeight: 0,
                overflowY: "auto",
                paddingRight: 4,
              }}
            >
              {/* 5/5 鸿波拍板隐私 fix: 默认只显标题, 不显正文 (有些 session 涉及私事
                  不该 in-glance 暴露在 Dashboard). 点击标题展开看正文 */}
              {view.recent_entries.map((e, i) => (
                <RelationEntry key={i} title={e.title} body={e.body} />
              ))}
            </div>
          )}

          {/* footer: 大小 + 清空 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginTop: "var(--space-3)",
              paddingTop: "var(--space-2)",
              borderTop: "1px solid var(--catfish-border)",
              fontSize: 11,
              color: "var(--catfish-text-muted)",
            }}
          >
            <span>共 {(view.journal_size_bytes / 1024).toFixed(1)} KB</span>
            {!confirming ? (
              <button
                type="button"
                onClick={() => setConfirming(true)}
                style={{
                  background: "transparent",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  fontSize: 11,
                  padding: "3px 8px",
                  color: "var(--catfish-text-muted)",
                  cursor: "pointer",
                }}
              >
                清空印象
              </button>
            ) : (
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button
                  type="button"
                  onClick={forget}
                  disabled={busy}
                  style={{
                    background: "var(--status-err)",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "white",
                    cursor: busy ? "default" : "pointer",
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  {busy ? "清空中…" : "确认"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  disabled={busy}
                  style={{
                    background: "transparent",
                    border: "1px solid var(--catfish-border)",
                    borderRadius: 4,
                    fontSize: 11,
                    padding: "3px 8px",
                    color: "var(--catfish-text-muted)",
                    cursor: busy ? "default" : "pointer",
                  }}
                >
                  算了
                </button>
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}


/** 单条 journal 条目: 默认显标题 + 摘要 (前 100 字), 点击看全文.
 *  5/5 鸿波拍板: 之前默认折叠只显标题, 用户得一条条点开太烦. 改成默认露摘要,
 *  全文长时再点击展开. 隐私敏感的话员工自己用"清空印象"按钮删. */
function RelationEntry({ title, body }: { title: string; body: string }) {
  const [expanded, setExpanded] = useState(false);
  const SNIPPET_LEN = 100;
  const hasMore = body.length > SNIPPET_LEN;
  const snippet = hasMore ? body.slice(0, SNIPPET_LEN) + "…" : body;

  return (
    <div
      style={{
        fontSize: 12,
        background: "var(--catfish-bg-cream)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 10px",
        cursor: hasMore ? "pointer" : "default",
      }}
      onClick={() => hasMore && setExpanded((e) => !e)}
      title={hasMore ? "点击展开全文" : ""}
    >
      <div
        style={{
          fontWeight: 500,
          color: "var(--catfish-text)",
          marginBottom: body ? 4 : 0,
        }}
      >
        {title}
      </div>
      {body && (
        <div
          style={{
            color: "var(--catfish-text-muted)",
            lineHeight: 1.6,
            whiteSpace: "pre-wrap",
          }}
        >
          {expanded ? body : snippet}
        </div>
      )}
      {hasMore && (
        <div
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            textAlign: "right",
            marginTop: 4,
          }}
        >
          {expanded ? "收起 ↑" : "展开 ↓"}
        </div>
      )}
    </div>
  );
}
