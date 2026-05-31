/** 主动闲聊 Dashboard 卡 — BL-E13 C-MVP (五一 sprint 5/2 收尾).
 *
 * 显示 LLM 上下文感知 starter (gateway 读 journal + 时段 + qwen-flash 生成).
 * 点 "起这个话题 →" → 切到 chat tab + 预填问题, 员工按 Enter 或编辑后发.
 *
 * 30 分钟自动刷新 starter (时段会变).
 *
 * 数据来源: GET /api/proactive/starter
 */

import { useEffect, useState } from "react";

import { fetchProactiveStarter, type ProactiveStarter } from "../../lib/me";
import { useUIStore } from "../../store/ui";
import { useAgentStore } from "../../store/agent";

const REFRESH_MS = 30 * 60 * 1000;  // 30 分钟

export default function ProactiveCard() {
  const [starter, setStarter] = useState<ProactiveStarter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // BL-PROACTIVE-CARD-SLIM (5/16): testStatus state + 测一下按钮整链路砍.
  // sendNotification / petIsVisible / petEmitBubble import 同时清.
  const startProactiveChat = useUIStore((s) => s.startProactiveChat);
  // BL-E11 后续: 按钮用员工自定义名 ("跟老李聊聊 →")
  const agentName = useAgentStore((s) => s.name);

  const load = async () => {
    setLoading(true);
    try {
      const s = await fetchProactiveStarter();
      if (s) {
        setStarter(s);
        setError(null);
      } else {
        // BL-LONG-RUNNING-V1-FOLLOWUP (5/31): "看 gateway 起没起" 是开发文案,
        // 员工看不懂. 换成员工视角 — null 返回基本就是离线/未登录, 跟 401 一样
        // 友好提示, 不暴露内部组件名.
        setError("__OFFLINE__");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 401 / unauthorized / 网络都归一成 OFFLINE, 显示统一友好提示
      if (/401|unauthorized|network|fetch/i.test(msg)) {
        setError("__OFFLINE__");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        // BL-FIX15 (5/8): 撑满 grid cell, 跟同行的 TasksCard 高度对齐
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0 }}>📝 今日话题</h3>
        {/* 5/5 鸿波拍板: 不暴露内部状态. 'LLM 不可用' / 'fallback' 这种术语员工没意义,
            改成更口语的友好话术. 真要看 source 走 dev tools / metrics 看 audit log. */}
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {starter?.source === "llm" ? `${agentName}帮你想的` : starter?.source === "fallback" ? "默认话题" : "..."}
        </span>
        <button
          onClick={() => void load()}
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
          title="换一个话题"
        >
          换一个
        </button>
      </div>

      {loading && !starter && (
        <div style={{ height: 60, background: "var(--catfish-border)", opacity: 0.4, borderRadius: 4 }} />
      )}

      {error && !starter && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)", lineHeight: 1.5 }}>
          {error === "__OFFLINE__" ? (
            <>
              暂时拉不到话题. 可能是没登录或在离线 — 30s 自动重试.
              <br />
              <span style={{ fontSize: 11 }}>
                你跟我聊的内容仍在你本机, 不受影响.
              </span>
            </>
          ) : (
            error
          )}
        </div>
      )}

      {starter && (
        <>
          <p
            style={{
              fontSize: 15,
              lineHeight: 1.5,
              margin: "var(--space-2) 0 var(--space-3) 0",
              color: "var(--catfish-text)",
            }}
          >
            {starter.starter}
          </p>

          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <button
              onClick={() => startProactiveChat(starter.starter)}
              style={{
                flex: 1,
                padding: "8px 16px",
                fontSize: 14,
                background: "var(--catfish-cyan)",
                color: "white",
                border: "none",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                fontWeight: 500,
              }}
            >
              跟{agentName}聊聊 →
            </button>
            {/* BL-PROACTIVE-CARD-SLIM (5/16): 5/6 加的"测一下 ▶" 是 dev 调试用 (触发桌宠气泡 /
                通知 fallback / chat prefill 全链路), 普通员工根本看不懂. 跟 IdentityCard 同
                性质 — 该走"控制台" tab. 砍.
                原代码 ~70 行 + testStatus state 留作 git 历史 / 5/6 commit, 这里直接删. */}
          </div>
        </>
      )}
    </div>
  );
}
