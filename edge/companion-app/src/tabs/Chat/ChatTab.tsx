/** 对话 tab 顶层 —— 左侧 SessionList sidebar | 右侧 ChatPanel。
 *
 * Plan C Week 3 (P0-3.1 + 3.2):
 *   - 左侧拉 ~/.hermes/state.db sessions, 区分 cli/companion 来源
 *   - 点列表项 → sessions_get 拉全部 messages → ChatStore.loadSession
 *   - "+ 新对话" → reset store, persistedSessionId 清空, 下次 send 自动新建
 *   - 流式输出中, sidebar 切换 / 新建按钮禁用 (避免条件竞争)
 */

import { useCallback, useEffect, useState } from "react";
import { useChat } from "../../hooks/useChat";
import { useChatStore } from "../../store/chat";
import { useCatalog } from "../../hooks/useCatalog";
import { getSession, sessionGetTaskUid } from "../../lib/tauri";
import ChatPanel from "./ChatPanel";
import ChatModelPicker from "./ChatModelPicker";
import ChatSidebar from "./ChatSidebar";
// P3.5.17.c.2 (6/17): ContextCounter 砍 — cumulative cost ≠ ctx 占用, 同款数学错.
// import ContextCounter from "./ContextCounter";  // BL-CONTEXT-COUNTER (5/13)
// P3.3.19 C Phase 3 (6/11): 工作台 chat 加 task picker, 让员工直接从工作台进 task 上下文
// (跟早安 DetailPane 共享同一 session, 哪边发都进同条 db row)
import TaskPicker from "./TaskPicker";

