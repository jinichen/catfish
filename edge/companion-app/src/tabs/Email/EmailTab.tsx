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
  emailClassifyNow,
  emailPhishingScanNow,           // P3.3.58 段 2B (6/12 鸿波)
  emailPoliticalGet,              // P3.3.53.2 (6/13 鸿波): list 仅查已扫
  type EmailDigestItem,
  type EmailAccountItem,
  type PhishingScanResult,        // P3.3.58 段 2B
  type PoliticalScanResult,       // P3.3.53.2
} from "../../lib/tauri";
import { useEmailStore } from "../../store/email";
import { useUIStore } from "../../store/ui";
// P3.5.158 Phase 4 (7/2 鸿波): 新建邮件入口
import { useAgentStore } from "../../store/agent";
import ComposeCore from "./components/ComposeCore";

// 5/20: ListItem / DetailPane / helpers 抽到 components/ (拆 1204 → <500)
import DetailPane, { type FullMessage } from "./components/DetailPane";
import ListItem from "./components/ListItem";

// P3.5.58 Phase 3 (6/22 鸿波 catch "邮件数量没有 100, 为什么一直显示 100, 是不是
// 硬编码了"): list 拉取上限. Rust 端 email_list_fetch clamp(1, 500), 这里取最大
// 不再硬编码 100. items.length >= 此值时 header 加 "+" 提示被 cap 截.
const MAX_EMAIL_LIST_LIMIT = 500;

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
  // P3.3.58 段 2B (6/12 鸿波): 钓鱼扫描结果 in-memory map (重启丢, 跟 scheduler PHISHING_STORE 同步)
  const [phishingMap, setPhishingMap] = useState<Record<string, PhishingScanResult>>({});
  // P3.3.53.2 (6/13 鸿波): 政治敏感扫描结果. 跟 phishing 不同 — list 阶段不扫,
  // 仅 DetailPane 打开邮件时扫. 这里 emailPoliticalGet 只查 store 已扫过的,
  // 让员工看完 detail 切回 list 时 badge 能显. 不调 scan_now 批量 (避白扫).
  const [politicalMap, setPoliticalMap] = useState<Record<string, PoliticalScanResult>>({});
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
        // P3.5.58 Phase 3 (6/22 鸿波 catch "邮件数量没有 100, 为什么一直显示
        // 100, 是不是硬编码了"): 真因 — 之前硬编码 limit=100, INBOX ≥100 封时
        // items.length 永远 100, header 显"100 封"实是被 cap 截了不告诉 user.
        // 改成 Rust 端 clamp 上限 500 (clamp(1, 500) 见 email.rs:89), 比 100 大
        // 5x. 一般员工 INBOX < 500 封, 真能看见全量. ≥500 时 header 显"500+"
        // 提示 user 真值被截 (见 headerSummary).
        emailListFetch(unreadOnly, MAX_EMAIL_LIST_LIMIT),
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

  // P3.3.58 段 2B (6/12 鸿波): 钓鱼扫描. 跟 urgency 评级同款 — 已扫的跳过省 LLM,
  // 新 id 走 light scan + LLM batch. 失败静默不阻塞 UI.
  useEffect(() => {
    if (items.length === 0) return;
    const unscanned = items.filter((it) => !phishingMap[it.id]);
    if (unscanned.length === 0) return;
    let cancelled = false;
    (async () => {
      const BATCH = 20;
      for (let i = 0; i < unscanned.length; i += BATCH) {
        if (cancelled) return;
        const batch = unscanned.slice(i, i + BATCH);
        try {
          const updated = await emailPhishingScanNow(batch.map((it) => ({
            id: it.id,
            subject: it.subject,
            sender: it.sender,
            account: it.account,
            date: it.date,
            is_read: it.is_read,
          })));
          if (!cancelled) setPhishingMap(updated);
        } catch {
          // 扫描失败 (gateway 挂 / 规则 panic), badge 维持空白不阻塞
          return;
        }
      }
    })();
    return () => { cancelled = true; };
  }, [items, phishingMap]);

  // P3.3.53.2 (6/13 鸿波): 政治敏感 list 刷新 — 仅查已扫的 (DetailPane 打开过的
  // 邮件才在 POLITICAL_STORE 里). 不扫新 id, 避免对全量邮件白扫 (yaml 默认关时
  // 全部返 engineEnabled=false, 也是浪费). 真扫由 DetailPane 触发.
  useEffect(() => {
    if (items.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const map = await emailPoliticalGet(items.map((it) => it.id));
        if (!cancelled) setPoliticalMap(map);
      } catch {
        // 查失败静默 — 跟 phishing 一致
      }
    })();
    return () => { cancelled = true; };
  }, [items]);

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
  //
  // P3.5.158 Phase 4 (7/2 鸿波 catch "少了新建邮件功能"): 5/18 删的是跳系统
  // Mail.app 按钮 (mailto: 离开 catfish 到 Mail.app 手动写). catfish 内部
  // 一直没做纯粹的新建邮件入口. 现在补: 顶部 ✏️ 新建 按钮 → 右侧 render
  // ComposeCore (originalMessage=null 拟稿禁用, in_reply_to=null 走新建路径),
  // 取代空白态 "👈 左边选一封邮件看详情". backend email_create_draft 早已
  // 支持 in_reply_to Option=None 走新建, 只差 UI 入口, 这里补.

  const [newComposing, setNewComposing] = useState(false);
  const agentName = useAgentStore((s) => s.name);
  const agentPersonality = useAgentStore((s) => s.personality);
  // 新建邮件默认账号: is_default=true 那个, 兜底第一个
  const defaultAccount = useMemo(() => {
    const def = accounts.find((a) => a.is_default);
    return def?.address || accounts[0]?.address || undefined;
  }, [accounts]);

  const handleOpenNewCompose = () => {
    // 打开新建 panel — 清选中真邮件 (员工在新建, 不看任何 selected msg)
    setSelectedId(null);
    setNewComposing(true);
  };

  const headerSummary = useMemo(() => {
    if (error) return "拉取失败";
    if (loading && items.length === 0) return "加载中…";
    const unreadCnt = items.filter((i) => !i.is_read).length;
    // P3.5.58 Phase 3 (6/22 鸿波): items.length === MAX 时给 "+" 后缀提示是不是
    // 被 limit cap 截了, 让 user 知道实际更多 (而不是误以为正好等于 cap).
    const cap = items.length >= MAX_EMAIL_LIST_LIMIT ? "+" : "";
    return unreadOnly
      ? `${items.length}${cap} 封未读 · ${accounts.length} 个账号`
      : `${items.length}${cap} 封 · ${unreadCnt} 未读 · ${accounts.length} 个账号`;
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
              onClick={handleOpenNewCompose}
              disabled={newComposing}
              title="新建邮件 (给谁 / 主题 / 正文自己写, 保存草稿或直接发送)"
              style={{
                marginLeft: "auto",
                background: newComposing ? "var(--catfish-bg)" : "transparent",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                color: "var(--catfish-text-muted)",
                cursor: newComposing ? "default" : "pointer",
                opacity: newComposing ? 0.4 : 1,
                fontSize: 11,
                padding: "2px 8px",
                fontFamily: "inherit",
              }}
            >
              ✏️ 新建
            </button>
            <button
              type="button"
              onClick={() => void loadList()}
              disabled={loading}
              title="重新同步"
              style={{
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
              phishing={phishingMap[m.id]}  // P3.3.58 段 2B
              political={politicalMap[m.id]}  // P3.3.53.2
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
        {/* P3.5.158 Phase 4: 新建 compose panel (取代空白态). newComposing=true 时
            显 ComposeCore, originalMessage=null → 拟稿禁用. */}
        {!selectedId && newComposing && (
          <ComposeCore
            isOpen={newComposing}
            onClose={() => setNewComposing(false)}
            initialTo=""
            initialCc=""
            initialSubject=""
            initialBody=""
            inReplyToMsgId={null}
            account={defaultAccount}
            originalMessage={null}
            agentName={agentName}
            agentPersonality={agentPersonality}
            resetKey="new-compose"
          />
        )}

        {!selectedId && !newComposing && (
          <div
            style={{
              flex: 1,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "var(--catfish-text-muted)",
              fontSize: 13,
              flexDirection: "column",
              gap: 10,
            }}
          >
            <div>👈 左边选一封邮件看详情</div>
            <div style={{ fontSize: 11 }}>或 点顶部 ✏️ 新建 写一封新邮件</div>
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
            // P3.5.58 (6/22 鸿波): 全 list 传给 DetailPane 让 isReplied 算法可见
            // 所有邮件的 in_reply_to / references, 算"这封是不是已回复过".
            list={items}
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

