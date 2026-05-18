/** 邮件 tab — BL-COMPANION-EMAIL-TAB step1 (5/18 鸿波 C 路径).
 *
 * 跟工作台 / 仪表盘同级顶部 tab. 完整 inbox 浏览 + 单封详情 + 起草回复入口.
 *
 * step1 范围 (本提交):
 *   - 左侧列表 + 右侧详情 双栏布局
 *   - toolbar: 仅未读 toggle / 刷新 / "🌐 开 Mail.app" 兜底
 *   - 列表显未读 5 列 (账号 / 发件人 / 主题 / 时间 / 状态), 滚动
 *   - 点单封 → 右侧加载全文 (subject / from / to / date / body_text)
 *   - 主区"💬 让小鲶处理这封" → useUIStore.startProactiveChat (跳工作台)
 *
 * step2 留:
 *   - 主题 / 发件人 搜索框
 *   - 急/中/低 评级 badge (跟 scheduler 评级状态同步)
 *   - 单封"起草回复" 调 catfish-email create-draft (一键写 + 进 Drafts)
 *   - 已读 / 删除 etc.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  emailListFetch,
  emailReadMessage,
  emailAccountsFetch,
  emailCreateDraft,
  emailUrgencyMap,
  type EmailDigestItem,
  type EmailAccountItem,
} from "../../lib/tauri";
import { useUIStore } from "../../store/ui";

interface FullMessage extends EmailDigestItem {
  recipients?: string[];
  cc?: string[];
  attachments?: Array<{ filename: string; size_bytes: number; content_type: string }>;
}

export default function EmailTab() {
  const [items, setItems] = useState<EmailDigestItem[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountItem[]>([]);
  const [urgencyMap, setUrgencyMap] = useState<Record<string, string>>({});
  const [unreadOnly, setUnreadOnly] = useState(true);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FullMessage | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const startProactiveChat = useUIStore((s) => s.startProactiveChat);
  const setActiveTab = useUIStore((s) => s.setActiveTab);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [listJson, accountsJson, urgency] = await Promise.all([
        emailListFetch(unreadOnly, 100),
        emailAccountsFetch().catch(() => "[]"),
        emailUrgencyMap().catch(() => ({})),
      ]);
      const list = JSON.parse(listJson);
      const accs = JSON.parse(accountsJson);
      if (Array.isArray(list)) setItems(list as EmailDigestItem[]);
      if (Array.isArray(accs)) setAccounts(accs as EmailAccountItem[]);
      setUrgencyMap(urgency || {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [unreadOnly]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // 前端 filter: 主题 / 发件人 / 账号 substring (case-insensitive)
  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter((m) =>
      (m.subject || "").toLowerCase().includes(q) ||
      (m.sender || "").toLowerCase().includes(q) ||
      (m.account || "").toLowerCase().includes(q),
    );
  }, [items, search]);

  // 选邮件 → 拉全文
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    emailReadMessage(selectedId)
      .then((json) => {
        const parsed = JSON.parse(json) as FullMessage;
        setDetail(parsed);
      })
      .catch((e) => {
        setDetailError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  const handleAskCatfish = (m: FullMessage) => {
    const snippet = (m.body_text || "").slice(0, 500);
    const starter =
      `这封邮件:\n` +
      `- 发件人: ${m.sender}\n` +
      `- 主题: ${m.subject}\n` +
      `- 时间: ${m.date}\n\n` +
      `正文摘要:\n${snippet}${(m.body_text || "").length > 500 ? "…" : ""}\n\n` +
      `帮我看下这封怎么回, 起个草稿.`;
    startProactiveChat(starter);
    setActiveTab("chat");  // 跳到工作台看 chat
  };

  const handleOpenMailApp = async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-shell");
      await open("mailto:");
    } catch (e) {
      console.warn("[EmailTab] open Mail.app 失败:", e);
    }
  };

  const headerSummary = useMemo(() => {
    if (error) return "拉取失败";
    if (loading && items.length === 0) return "加载中…";
    const unreadCnt = items.filter((i) => !i.is_read).length;
    return unreadOnly
      ? `${items.length} 封未读 · ${accounts.length} 个账号`
      : `${items.length} 封 · ${unreadCnt} 未读 · ${accounts.length} 个账号`;
  }, [items, accounts, loading, error, unreadOnly]);

  return (
    <div
      style={{
        display: "flex",
        height: "100%",
        background: "var(--catfish-bg)",
        overflow: "hidden",
      }}
    >
      {/* ━━ 左侧列表 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
      <aside
        style={{
          width: 340,
          flex: "0 0 auto",
          borderRight: "1px solid var(--catfish-border)",
          display: "flex",
          flexDirection: "column",
          background: "var(--catfish-bg-elevated)",
        }}
      >
        {/* 列表 header: 概要 + toolbar */}
        <div
          style={{
            padding: "var(--space-3)",
            borderBottom: "1px solid var(--catfish-border)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              marginBottom: "var(--space-2)",
            }}
          >
            <span style={{ fontSize: 18 }}>📧</span>
            <strong style={{ fontSize: 14 }}>邮件</strong>
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginLeft: 4 }}>
              {headerSummary}
            </span>
            <button
              type="button"
              onClick={() => void loadList()}
              disabled={loading}
              title="重新同步"
              style={{
                marginLeft: "auto",
                background: "transparent",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                color: "var(--catfish-text-muted)",
                cursor: loading ? "wait" : "pointer",
                fontSize: 11,
                padding: "2px 8px",
                fontFamily: "inherit",
              }}
            >
              {loading ? "…" : "⟳"}
            </button>
          </div>
          {/* toolbar: 仅未读 toggle + 开 Mail.app */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11, marginBottom: 6 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={unreadOnly}
                onChange={(e) => setUnreadOnly(e.target.checked)}
              />
              <span style={{ color: "var(--catfish-text-muted)" }}>仅未读</span>
            </label>
            <button
              type="button"
              onClick={() => void handleOpenMailApp()}
              style={{
                marginLeft: "auto",
                fontSize: 11,
                padding: "2px 8px",
                background: "transparent",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                color: "var(--catfish-text-muted)",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
              title="在 Apple Mail.app 里打开 (catfish 不重做 inbox UI, 复杂操作走 Mail.app)"
            >
              📬 Mail.app
            </button>
          </div>
          {/* 搜索框 — 前端 filter 主题/发件人/账号 */}
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="🔍 搜主题 / 发件人 / 账号"
            style={{
              width: "100%",
              padding: "4px 8px",
              fontSize: 12,
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              fontFamily: "inherit",
              boxSizing: "border-box",
            }}
          />
          {search && (
            <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginTop: 4 }}>
              🔍 已筛选 {filteredItems.length} / {items.length}
            </div>
          )}
        </div>

        {/* 错误态 */}
        {error && (
          <div
            style={{
              padding: "var(--space-3)",
              fontSize: 12,
              color: "var(--catfish-text-muted)",
              background: "rgba(239, 68, 68, 0.06)",
              borderBottom: "1px solid var(--catfish-border)",
              lineHeight: 1.5,
            }}
          >
            {error}
            <div style={{ marginTop: 6, fontSize: 11, opacity: 0.7 }}>
              常见: Mail.app 没开 · Automation 权限没给 · CLI 没装 (
              <code>bash edge/email-agent/install.sh</code>)
            </div>
          </div>
        )}

        {/* 列表 */}
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            flex: 1,
            overflowY: "auto",
            scrollbarWidth: "thin",
          }}
        >
          {!error && !loading && filteredItems.length === 0 && (
            <li
              style={{
                padding: "var(--space-4) var(--space-3)",
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                textAlign: "center",
              }}
            >
              {search
                ? `🔍 没匹配 "${search}"`
                : unreadOnly ? "🌊 没有未读邮件, 都处理完了" : "📭 收件箱为空"}
            </li>
          )}
          {filteredItems.map((m) => (
            <ListItem
              key={m.id}
              item={m}
              active={selectedId === m.id}
              urgency={urgencyMap[m.id]}
              onClick={() => setSelectedId(m.id)}
            />
          ))}
        </ul>
      </aside>

      {/* ━━ 右侧详情 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */}
      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        {!selectedId && (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--catfish-text-muted)",
              fontSize: 13,
            }}
          >
            👈 左边选一封邮件看详情
          </div>
        )}

        {selectedId && detailLoading && (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--catfish-text-muted)",
              fontSize: 13,
            }}
          >
            加载中…
          </div>
        )}

        {selectedId && detailError && (
          <div
            style={{
              flex: 1,
              padding: "var(--space-4)",
              color: "var(--catfish-text-muted)",
              fontSize: 13,
              lineHeight: 1.6,
            }}
          >
            <strong style={{ color: "var(--catfish-text)" }}>读邮件失败:</strong>
            <pre
              style={{
                marginTop: 8,
                padding: "var(--space-2)",
                background: "rgba(239, 68, 68, 0.06)",
                borderRadius: 4,
                fontSize: 11,
                overflowX: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {detailError}
            </pre>
          </div>
        )}

        {selectedId && detail && !detailLoading && (
          <DetailPane msg={detail} onAskCatfish={handleAskCatfish} />
        )}
      </main>
    </div>
  );
}