export default function ChatTab() {
  const { catalog } = useCatalog();
  const defaultModel =
    catalog?.default || catalog?.models?.[0]?.id || "catfish-private-main";

  const {
    messages,
    isStreaming,
    streamingId,
    model,
    setModel,
    send,
    cancel,
    cancelAndSend,  // BL-COMPANION-UX1 (5/12): 一键停止+发新消息
    enqueue,        // BL-HERMES013-RED-1A (5/13 ACP /queue): 排队下一条
    steer,          // BL-HERMES013-RED-1B (5/13 ACP /steer): 中途插话改方向
    reset,
  } = useChat(defaultModel);

  const persistedSessionId = useChatStore((s) => s.persistedSessionId);
  const loadSession = useChatStore((s) => s.loadSession);
  const loadSessionAttachments = useChatStore((s) => s.loadSessionAttachments);
  // P3.5.8 BL-FILE-SESSION-INDEX-V1 Phase 2 (6/16 鸿波): resume 时还原历史 image
  // base64 到 message.attachments, 让后续 toWire 走 multipart 带图给 vision LLM.
  // 老 Phase 1 只持久化 metadata, 切走 session / 重启 Companion 后历史图在 wire
  // 里失踪, gateway user_multipart=0, 私有 vision 模型空跑, 小鲶编借口.
  const restoreImageAttachments = useChatStore((s) => s.restoreImageAttachments);

  // P3.3.19 C Phase 3 (6/11): 当前 session 关联的 task_uid (null = 普通对话).
  // sidecar catfish_session_metadata 查. 每次 session 切换重拉.
  const [currentTaskUid, setCurrentTaskUid] = useState<string | null>(null);
  useEffect(() => {
    if (!persistedSessionId) {
      setCurrentTaskUid(null);
      return;
    }
    let cancelled = false;
    void sessionGetTaskUid(persistedSessionId)
      .then((uid) => {
        if (!cancelled) setCurrentTaskUid(uid);
      })
      .catch((e) => {
        if (!cancelled) {
          console.warn("[ChatTab] sessionGetTaskUid 失败:", e);
          setCurrentTaskUid(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [persistedSessionId]);

  /** 父组件持有 sidebar 的 refresh key —— 发完一条消息后 bump 让左侧列表重拉 */
  const [refreshKey, setRefreshKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  /** BL-COMPANION-UX2 (5/12): streaming 中点列表切换.
   *
   * BL-MULTI-SESSION-STREAM (5/24 鸿波): 老逻辑切走前 cancel() abort 当前流, 长任务
   * 被打断. 新逻辑跟 streamRegistry 配合 — 切走**不再 abort**, 老 stream 在 registry
   * 里继续跑直到完成, 持久化到 db (用 ctx.sessionId 锁定写到对的 session). 用户切回
   * 来时 loadSession 从 db 读最新内容, 看到 stream 还在 / 已完成.
   *
   * BL-SWITCH-CONFIRM (5/24): 老的二次确认也撤掉 — 切走没"破坏性后果"了, 别打扰用户.
   * sidebar 上的 ⏳ 徽章 (Step 3.5) 会告诉员工"那条还在跑".
   */
  const handleSelect = useCallback(
    async (id: string) => {
      if (id === persistedSessionId) return; // 点的就是当前
      // ★ 不再 cancel! stream 继续在 registry 里跑.
      try {
        const detail = await getSession(id);
        loadSession(detail);
        // BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): 异步拉该 session 历史附件 list.
        // 不阻塞 loadSession (附件慢一点显出来不影响读消息). store 内部有 race 防护.
        void loadSessionAttachments(id);
        // P3.5.8 Phase 2 (6/16): 同上 fire-and-forget — 异步读图片 base64 还原
        // message.attachments. UI 先显纯文字 + "[📎 1 张图]" 占位, base64 拉好后
        // patch 进 messages. 切走 session 时 store 用 persistedSessionId 防 race
        // 不污染新会话.
        void restoreImageAttachments(detail);
        setLoadError(null);
      } catch (e) {
        console.error("[catfish chat] 切换会话失败:", e);
        setLoadError(`切换失败: ${e}`);
      }
    },
    [persistedSessionId, loadSession, loadSessionAttachments, restoreImageAttachments],
  );

  /** BL-COMPANION-UX2 (5/12): streaming 中也能点"+ 新对话".
   * BL-MULTI-SESSION-STREAM (5/24): 同 handleSelect, 开新对话**不再 abort** 老 stream.
   * 老 stream 在 registry 里继续跑完, 自己持久化. 新对话独立 reset store.
   */
  const handleNew = useCallback(async () => {
    if (messages.length === 0 && !persistedSessionId) return;
    // ★ 不再 cancel! 老 stream 在 streamRegistry 里自跑完.
    reset();
    setLoadError(null);
  }, [messages.length, persistedSessionId, reset]);

  /** 包一层 send: 完成后 bump refreshKey 让 sidebar 看到新会话 / 新 message_count */
  const handleSend = useCallback(
    async (text: string, attachments: import("../../types/chat").Attachment[] = []) => {
      await send(text, attachments);
      setRefreshKey((k) => k + 1);
    },
    [send],
  );

  // BL-COMPANION-UX1 (5/12): streaming 中员工想发新消息, 一键 abort+发.
  // 跟 handleSend 同款包装 (setRefreshKey 给 session 列表刷新).
  const handleCancelAndSend = useCallback(
    async (text: string, attachments: import("../../types/chat").Attachment[] = []) => {
      await cancelAndSend(text, attachments);
      setRefreshKey((k) => k + 1);
    },
    [cancelAndSend],
  );

  // 5/7 BL-D14: 当前选中的 session 也自动 polling — 5s 一次重拉 detail.
  // 让微信 / 飞书 / 企微进来的新消息自动 append 到 chat 面板.
  //
  // 关键约束 (防体验崩):
  //   1. streaming 中不 poll (会跟当前流式响应冲突)
  //   2. 没选中 session 不 poll
  //   3. message_count 没变就不重 load (避免 React 重渲染抖动)
  useEffect(() => {
    if (!persistedSessionId) return;
    if (isStreaming) return;
    const POLL_MS = 5000;
    let lastMsgCount = messages.length;
    const t = window.setInterval(async () => {
      if (isStreaming) return;
      try {
        const detail = await getSession(persistedSessionId);
        // detail.meta.messageCount 是 state.db 真实总数
        // 如果跟当前 messages 长度差了 (微信/飞书/企微 进来新消息), reload
        const dbCount = detail.meta?.messageCount ?? 0;
        if (dbCount > lastMsgCount) {
          lastMsgCount = dbCount;
          loadSession(detail);
          // BL-FILE-SESSION-INDEX-V1 Phase 1: polling 拉新消息时同步刷附件 list
          // (微信/飞书新消息可能带附件 metadata)
          void loadSessionAttachments(persistedSessionId);
          // 同时 bump refreshKey, sidebar 也跟着刷
          setRefreshKey((k) => k + 1);
        }
      } catch {
        // 网络抖 / state.db 锁 — silent, 下次 tick 再试
      }
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [persistedSessionId, isStreaming, messages.length, loadSession, loadSessionAttachments]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        height: "100%",
        minHeight: 0,
      }}
    >
      <ChatSidebar
        activeId={persistedSessionId}
        onSelect={handleSelect}
        onNew={handleNew}
        refreshKey={refreshKey}
        busy={isStreaming}
      />

      <div
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          height: "100%",
        }}
      >
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "var(--space-3) var(--space-4)",
            borderBottom: "1px solid var(--catfish-border)",
            background: "var(--catfish-bg-elevated)",
            gap: "var(--space-3)",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              fontSize: 13,
              fontWeight: 600,
              minWidth: 0,
            }}
          >
            {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 小尺寸正式头像 */}
            {/* P3.3.22 (6/11): flex-shrink: 0 + nowrap — 防 TaskPicker 加进来后
                "对话" 两字被竖向压扁 (header 一行塞太多撞挤). */}
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                flexShrink: 0,
                whiteSpace: "nowrap",
              }}
            >
              <img src="/catfish-avatar.svg" alt="" width={18} height={18} style={{ display: "block" }} />
              对话
            </span>
            {persistedSessionId && (
              <span
                title={persistedSessionId}
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                  fontFamily: "var(--font-mono, ui-monospace, monospace)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  // P3.3.22 (6/11): 200 → 140, 给 TaskPicker 让位
                  maxWidth: 140,
                  flexShrink: 1,
                }}
              >
                · {persistedSessionId}
              </span>
            )}
            {messages.length > 0 && (
              <span
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  fontWeight: 400,
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                }}
              >
                · {messages.length} 条
              </span>
            )}
          </div>

          {/* P3.3.22 (6/11): 右组 flex-shrink: 0 防被左组 sessionId 撑挤. */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              flexShrink: 0,
            }}
          >
            {/* P3.3.19 C Phase 3 (6/11): task picker — 让员工从工作台直接进
                早安 task 上下文聊. 选了 task 走跟 DetailPane 同 sessionId. */}
            <TaskPicker
              currentTaskUid={currentTaskUid}
              onSelectSession={(sid) => {
                if (sid) {
                  void handleSelect(sid);
                } else {
                  void handleNew();
                }
              }}
              model={model}
              disabled={isStreaming}
            />
            {/* P3.5.17.c.2 (6/17): ContextCounter 砍 — cumulative cost / ctx_window
                数学错, 误导. hermes 自带 ContextCompressor 处理 ctx, 不需 Companion 算. */}
            <ChatModelPicker current={model} onChange={setModel} />
          </div>
        </header>

        {loadError && (
          <div
            style={{
              padding: "8px 12px",
              fontSize: 12,
              background: "rgba(220, 38, 38, 0.08)",
              color: "#dc2626",
              borderBottom: "1px solid var(--catfish-border)",
            }}
          >
            {loadError}
          </div>
        )}

        <SessionAttachmentsBar />

        <div style={{ flex: 1, minHeight: 0 }}>
          <ChatPanel
            messages={messages}
            isStreaming={isStreaming}
            streamingId={streamingId}
            onSend={handleSend}
            onCancel={cancel}
            onCancelAndSend={handleCancelAndSend}
            onEnqueue={enqueue}
            onSteer={steer}
            onReset={reset}
          />
        </div>
      </div>
    </div>
  );
}

