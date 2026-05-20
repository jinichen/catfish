/** RecMode ErrorBanner — 抽自 RecModeButton.tsx (5/20 拆分).
 *
 * 录制 / 分析 失败时显错误模态. 主按钮: 重试 / 关闭.
 */

import { useRecModeStore } from "../../../store/recmode";

import { ModalShell, T } from "./shared";


function ErrorBanner() {
  const errorMessage = useRecModeStore((s) => s.errorMessage);
  const errorCategory = useRecModeStore((s) => s.errorCategory);
  const reset = useRecModeStore((s) => s.reset);
  const openSetup = useRecModeStore((s) => s.openSetup);

  // G: 按 category 给针对性 hint + 修法
  const categoryInfo: Record<string, { title: string; hint: string; canRetry: boolean }> = {
    cdp_unavailable: {
      title: "Catfish Chrome 没起",
      hint: "去 Companion '控制台' 点'启动 Catfish Chrome', 起好后重试. 或确认 Chrome 用 --remote-debugging-port=9222 启动.",
      canRetry: true,
    },
    whisper_failed: {
      title: "录音失败",
      hint: "ffmpeg 或 whisper.cpp 跑挂了. 看 Companion '控制台' 错日志. 没语音也能跑 RecMode (只是 main 综合时少一类信号), 重试吧.",
      canRetry: true,
    },
    aggregator_timeout: {
      title: "鲶鱼分析超时",
      hint: "main 综合 5-10 分钟录屏一般 30-90 秒, 超时通常是录得太长 (>20 min) 或内网 LLM 排队. 重试; 还慢就拆短录屏.",
      canRetry: true,
    },
    llm_parse_failed: {
      title: "鲶鱼输出格式错",
      hint: "main 综合输出的 JSON parse 不了. 通常是 SYSTEM_PROMPT 没卡住格式. 把 questions_for_user 反馈我们调 prompt. 重录可能 OK.",
      canRetry: true,
    },
    network: {
      title: "网络错",
      hint: "catfish-gateway 不可达 / VPN 抖动. 看 gateway 是不是起着 (`pkill -f catfish_gateway` 然后重启).",
      canRetry: true,
    },
    unknown: {
      title: "RecMode 出错",
      hint: "原始错: " + (errorMessage || "(无详情)"),
      canRetry: true,
    },
  };
  const info = categoryInfo[errorCategory || "unknown"] || categoryInfo.unknown;

  return (
    <ModalShell onClose={reset}>
      <button
        onClick={reset}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      <div style={{
        width: 56,
        height: 56,
        borderRadius: 14,
        background: `linear-gradient(135deg, ${T.errorRed}, #ff6b3d)`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        marginBottom: 18,
        boxShadow: `0 6px 16px ${T.errorRed}40`,
      }}>
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round">
          <line x1="12" y1="8" x2="12" y2="13" />
          <circle cx="12" cy="17" r="0.5" fill="white" />
        </svg>
      </div>

      <h3 style={{
        margin: 0,
        fontSize: 19,
        fontWeight: 600,
        color: T.text,
        fontFamily: T.systemFont,
      }}>
        {info.title}
      </h3>
      <p style={{
        margin: "8px 0 0",
        fontSize: 13,
        color: T.textSecondary,
        lineHeight: 1.5,
        fontFamily: T.systemFont,
      }}>
        {info.hint}
      </p>

      {/* 折叠原始错信息 */}
      {errorCategory !== "unknown" && (
        <details style={{ marginTop: 14, fontSize: 11, color: T.textTertiary, fontFamily: T.systemFont }}>
          <summary style={{ cursor: "pointer" }}>原始错信息</summary>
          <pre style={{
            marginTop: 6,
            padding: 10,
            background: "#f5f5f7",
            borderRadius: 6,
            fontSize: 11,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: T.textSecondary,
          }}>
            {errorMessage}
          </pre>
        </details>
      )}

      <div style={{
        display: "flex",
        gap: 8,
        marginTop: 22,
        paddingTop: 16,
        borderTop: `1px solid ${T.border}`,
        alignItems: "center",
      }}>
        <button
          onClick={reset}
          style={{
            padding: "9px 18px",
            border: "none",
            borderRadius: 8,
            background: "transparent",
            color: T.textSecondary,
            fontSize: 14,
            fontFamily: T.systemFont,
            cursor: "pointer",
          }}
        >
          关闭
        </button>
        <div style={{ flex: 1 }} />
        {info.canRetry && (
          <button
            onClick={openSetup}
            style={{
              padding: "9px 22px",
              border: "none",
              borderRadius: 8,
              background: T.cyan,
              color: "white",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: T.systemFont,
              cursor: "pointer",
              boxShadow: `0 2px 6px ${T.cyan}40`,
            }}
          >
            🔄 重试
          </button>
        )}
      </div>
    </ModalShell>
  );
}


export default ErrorBanner;
