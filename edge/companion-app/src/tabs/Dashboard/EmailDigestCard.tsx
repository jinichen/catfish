/** Dashboard 邮件简报卡 — BL-COMPANION-EMAIL-DIGEST step1 (5/18 鸿波).
 *
 * 不重复造 Mail.app 的 inbox UI. 只显:
 *   - 当前未读数 + 账号数
 *   - 最近 5 封未读元数据 (发件人 + 主题, 不显正文 — 隐私 + 重要邮件员工自己开 Mail 看)
 *   - 两个按钮: "💬 让小鲶处理" (跳 chat + auto prompt) / "📬 开 Mail.app"
 *
 * step1 不带 scheduler / LLM 评级 / 桌宠主动闲聊集成 — 这些 step2/3 加.
 * 数据走 Rust shell out `catfish-email list --unread --json`, 不缓存.
 */

import { useEffect, useState } from "react";

import {
  emailAccountsFetch,
  emailDigestFetch,
  type EmailAccountItem,
  type EmailDigestItem,
} from "../../lib/tauri";
import { useUIStore } from "../../store/ui";

const DISPLAY_LIMIT = 5;
const FETCH_LIMIT = 10;  // 多取 5 个 buffer, 万一前 5 个被员工标"忽略"未来 (step2)

export default function EmailDigestCard() {
  const [items, setItems] = useState<EmailDigestItem[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastFetched, setLastFetched] = useState<Date | null>(null);

  const startProactiveChat = useUIStore((s) => s.startProactiveChat);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [digestJson, accountsJson] = await Promise.all([
        emailDigestFetch(FETCH_LIMIT),
        emailAccountsFetch().catch(() => "[]"),  // 账号拉不到不挡主流程
      ]);
      const digestParsed = JSON.parse(digestJson);
      const accountsParsed = JSON.parse(accountsJson);
      if (Array.isArray(digestParsed)) {
        setItems(digestParsed as EmailDigestItem[]);
      }
      if (Array.isArray(accountsParsed)) {
        setAccounts(accountsParsed as EmailAccountItem[]);
      }
      setLastFetched(new Date());
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // 首次挂载 fetch 一次. step1 没 scheduler, 后续靠员工点⟳.
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAskCatfish = () => {
    if (items.length === 0) return;
    // 把未读简报扔给小鲶, 让它在 chat tab 里接手
    const list = items.slice(0, DISPLAY_LIMIT)
      .map((m, i) => `${i + 1}. ${_extractSenderName(m.sender)} - ${m.subject}`)
      .join("\n");
    const starter = `我有 ${items.length} 封未读邮件:\n${list}\n\n帮我看下哪些重要 / 要回的, 起草下回复.`;
    startProactiveChat(starter);
  };

  const handleOpenMail = async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      // mailto:? 让 Mail.app 起 (没参数 = 开 inbox)
      await open("mailto:");
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("[EmailDigest] shell.open mailto: 失败", e);
    }
  };

  return (
    <section
      style={{
        // BL-EMAIL-DIGEST-HEIGHT-CAP (5/18 鸿波): 不撑满 grid cell (老代码 height:100% 太高).
        // 卡片高度 = header + 列表 (max-height 限) + 按钮, 紧凑. 邮件多了列表内部滚.
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3)",
        // 卡片本身不 flex-grow, 由内部内容决定高度. 列表 max-height 兜住.
        boxSizing: "border-box",
        // grid-cell 里如果对侧卡更高, 本卡顶部对齐, 下方留空 (alignSelf:start)
        alignSelf: "start",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-2)",
        }}
      >
        <span style={{ fontSize: 16 }}>📧</span>
        <strong style={{ fontSize: 13 }}>邮件简报</strong>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {error
            ? "拉取失败"
            : loading
              ? "同步中…"
              : items.length === 0
                ? "收件箱全部已读"
                : `${items.length} 封未读 · ${accounts.length} 个账号`}
        </span>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          title="重新同步未读邮件"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--catfish-text-muted)",
            cursor: loading ? "wait" : "pointer",
            fontSize: 11,
            padding: "2px 8px",
            fontFamily: "inherit",
          }}
        >
          {loading ? "…" : "⟳"}
        </button>
      </header>

      {/* 错误态 — 主要是 Mail.app 没开 / 没装 CLI / Automation 没权限 */}
      {error && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            lineHeight: 1.6,
            padding: "var(--space-2)",
            background: "rgba(239, 68, 68, 0.06)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          <div style={{ marginBottom: 4 }}>{error}</div>
          <div style={{ fontSize: 11, opacity: 0.7 }}>
            常见: Mail.app 没开 → 先开 Mail.app · 第一次用 → 系统会问 catfish 是否能控制
            Mail, 必须点允许 · 装 CLI: <code>cd ~/person_task/catfish/edge/email-agent && bash install.sh</code>
          </div>
        </div>
      )}

      {/* 未读列表 — 限高 + 内部滚动 (BL-EMAIL-DIGEST-HEIGHT-CAP 5/18). */}
      {!error && items.length > 0 && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            marginBottom: "var(--space-2)",
            // 限 4-5 行可见, 多了滚 — 5 行 × 约 30px = 150px
            maxHeight: 160,
            overflowY: "auto",
            scrollbarWidth: "thin",
          }}
        >
          {/* 全显, 不再 slice 到 DISPLAY_LIMIT — 卡片高度限制 + 内部滚动会自然 handle */}
          {items.map((m) => (
            <li
              key={m.id}
              style={{
                display: "flex",
                gap: "var(--space-2)",
                padding: "6px 0",
                borderBottom: "1px dashed var(--catfish-border)",
                fontSize: 12,
              }}
            >
              <span style={{ flex: "0 0 auto", color: "var(--catfish-text-muted)", minWidth: 80, maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {_extractSenderName(m.sender)}
              </span>
              <span style={{ flex: 1, color: "var(--catfish-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {m.subject || "(无主题)"}
              </span>
              <span style={{ flex: "0 0 auto", color: "var(--catfish-text-muted)", fontSize: 11 }}>
                {_formatDate(m.date)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* 空状态 — 全部已读 */}
      {!error && !loading && items.length === 0 && (
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            textAlign: "center",
            padding: "var(--space-3) 0",
          }}
        >
          🌊 没有未读邮件, 都处理完了
        </div>
      )}

      {/* 动作 */}
      <div
        style={{
          display: "flex",
          gap: "var(--space-2)",
          alignItems: "center",
          marginTop: "var(--space-2)",
        }}
      >
        <button
          type="button"
          onClick={handleAskCatfish}
          disabled={items.length === 0}
          style={{
            flex: 1,
            // BL-EMAIL-DATE-ISO 顺手 fix: 老代码用 --catfish-accent (tokens.css 没此变量),
            // 解析失败按钮渲染成透明=看着 disabled. 改 --catfish-cyan (主色).
            background: items.length === 0 ? "var(--catfish-bg)" : "var(--catfish-cyan)",
            color: items.length === 0 ? "var(--catfish-text-muted)" : "#fff",
            border: "none",
            borderRadius: "var(--radius-sm)",
            padding: "8px 12px",
            cursor: items.length === 0 ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 500,
            fontFamily: "inherit",
          }}
        >
          💬 让小鲶帮我处理
        </button>
        <button
          type="button"
          onClick={() => void handleOpenMail()}
          style={{
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            padding: "8px 12px",
            cursor: "pointer",
            fontSize: 13,
            fontFamily: "inherit",
          }}
        >
          📬 开 Mail.app
        </button>
      </div>

      {lastFetched && !loading && (
        <div
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            opacity: 0.6,
            textAlign: "right",
            marginTop: 6,
          }}
        >
          {_formatSyncTime(lastFetched)}
        </div>
      )}
    </section>
  );
}

/** "张三 <zhang@x.com>" → "张三". 没显示名 → 取邮箱 @ 前部分. */
function _extractSenderName(sender: string): string {
  if (!sender) return "(未知)";
  const m = sender.match(/^([^<]+?)\s*<.+>$/);
  if (m) return m[1].trim();
  if (sender.includes("@")) return sender.split("@")[0];
  return sender;
}

/** ISO date → "今天 14:30" / "昨天" / "5-15". 用员工时区. */
function _formatDate(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const dayDiff = Math.floor(
      (now.getTime() - d.getTime()) / (24 * 3600 * 1000)
    );
    if (dayDiff === 0) {
      return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    }
    if (dayDiff === 1) return "昨天";
    if (dayDiff < 7) return `${dayDiff}天前`;
    return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch {
    return "";
  }
}

function _formatSyncTime(d: Date): string {
  return `上次同步 ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}