/** BL-FILE-SESSION-INDEX-V1 Phase 1 (5/30): session 顶部历史附件 chip bar.
 *  切回老会话时显示该会话所有上传过的附件 (从 ~/.catfish/attachments.db 读).
 *  无附件不渲染. 点击 chip 展开 (后续做), 当前先列名字.
 *
 *  跟 LLM 那边的 catfish_search_attachments 工具数据源是同一张表 —
 *  UI 看到的就是 LLM 能搜的. 双向一致.
 */
function SessionAttachmentsBar() {
  const sessionAttachments = useChatStore((s) => s.sessionAttachments);
  if (sessionAttachments.length === 0) return null;
  // 去重按 name (同一文件可能被 record 多次 — 比如同一附件在多轮提及)
  const byName = new Map<string, typeof sessionAttachments[number]>();
  for (const a of sessionAttachments) {
    if (!byName.has(a.name)) byName.set(a.name, a);
  }
  const unique = Array.from(byName.values()).slice(0, 12);
  const more = byName.size > unique.length ? byName.size - unique.length : 0;
  return (
    <div
      title="本会话历史附件 — 切回会话时从本机 db 恢复, LLM 也能通过工具查到"
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 6,
        padding: "6px 12px",
        fontSize: 11,
        background: "var(--catfish-bg-elevated)",
        borderBottom: "1px solid var(--catfish-border)",
        color: "var(--catfish-text-muted)",
      }}
    >
      <span style={{ marginRight: 4 }}>📎 本会话历史附件:</span>
      {unique.map((a) => {
        const iconByKind: Record<string, string> = {
          image: "🖼",
          file: "📄",
          audio: "🎙",
        };
        const icon = iconByKind[a.kind] ?? "📎";
        return (
          <span
            key={a.id}
            title={`${a.name}${a.sizeBytes ? ` · ${(a.sizeBytes / 1024).toFixed(1)} KB` : ""}${a.keptPath ? `\n${a.keptPath}` : ""}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "2px 8px",
              borderRadius: 10,
              background: "rgba(14, 158, 140, 0.12)",
              color: "var(--catfish-accent, #0e9e8c)",
              maxWidth: 220,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <span>{icon}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {a.name}
            </span>
          </span>
        );
      })}
      {more > 0 && (
        <span style={{ marginLeft: 4, fontStyle: "italic" }}>
          +{more} 更多
        </span>
      )}
    </div>
  );
}
