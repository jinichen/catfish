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
import { sendNotification, petIsVisible, petEmitBubble } from "../../lib/tauri";
import { useUIStore } from "../../store/ui";
import { useAgentStore } from "../../store/agent";

const REFRESH_MS = 30 * 60 * 1000;  // 30 分钟

export default function ProactiveCard() {
  const [starter, setStarter] = useState<ProactiveStarter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 5/6 鸿波报"主动闲聊好像有问题" — "测一下 ▶" 后显示发送结果, 让员工知道
  // 是 emit 了还是 notify 了, 没收到时也能看到 root cause.
  const [testStatus, setTestStatus] = useState<string | null>(null);
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
        setError("拉不到话题, 看 gateway 起没起");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)" }}>
          {error}
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
            {/* 5/6 鸿波报"主动闲聊好像有问题": dev/test 时不用等 9:30/14:00/17:30,
                这个按钮立即触发 fireOne 全链路 — 桌宠气泡 / 通知 fallback / chat prefill 一起走. */}
            <button
              onClick={async () => {
                console.log("[proactive-card] 立即触发主动闲聊");
                setTestStatus("处理中...");
                startProactiveChat(starter.starter);
                let usedBubble = false;
                let bubbleErr: string | null = null;
                let notifyErr: string | null = null;
                try {
                  const visible = await petIsVisible();
                  console.log(`[proactive-card] 桌宠 visible=${visible}`);
                  if (visible) {
                    try {
                      // 5/6: 走 Rust pet_emit_bubble (frontend emitTo 不可靠), 拿诊断
                      const diag = await petEmitBubble(starter.starter, agentName);
                      console.log("[proactive-card] pet_emit_bubble Rust 返回诊断:", diag);
                      usedBubble = true;
                    } catch (e) {
                      bubbleErr = e instanceof Error ? e.message : String(e);
                      console.warn("[proactive-card] emit pet_bubble 失败:", e);
                    }
                  }
                } catch (e) {
                  bubbleErr = e instanceof Error ? e.message : String(e);
                  console.warn("[proactive-card] pet_is_visible 失败:", e);
                }
                if (!usedBubble) {
                  try {
                    console.log("[proactive-card] 走 macOS 通知 fallback...");
                    await sendNotification(
                      `${agentName}想跟你聊一句`,
                      starter.starter,
                    );
                    console.log(
                      "[proactive-card] sendNotification 已调 (osascript). 没看到通知 = macOS 通知权限没给, 系统设置 → 通知 → 允许 Catfish/osascript",
                    );
                    setTestStatus(
                      `📢 通知已发送 (Cmd+Shift+P 开桌宠改走气泡; 没收到通知 = macOS 系统设置→通知里给 Catfish/osascript 权限)`,
                    );
                  } catch (e) {
                    notifyErr = e instanceof Error ? e.message : String(e);
                    console.warn("[proactive-card] sendNotification 失败:", e);
                    setTestStatus(`❌ 通知失败: ${notifyErr}`);
                  }
                } else {
                  setTestStatus("💬 已 emit 桌宠气泡 (看桌宠头顶)");
                }
                if (bubbleErr && !usedBubble) {
                  setTestStatus(
                    (prev) => `${prev || ""}\n⚠️ pet 检测错: ${bubbleErr}`,
                  );
                }
                // 5 秒后清状态
                setTimeout(() => setTestStatus(null), 5000);
              }}
              title="测试主动闲聊全流程 (桌宠气泡 / 通知 / chat prefill)"
              style={{
                padding: "8px 12px",
                fontSize: 12,
                background: "transparent",
                color: "var(--catfish-text-muted)",
                border: "1px solid var(--catfish-border)",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
              }}
            >
              测一下 ▶
            </button>
          </div>

          {/* 5/6 测一下 ▶ 的 visual feedback */}
          {testStatus && (
            <div
              style={{
                marginTop: "var(--space-3)",
                padding: "8px 12px",
                fontSize: 12,
                background: "var(--catfish-bg)",
                border: "1px dashed var(--catfish-cyan-dim)",
                borderRadius: "var(--radius-sm)",
                color: "var(--catfish-text)",
                whiteSpace: "pre-line",
                lineHeight: 1.4,
              }}
            >
              {testStatus}
            </div>
          )}

          {/* 5/5 鸿波拍板: 删 "闲聊为了攒 demo 卖点" — 这是开发期内部 placeholder,
              不该到生产 UI. 留中性的"30 分钟换一次"提示员工话题会自动刷新. */}
          <div style={{ marginTop: "var(--space-3)", fontSize: 11, color: "var(--catfish-text-muted)" }}>
            30 分钟自动换一次
          </div>
        </>
      )}
    </div>
  );
}