/** ─── 列表项 ─────────────────────────────────────────── */

function ListItem({
  item,
  active,
  urgency,
  onClick,
}: {
  item: EmailDigestItem;
  active: boolean;
  urgency?: string;  // '急' / '中' / '低', undef = scheduler 还没评级
  onClick: () => void;
}) {
  return (
    <li
      onClick={onClick}
      style={{
        padding: "10px 12px",
        borderBottom: "1px solid var(--catfish-border)",
        cursor: "pointer",
        background: active ? "var(--catfish-bg-cream)" : "transparent",
        borderLeft: active
          ? "3px solid var(--catfish-cyan)"
          : "3px solid transparent",
        fontSize: 12,
        userSelect: "none",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 6,
          marginBottom: 3,
        }}
      >
        {!item.is_read && (
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: "var(--catfish-cyan)",
              flex: "0 0 auto",
              marginTop: 4,
            }}
            aria-label="未读"
          />
        )}
        <strong
          style={{
            flex: 1,
            color: "var(--catfish-text)",
            fontWeight: item.is_read ? 400 : 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {_extractSenderName(item.sender)}
        </strong>
        {/* 评级 badge — scheduler 已评过的才显, 急=红 / 低=灰 / 中=不显省视觉 */}
        {urgency === "急" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(239, 68, 68, 0.15)",
              color: "rgb(185, 28, 28)",
              borderRadius: 3,
              fontWeight: 600,
            }}
          >
            急
          </span>
        )}
        {urgency === "低" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "transparent",
              color: "var(--catfish-text-muted)",
              opacity: 0.6,
              borderRadius: 3,
            }}
          >
            低
          </span>
        )}
        <span style={{ flex: "0 0 auto", color: "var(--catfish-text-muted)", fontSize: 11 }}>
          {_formatShortDate(item.date)}
        </span>
      </div>
      <div
        style={{
          color: "var(--catfish-text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontWeight: item.is_read ? 400 : 500,
        }}
      >
        {item.subject || "(无主题)"}
      </div>
      <div
        style={{
          color: "var(--catfish-text-muted)",
          opacity: 0.7,
          fontSize: 10,
          marginTop: 2,
        }}
      >
        {item.account}
      </div>
    </li>
  );
}

