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
  emailCheckNew,                  // P3.5.204.c (7/9 鸿波): 触发客户端 IMAP/POP fetch
  emailMailDirStatus,             // 8/8: 缺完全磁盘访问权限时提示"少账号"
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
// P3.5.204 (7/9): 复用 P3.5.58 isReplied 算法给 ListItem 显 "↩ 已回复" badge.
import { isReplied } from "../../lib/emailThread";

// P3.5.58 Phase 3 (6/22 鸿波 catch "邮件数量没有 100, 为什么一直显示 100, 是不是
// 硬编码了"): list 拉取上限. Rust 端 email_list_fetch clamp(1, 500), 这里取最大
// 不再硬编码 100. items.length >= 此值时 header 加 "+" 提示被 cap 截.
const MAX_EMAIL_LIST_LIMIT = 500;

export default function EmailTab() {
  const [items, setItems] = useState<EmailDigestItem[]>([]);
  // P3.5.204.b (7/9): Sent (已发送) 邮件独立存. 只用于 isReplied 判定 (员工回
  // 复过的邮件 in_reply_to 指向 Inbox 里被回复邮件的 message_id, R.in_reply_to
  // 检索需要 R 在 list 里, 但 R 存 Sent 不在 Inbox items → repliedMap 永远返
  // false → replied badge 显示不出来). 不合并进 items (列表还是显 Inbox), 只
  // 拿来算 repliedMap.
  const [sentItems, setSentItems] = useState<EmailDigestItem[]>([]);
  const [accounts, setAccounts] = useState<EmailAccountItem[]>([]);
  // BL-COMPANION-EMAIL-DIGEST-STEP5 (5/20): urgencyMap 走 useEmailStore
  // (localStorage hydrate + Rust reconcile + 跨 tab 共享, 不再 local useState).
  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const setUrgencyMap = useEmailStore((s) => s.setUrgencyMap);
  const reconcileUrgency = useEmailStore((s) => s.reconcileFromRust);
  const markEmailRead = useEmailStore((s) => s.markRead);
  // 8/8 鸿波: 默认列全部, 不再默认「仅未读」。
  //
  // 默认只给未读的问题是: 邮件页打开就是一个**残缺的收件箱** —— 员工要找
  // 昨天那封已经读过的, 得先意识到有个勾选框、再去取消它。而"未读"是个
  // 会自己变的状态: 点开看一眼就没了, 于是刚看过的邮件从列表里消失,
  // 想回头找反而找不到。
  //
  // toggle 保留 —— 想只看未读仍然一勾就有, 只是不再是进门时的默认。
  const [unreadOnly, setUnreadOnly] = useState(false);
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
      const [listJson, sentJson, accountsJson, _urgency] = await Promise.all([
        // P3.5.58 Phase 3 (6/22 鸿波 catch "邮件数量没有 100, 为什么一直显示
        // 100, 是不是硬编码了"): 真因 — 之前硬编码 limit=100, INBOX ≥100 封时
        // items.length 永远 100, header 显"100 封"实是被 cap 截了不告诉 user.
        // 改成 Rust 端 clamp 上限 500 (clamp(1, 500) 见 email.rs:89), 比 100 大
        // 5x. 一般员工 INBOX < 500 封, 真能看见全量. ≥500 时 header 显"500+"
        // 提示 user 真值被截 (见 headerSummary).
        emailListFetch(unreadOnly, MAX_EMAIL_LIST_LIMIT),
        // P3.5.204.b (7/9): 并行拉 Sent (最近 200 封), 只用于 isReplied 数据源.
        // 出错不阻塞 (Sent 拉不到只影响 replied badge, 不影响主流程) → catch 空数组.
        emailListFetch(false, 200, "Sent").catch(() => "[]"),
        emailAccountsFetch().catch(() => "[]"),
        // BL-COMPANION-EMAIL-DIGEST-STEP5: 走 store.reconcileFromRust 后台拉,
        // setUrgencyMap 不再这里调 — store 内部自己 merge + 持久化
        reconcileUrgency().catch(() => ({})),
      ]);
      void _urgency;
      const list = JSON.parse(listJson);
      const sent = JSON.parse(sentJson);
      const accs = JSON.parse(accountsJson);
      if (Array.isArray(list)) setItems(list as EmailDigestItem[]);
      if (Array.isArray(sent)) setSentItems(sent as EmailDigestItem[]);
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

  // P3.5.204.c (7/9 鸿波 catch "客户端还没同步的邮件在鲶鱼里无法激活客户端去同步"):
  // "收信" 按钮 — 先触发客户端 IMAP/POP fetch (catfish-email check), 拉完再 loadList.
  // check 出错 best-effort (Foxmail 不支持 / 网络不通) → 不阻断 loadList, 让员工至少
  // 能看到当前 DB 里的邮件.
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const handleCheckNew = useCallback(async () => {
    setSyncing(true);
    setSyncError(null);
    try {
      await emailCheckNew();
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
      // check 完 (无论成败) 立即 refetch, 让新拉到的邮件立即入列表.
      void loadList();
    }
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

  // P3.5.204 (7/9 鸿波 catch "回复过的邮件怎么没有标志"):
  // isReplied 算法 (P3.5.58 6/22) 已经存在, DetailPane 也在用, 但 ListItem
  // 没读 → 员工在左侧列表看不到"已回复"提示, 必须点开邮件看右侧详情才知道.
  //
  // P3.5.204.b (7/9): 用 items.concat(sentItems) 算. Sent 邮件是员工回信的
  // R (in_reply_to 指向 Inbox 里被回复邮件的 message_id), 只有 Sent 也在 list
  // 里 isReplied 才能匹配到. items 仅 Inbox → replied badge 从来显不出来 —
  // 之前 P3.5.204 半吊子的原因. 用 sentItems 补数据源.
  //
  // 一次 O((N+S)²) 算全表 repliedMap 传给每 ListItem O(1) 读. Sent 只用于算
  // 法, 不显在列表.
  // 8/6: combined 提到外面 —— repliedMap 和 DetailPane 的 repliedPool 共用同一份.
  const combined = useMemo(() => items.concat(sentItems), [items, sentItems]);

  const repliedMap = useMemo(() => {
    const m = new Map<string, boolean>();
    for (const it of items) {
      const r = isReplied(it, combined);
      if (r.replied) m.set(it.id, true);
    }
    return m;
  }, [items, combined]);

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

  // 8/8 (鸿波实撞): 授予完全磁盘访问权限前邮件页只有 1 个账号, 授予后 5 个。
  //
  // ~/Library/Mail 受 macOS TCC 保护, 没权限时 catfish-email 会**安静跳过**
  // Apple Mail 只用 Foxmail —— 不崩了 (5db6e8f), 但界面上一个字都不说, 员工只
  // 觉得"怎么少了几个邮箱", 完全联想不到是系统权限。跟今天早上 advisor 静默
  // 404 是同一类病: 降级了但没人知道。
  //
  // 只在 "no_access" 时挂提示: 目录压根没有 (真没用 Apple Mail) 返 "ok",
  // 不打扰只用 Foxmail 的人。
  const [mailDirBlocked, setMailDirBlocked] = useState(false);
  useEffect(() => {
    let cancelled = false;
    emailMailDirStatus()
      .then((s) => { if (!cancelled) setMailDirBlocked(s === "no_access"); })
      // 探测失败不该影响邮件本身 —— 顶多少一条提示
      .catch(() => { /* 忽略 */ });
    return () => { cancelled = true; };
  }, []);

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
            {/* P3.5.204.f (7/9 鸿波 catch "收信中的时候又出现太宽的问题, 你把邮件的那个图标去掉"):
                左栏 340px 硬编码, header flex 塞 📧 + 邮件 + summary + 新建 + 收信中…,
                收信中… 4 字比 收信 2 字宽, 触发全行溢出 → title 竖排"邮"↵"件". 删 📧
                省 icon(20) + gap(8) = 28px, 边界够. */}
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
                // P3.5.204.e (7/9 鸿波 catch "按钮上的图标不要了, 太浪费空间"):
                // 左栏窄 (grid 1fr 主区拿大头), padding + emoji + 中文塞不下, 触发
                // 换行 "新" ↵ "建". 删 emoji + nowrap 兜底, 未来 layout 变也不再换.
                whiteSpace: "nowrap",
              }}
            >
              新建
            </button>
            {/* P3.5.204.c (7/9 鸿波 catch "客户端还没同步的邮件在鲶鱼里无法激活客户端去同步"):
                "收信" 按钮触发 Apple Mail 立即 IMAP/POP fetch, 完了自动 refetch 列表.
                P3.5.204.d (7/9 鸿波 catch "有了收信按钮, 为什么还刷新, 不是冗余吗"):
                之前留的 ⟳ 只重刷 DB, 是"收信"的严格子集 (收信 = check + refetch).
                两个按钮让员工二选一制造决策疲劳, 违背军规极简原则. 删 ⟳, 只留收信. */}
            <button
              type="button"
              onClick={() => void handleCheckNew()}
              disabled={syncing || loading}
              title={
                syncError
                  ? `上次收信失败: ${syncError} (点再试一次)`
                  : "让邮件客户端立即从邮箱服务器收取新邮件, 再刷新列表"
              }
              style={{
                background: syncError ? "rgba(200, 80, 80, 0.10)" : "transparent",
                border: `1px solid ${syncError ? "rgba(200, 80, 80, 0.35)" : "var(--catfish-border)"}`,
                borderRadius: 4,
                color: syncError ? "rgb(200, 80, 80)" : "var(--catfish-text-muted)",
                cursor: syncing || loading ? "wait" : "pointer",
                fontSize: 11,
                padding: "2px 8px",
                fontFamily: "inherit",
                whiteSpace: "nowrap",  // P3.5.204.e: 同"新建", 防窄栏换行
              }}
            >
              {syncing || loading ? "收信中…" : "收信"}
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

          {/* 缺完全磁盘访问权限的提示 —— **独占一行, 不能塞进上面 header**。
            *
            * 8/8 我第一版把它做成 header 概要后面的一个 "⚠ 少账号?" chip, 当场把
            * 标题挤成竖排的"邮"↵"件"。而这个坑文件里就记着 (P3.5.204.f):
            * 左栏 340px 硬编码, header 那一行塞 邮件 + 概要 + 新建 + 收信中…
            * 已经是临界的, 上次为了腾 28px 才把 📧 图标删掉 —— 我转手加了 5 个字回去。
            *
            * 独占一行还有个好处: 说得下人话。chip 只能塞四五个字, 员工看不懂要做什么。 */}
          {mailDirBlocked && (
            <div
              style={{
                fontSize: 11,
                lineHeight: 1.5,
                color: "var(--catfish-hint-amber-text)",
                background: "var(--catfish-hint-amber-bg)",
                border: "1px solid var(--catfish-hint-amber-border)",
                borderRadius: "var(--radius-sm)",
                padding: "6px 8px",
                marginBottom: 6,
              }}
            >
              ⚠ 有邮箱账号读不到 —— 缺「完全磁盘访问权限」。
              <br />
              系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 打开「鲶鱼 Companion」→ 重启。
              <br />
              <span style={{ opacity: 0.8 }}>
                注: 每次重装 Companion 都要重授一次 (app 还没做代码签名, 系统当成新程序)。
              </span>
            </div>
          )}
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
            {/* 7/30: 原文让员工去跑 `bash edge/email-agent/install.sh` ——
                员工手里只有一个 dmg, 根本没有那个目录, 这条提示等于没给。
                (而且 catfish-email 长期就没被打包过, 见 hermes_install.rs
                 的 install_catfish_email。) 改成员工真能做的两件事。 */}
            <div style={{ marginTop: 6, fontSize: 11, opacity: 0.7 }}>
              常见原因：Mail.app 没打开 · 系统没给「自动化」权限（系统设置 →
              隐私与安全性 → 自动化，勾上鲶鱼下面的「邮件」）。
              都正常还是不行，去仪表盘点「重新安装 Hermes」；仍然不行请找 IT。
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
              replied={repliedMap.get(m.id)}  // P3.5.204 (7/9)
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
            <div style={{ fontSize: 11 }}>或 点顶部"新建"写一封新邮件</div>
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
            // 8/6: 算「已回复」要 Sent, 传含 Sent 的 combined
            repliedPool={combined}
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

