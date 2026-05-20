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
  emailDeleteMessage,
  emailSendMessage,
  emailClassifyNow,
  type EmailDigestItem,
  type EmailAccountItem,
} from "../../lib/tauri";
import { useEmailStore } from "../../store/email";
import { useUIStore } from "../../store/ui";

interface FullMessage extends EmailDigestItem {
  recipients?: string[];
  cc?: string[];
  attachments?: Array<{ filename: string; size_bytes: number; content_type: string }>;
}

export default function EmailTab() {
  const [items, setItems] = useState<EmailDigestItem[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountItem[]>([]);
  // BL-COMPANION-EMAIL-DIGEST-STEP5 (5/20): urgencyMap 走 useEmailStore
  // (localStorage hydrate + Rust reconcile + 跨 tab 共享, 不再 local useState).
  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const setUrgencyMap = useEmailStore((s) => s.setUrgencyMap);
  const reconcileUrgency = useEmailStore((s) => s.reconcileFromRust);
  const markEmailRead = useEmailStore((s) => s.markRead);
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
      const [listJson, accountsJson, _urgency] = await Promise.all([
        emailListFetch(unreadOnly, 100),
        emailAccountsFetch().catch(() => "[]"),
        // BL-COMPANION-EMAIL-DIGEST-STEP5: 走 store.reconcileFromRust 后台拉,
        // setUrgencyMap 不再这里调 — store 内部自己 merge + 持久化
        reconcileUrgency().catch(() => ({})),
      ]);
      void _urgency;
      const list = JSON.parse(listJson);
      const accs = JSON.parse(accountsJson);
      if (Array.isArray(list)) setItems(list as EmailDigestItem[]);
      if (Array.isArray(accs)) setAccounts(accs as EmailAccountItem[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [unreadOnly]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // 5/18 BL-EMAIL-URGENCY-BADGE: 列表加载完后主动评级所有未评 id.
  // scheduler 只评 diff 新邮件, 启动时已有的历史邮件永远无评级 → badge 空白.
  // 这里主动 batch 评级 (LLM call 一次, 已 cache 的跳过省 token).
  // 分批 30 防 prompt 太长, 顺序 await 不并发避免烧 quota.
  useEffect(() => {
    if (items.length === 0) return;
    const unrated = items.filter((it) => !urgencyMap[it.id]);
    if (unrated.length === 0) return;
    let cancelled = false;
    (async () => {
      const BATCH = 30;
      for (let i = 0; i < unrated.length; i += BATCH) {
        if (cancelled) return;
        const batch = unrated.slice(i, i + BATCH);
        try {
          const updated = await emailClassifyNow(batch.map((it) => ({
            id: it.id,
            subject: it.subject,
            sender: it.sender,
            account: it.account,
            date: it.date,
            is_read: it.is_read,
          })));
          if (!cancelled) setUrgencyMap(updated);
        } catch {
          // 评级失败 (gateway 挂 / token 过期) — 跳过, badge 维持空白不阻塞 UI
          return;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 依赖 items.length + 第一条 id 防 items 引用变更 (filteredItems 重新算) 触发重跑
  }, [items, urgencyMap]);

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
        // 5/18 BL-EMAIL-MARK-READ: CLI 已经在 Mail.app/Foxmail 那侧标已读了,
        // 这里乐观更新本地 items 让列表立即反映 (无需重新拉 list_fetch).
        // parsed.is_read 是 CLI 返回的最新状态; 若 CLI 标失败它会保持 false,
        // 跟 stderr 警告对得上, UI 也不会乱标.
        if (parsed.is_read) {
          setItems((prev) =>
            prev.map((it) => (it.id === selectedId ? { ...it, is_read: true } : it)),
          );
          // BL-COMPANION-EMAIL-DIGEST-STEP5 sub-task 2 (5/20): 同步告诉 store
          // 这封被读了 → 桌宠主动闲聊 (BL-E13) 不再 push 这封, 即使 24h
          // dedup window 还在. 写 localStorage 持久化跨 Companion 重启.
          markEmailRead(selectedId);
        }
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

  // BL-COMPANION-EMAIL-TAB-MAILAPP-BUTTON-REMOVE (5/18 鸿波):
  // 老 `📬 Mail.app` 按钮删 — catfish 自己读两边客户端都正常, 这按钮只跑 mailto:
  // 起空白 compose, 跟员工预期"打开收件箱"不一致 + 现在没用例.

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
          {/* toolbar: 只剩 仅未读 toggle. Mail.app 按钮 5/18 已删 (catfish 自己读完整, 不需要 bounce 出去) */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11, marginBottom: 6 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={unreadOnly}
                onChange={(e) => setUnreadOnly(e.target.checked)}
              />
              <span style={{ color: "var(--catfish-text-muted)" }}>仅未读</span>
            </label>
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
          <DetailPane
            msg={detail}
            onAskCatfish={handleAskCatfish}
            onDeleted={() => {
              // 5/18 BL-EMAIL-DELETE: 删除成功后从列表移除 + 清详情. 不重新拉
              // list_fetch (avoid 网络 + 抖动), Mail.app 那边已经移到 Trash, 列表
              // 反映即可.
              if (selectedId) {
                setItems((prev) => prev.filter((it) => it.id !== selectedId));
                setSelectedId(null);
              }
            }}
          />
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
        {/* 评级 badge — 急=红 / 中=黄 / 低=灰 / 未评=空.
            5/18 BL-EMAIL-URGENCY-BADGE: 老逻辑 "中=不显" 让用户以为没评级, 实际是
            已评但被藏起来. 鸿波反馈"现在邮件没有任何优先级"就是这问题. 改 中
            也显黄色 chip, 三色齐全用户看得见. */}
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
        {urgency === "中" && (
          <span
            style={{
              flex: "0 0 auto",
              fontSize: 9,
              padding: "1px 5px",
              background: "rgba(251, 191, 36, 0.18)",
              color: "rgb(180, 130, 20)",
              borderRadius: 3,
              fontWeight: 500,
            }}
          >
            中
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
  onDeleted,
}: {
  msg: FullMessage;
  onAskCatfish: (m: FullMessage) => void;
  onDeleted: () => void;  // 5/18 BL-EMAIL-DELETE: 删除成功 → 父组件移除 item
}) {
  const [drafting, setDrafting] = useState(false);
  const [draftResult, setDraftResult] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  /** 5/18 BL-EMAIL-DELETE: 两步确认 — 第一次点 "🗑 删除" 切到 "再次点击确认" 状态,
   *  第二次点才真删. 3s 后自动取消恢复初态. 比 window.confirm 在 Tauri WebView
   *  下可靠 (有些场景 confirm 被吞), 也比系统 dialog 打扰. */
  const [confirmPending, setConfirmPending] = useState(false);

  // 取消 confirm 状态的 timer
  useEffect(() => {
    if (!confirmPending) return;
    const t = window.setTimeout(() => setConfirmPending(false), 3000);
    return () => window.clearTimeout(t);
  }, [confirmPending]);

  // 选不同邮件时清 state, 防上封邮件的 error / confirm 残留
  useEffect(() => {
    setConfirmPending(false);
    setDeleteError(null);
  }, [msg.id]);

  const handleDelete = async () => {
    if (!confirmPending) {
      // 第一次点 — 进 "再次确认" 状态, 不调 CLI
      setConfirmPending(true);
      setDeleteError(null);
      return;
    }
    // 第二次点 — 真删
    setConfirmPending(false);
    setDeleting(true);
    setDeleteError(null);
    console.log("[BL-EMAIL-DELETE] 调用 emailDeleteMessage", msg.id);
    try {
      const result = await emailDeleteMessage(msg.id);
      console.log("[BL-EMAIL-DELETE] 删除成功", result);
      onDeleted();
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[BL-EMAIL-DELETE] 删除失败:", errMsg);
      setDeleteError(errMsg);
    } finally {
      setDeleting(false);
    }
  };

  // 5/18 BL-EMAIL-COMPOSE-SEND: compose panel state — 点 "起草回复" 后展开,
  // 显示可编辑 to/cc/subject/body, 员工 review/编辑 → 点 "✉ 发送" 真发.
  // 红线: AI 不能绕过这个 panel 直接 send, 必须人工在 panel 里点按钮.
  const [composing, setComposing] = useState(false);
  const [composeTo, setComposeTo] = useState("");
  const [composeCc, setComposeCc] = useState("");
  const [composeSubject, setComposeSubject] = useState("");
  const [composeBody, setComposeBody] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [sendResult, setSendResult] = useState<string | null>(null);
  const [sendConfirmPending, setSendConfirmPending] = useState(false);

  // 3s 自动取消 send confirm
  useEffect(() => {
    if (!sendConfirmPending) return;
    const t = window.setTimeout(() => setSendConfirmPending(false), 3000);
    return () => window.clearTimeout(t);
  }, [sendConfirmPending]);

  // 选别的邮件时清 compose 状态
  useEffect(() => {
    setComposing(false);
    setSendError(null);
    setSendResult(null);
    setSendConfirmPending(false);
  }, [msg.id]);

  /** 5/18 BL-EMAIL-COMPOSE-SEND: 点 "起草回复" 不再立即建 draft, 而是开
   *  compose panel 让员工编辑. 这样:
   *    - 取消不留 orphan draft
   *    - 编辑后再 send 不会跟 Mail.app 那侧的草稿不一致 */
  const handleOpenCompose = () => {
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
    setComposeTo(replyTo);
    setComposeCc("");
    setComposeSubject(subj);
    setComposeBody(body);
    setSendError(null);
    setSendResult(null);
    setSendConfirmPending(false);
    setComposing(true);
  };

  /** 只保存到 Drafts, 不发. 等价于老 handleDraftReply 行为 (但用 panel 的内容). */
  const handleSaveDraft = async () => {
    setDrafting(true);
    setDraftResult(null);
    setDraftError(null);
    try {
      const resultJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: msg.id,
        account: msg.account,
      });
      const parsed = JSON.parse(resultJson);
      setDraftResult(parsed?.draft_id ?? "ok");
      setComposing(false);  // 保存成功关 panel
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  };

  /** 真发送: 两步 confirm, 第一次切 "再次点击确认", 第二次真发.
   *  实现: 先 emailCreateDraft 拿 id, 再 emailSendMessage(id). */
  const handleSendNow = async () => {
    if (!sendConfirmPending) {
      setSendConfirmPending(true);
      setSendError(null);
      return;
    }
    setSendConfirmPending(false);
    setSending(true);
    setSendError(null);
    setSendResult(null);
    console.log("[BL-EMAIL-COMPOSE-SEND] 起草+发送", { to: composeTo, subject: composeSubject });
    try {
      const draftJson = await emailCreateDraft({
        to: composeTo,
        cc: composeCc || undefined,
        subject: composeSubject,
        body: composeBody,
        inReplyTo: msg.id,
        account: msg.account,
      });
      const draftParsed = JSON.parse(draftJson);
      const draftId = draftParsed?.draft_id;
      if (!draftId) {
        throw new Error("起草返回没 draft_id, 无法发送");
      }
      console.log("[BL-EMAIL-COMPOSE-SEND] draft 已建, 现在 send", draftId);
      const sendJson = await emailSendMessage(draftId);
      console.log("[BL-EMAIL-COMPOSE-SEND] 发送成功", sendJson);
      setSendResult("✓ 已发送");
      // 2s 后关 panel
      setTimeout(() => {
        setComposing(false);
        setSendResult(null);
      }, 2000);
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[BL-EMAIL-COMPOSE-SEND] 发送失败:", errMsg);
      setSendError(errMsg);
    } finally {
      setSending(false);
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
            onClick={handleOpenCompose}
            disabled={drafting || composing}
            style={{
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 16px",
              fontSize: 13,
              cursor: composing ? "default" : "pointer",
              fontFamily: "inherit",
            }}
            title="打开 compose 面板, 编辑回复内容 + 选择保存草稿或发送 (人工 confirm 才发)"
          >
            {composing ? "📝 编辑中…" : "✏️ 回复"}
          </button>
          {/* 5/18 BL-EMAIL-DELETE: 两步点击确认 (window.confirm 在 Tauri 不可靠).
              第一次点 → "🗑 再次点击确认" (3s 内有效), 第二次才真删. */}
          <button
            type="button"
            onClick={() => void handleDelete()}
            disabled={deleting}
            style={{
              background: confirmPending ? "rgba(220, 80, 60, 0.15)" : "var(--catfish-bg)",
              color: "rgb(220, 80, 60)",
              border: `1px solid ${confirmPending ? "rgb(220, 80, 60)" : "rgba(220, 80, 60, 0.4)"}`,
              borderRadius: 4,
              padding: "8px 14px",
              fontSize: 13,
              fontWeight: confirmPending ? 600 : 400,
              cursor: deleting ? "wait" : "pointer",
              fontFamily: "inherit",
              marginLeft: "auto",
            }}
            title={
              confirmPending
                ? "再次点击确认删除 (3s 内有效, 否则自动取消)"
                : "把这封邮件移到客户端 Trash 文件夹 (软删, 30 天内可恢复)"
            }
          >
            {deleting
              ? "删除中…"
              : confirmPending
                ? "🗑 再次点击确认 (3s)"
                : "🗑 删除"}
          </button>
          {deleteError && (
            <div
              style={{
                width: "100%",
                marginTop: 8,
                padding: "8px 12px",
                background: "rgba(220, 80, 60, 0.1)",
                border: "1px solid rgba(220, 80, 60, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(220, 80, 60)",
                lineHeight: 1.5,
              }}
            >
              <strong>✗ 删除失败</strong>
              <br />
              {deleteError}
              {deleteError.includes("不支持") && (
                <>
                  <br />
                  <span style={{ opacity: 0.85 }}>
                    (Foxmail Mac 没暴露删除 IPC, 我们试过直接动 sqlite 但 IMAP
                    同步会把邮件从 server 拉回 INBOX 让操作无效. 请打开 Foxmail
                    客户端自己删 — Foxmail 会通知 server, 然后下次 Companion
                    刷新就看不到这封了.)
                  </span>
                </>
              )}
            </div>
          )}
          {draftResult && (
            <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
              ✓ 草稿已落 Mail Drafts (5/18 起 Companion 也能直接发了, 用 "✏️ 回复")
            </span>
          )}
          {draftError && (
            <span style={{ fontSize: 11, color: "rgb(185, 28, 28)" }}>
              起草失败: {draftError}
            </span>
          )}
        </div>
      </div>

      {/* 5/18 BL-EMAIL-COMPOSE-SEND: compose panel - composing=true 时取代正文区显. */}
      {composing ? (
        <div
          style={{
            flex: 1,
            overflowY: "auto",
            padding: "var(--space-4)",
            fontSize: 13,
            background: "var(--catfish-bg)",
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {/* 红线提示横幅 */}
          <div
            style={{
              padding: "8px 12px",
              background: "rgba(34, 197, 94, 0.1)",
              border: "1px solid rgba(34, 197, 94, 0.3)",
              borderRadius: 4,
              fontSize: 11,
              color: "rgb(21, 128, 61)",
              lineHeight: 1.5,
            }}
          >
            ✏️ <strong>Compose 模式</strong> — 编辑下面内容, "💾 仅保存草稿"
            落 Mail.app Drafts; "✉ 发送" 真发出去 (两步 confirm).
            红线: AI 永远不能绕过这步直接 send, 必须你人工点按钮.
          </div>

          {/* to */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>收件人</span>
            <input
              type="text"
              value={composeTo}
              onChange={(e) => setComposeTo(e.target.value)}
              placeholder="alice@x.com, bob@y.com"
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* cc */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>抄送</span>
            <input
              type="text"
              value={composeCc}
              onChange={(e) => setComposeCc(e.target.value)}
              placeholder="可选, 多人逗号分隔"
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* subject */}
          <label style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ width: 50, color: "var(--catfish-text-muted)", flex: "0 0 auto" }}>主题</span>
            <input
              type="text"
              value={composeSubject}
              onChange={(e) => setComposeSubject(e.target.value)}
              style={{
                flex: 1,
                background: "var(--catfish-bg-elevated)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "6px 10px",
                fontSize: 13,
                fontFamily: "inherit",
              }}
            />
          </label>

          {/* body */}
          <textarea
            value={composeBody}
            onChange={(e) => setComposeBody(e.target.value)}
            placeholder="正文..."
            style={{
              flex: 1,
              minHeight: 200,
              background: "var(--catfish-bg-elevated)",
              color: "var(--catfish-text)",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              padding: "8px 12px",
              fontSize: 13,
              lineHeight: 1.6,
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />

          {/* 错误 / 成功提示 */}
          {sendError && (
            <div
              style={{
                padding: "8px 12px",
                background: "rgba(220, 80, 60, 0.1)",
                border: "1px solid rgba(220, 80, 60, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(220, 80, 60)",
                lineHeight: 1.5,
              }}
            >
              <strong>✗ 发送失败</strong>
              <br />
              {sendError}
            </div>
          )}
          {sendResult && (
            <div
              style={{
                padding: "8px 12px",
                background: "rgba(34, 197, 94, 0.1)",
                border: "1px solid rgba(34, 197, 94, 0.3)",
                borderRadius: 4,
                fontSize: 12,
                color: "rgb(21, 128, 61)",
              }}
            >
              {sendResult} (2 秒后关闭)
            </div>
          )}

          {/* 行动按钮区 */}
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button
              type="button"
              onClick={() => {
                setComposing(false);
                setSendConfirmPending(false);
                setSendError(null);
              }}
              disabled={sending}
              style={{
                background: "transparent",
                color: "var(--catfish-text-muted)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                cursor: sending ? "wait" : "pointer",
                fontFamily: "inherit",
              }}
            >
              × 取消
            </button>
            <button
              type="button"
              onClick={() => void handleSaveDraft()}
              disabled={sending || drafting}
              style={{
                background: "var(--catfish-bg)",
                color: "var(--catfish-text)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                cursor: drafting ? "wait" : "pointer",
                fontFamily: "inherit",
              }}
              title="保存到 Mail.app Drafts, 不发送. 等会儿去 Mail.app 改完自己发."
            >
              {drafting ? "保存中…" : "💾 仅保存草稿"}
            </button>
            <button
              type="button"
              onClick={() => void handleSendNow()}
              disabled={sending || !composeTo.trim()}
              style={{
                background: sendConfirmPending ? "rgba(34, 197, 94, 0.15)" : "var(--catfish-cyan)",
                color: sendConfirmPending ? "rgb(21, 128, 61)" : "#fff",
                border: sendConfirmPending
                  ? "1px solid rgb(21, 128, 61)"
                  : "1px solid var(--catfish-cyan)",
                borderRadius: 4,
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: sendConfirmPending ? 600 : 500,
                cursor: sending ? "wait" : (!composeTo.trim() ? "not-allowed" : "pointer"),
                fontFamily: "inherit",
                marginLeft: "auto",
              }}
              title={
                sendConfirmPending
                  ? "再次点击确认发送 (3s 内有效)"
                  : "起草 + 发送邮件. 红线: 必须人工点这个按钮."
              }
            >
              {sending
                ? "发送中…"
                : sendConfirmPending
                  ? "✉ 再次点击确认 (3s)"
                  : "✉ 发送"}
            </button>
          </div>
        </div>
      ) : (
        /* 正文 (非 compose 时) */
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
      )}
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