/** ─── 详情面板 ───────────────────────────────────────── */

function DetailPane({
  msg,
  onAskCatfish,
}: {
  msg: FullMessage;
  onAskCatfish: (m: FullMessage) => void;
}) {
  const [drafting, setDrafting] = useState(false);
  const [draftResult, setDraftResult] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);

  const handleDraftReply = async () => {
    setDrafting(true);
    setDraftResult(null);
    setDraftError(null);
    try {
      // 直接落简单 placeholder 草稿 — 不调 LLM (那是工作台的事, 跟"💬 让小鲶处理" 区分).
      // 这里只做 "起草壳子": 收件人 = 原发件人, subject = Re:, body = quote 原文.
      // 员工去 Mail.app Drafts 打开后, 自己写正文 (或者切回工作台让小鲶帮起草).
      const replyTo = _replyAddress(msg.sender);
      const subj = msg.subject?.startsWith("Re:") ? msg.subject : `Re: ${msg.subject || ""}`;
      const quoted = (msg.body_text || "")
        .split("\n")
        .map((l) => `> ${l}`)
        .join("\n");
      const body = `\n\n\n${"-".repeat(20)} 原邮件 ${"-".repeat(20)}\n` +
        `发件人: ${msg.sender}\n` +
        `时间: ${msg.date}\n` +
        `主题: ${msg.subject}\n\n` +
        quoted;
      const resultJson = await emailCreateDraft({
        to: replyTo,
        subject: subj,
        body,
        inReplyTo: msg.id,
        account: msg.account,
      });
      const parsed = JSON.parse(resultJson);
      setDraftResult(parsed?.draft_id ?? "ok");
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  };
  return (
    <>
      {/* 详情 header */}
      <div
        style={{
          padding: "var(--space-4)",
          borderBottom: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
        }}
      >
        <h2
          style={{
            margin: 0,
            fontSize: 16,
            fontWeight: 600,
            color: "var(--catfish-text)",
            lineHeight: 1.4,
          }}
        >
          {msg.subject || "(无主题)"}
        </h2>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: "4px 12px",
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            marginTop: 12,
          }}
        >
          <div>发件人</div>
          <div style={{ color: "var(--catfish-text)" }}>{msg.sender}</div>
          {msg.recipients && msg.recipients.length > 0 && (
            <>
              <div>收件人</div>
              <div>{msg.recipients.join(", ")}</div>
            </>
          )}
          {msg.cc && msg.cc.length > 0 && (
            <>
              <div>抄送</div>
              <div>{msg.cc.join(", ")}</div>
            </>
          )}
          <div>时间</div>
          <div>{msg.date}</div>
          <div>账号</div>
          <div>{msg.account}</div>
          {msg.has_attachments && msg.attachments && msg.attachments.length > 0 && (
            <>
              <div>附件</div>
              <div>
                {msg.attachments.map((a, i) => (
                  <span key={i} style={{ marginRight: 8 }}>
                    📎 {a.filename} ({Math.round(a.size_bytes / 1024)} KB)
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
        {/* 行动按钮 */}
        <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => onAskCatfish(msg)}
            style={{
              background: "var(--catfish-cyan)",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            💬 让小鲶处理这封
          </button>
          <button
            type="button"
            onClick={() => void handleDraftReply()}
            disabled={drafting}
            style={{
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              cursor: drafting ? "wait" : "pointer",
              fontFamily: "inherit",
            }}
            title="在 Apple Mail 起一份回复草稿 (空白带 quote 原文). 真发送要你自己去 Mail.app Drafts 点发. 红线."
          >
            {drafting ? "起草中…" : "✏️ 起草回复 (落 Drafts)"}
          </button>
          {draftResult && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              ✓ 草稿已落 Mail Drafts, 打开 Mail.app review + 发送
            </span>
          )}
          {draftError && (
            <span style={{ fontSize: 11, color: "rgb(185, 28, 28)" }}>
              起草失败: {draftError}
            </span>
          )}
        </div>
      </div>

      {/* 正文 */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--space-4)",
          fontSize: 13,
          lineHeight: 1.6,
          color: "var(--catfish-text)",
          whiteSpace: "pre-wrap",
          background: "var(--catfish-bg)",
        }}
      >
        {msg.body_text || "(无正文)"}
      </div>
    </>
  );
}

/** ─── 工具函数 ───────────────────────────────────────── */

function _extractSenderName(sender: string): string {
  if (!sender) return "(未知)";
  const m = sender.match(/^([^<]+?)\s*<.+>$/);
  if (m) return m[1].trim().replace(/^"|"$/g, "");
  if (sender.includes("@")) return sender.split("@")[0];
  return sender;
}

/** "张三 <zhang@x.com>" → "zhang@x.com"; "bob@example.com" → "bob@example.com" */
function _replyAddress(sender: string): string {
  if (!sender) return "";
  const m = sender.match(/<([^>]+)>/);
  if (m) return m[1].trim();
  return sender.trim();
}

function _formatShortDate(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const dayDiff = Math.floor((now.getTime() - d.getTime()) / (24 * 3600 * 1000));
    if (dayDiff === 0) {
      return d.toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    if (dayDiff === 1) return "昨天";
    if (dayDiff < 7) return `${dayDiff}天前`;
    return d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  } catch {
    return "";
  }
}
