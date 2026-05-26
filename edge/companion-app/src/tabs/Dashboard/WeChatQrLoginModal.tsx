/** BL-WECHAT-CATFISH-BIND v3 (5/26 鸿波): 微信二维码登录 modal.
 *
 * # 流程
 *   1. mount → 调 wechatQrStart 拿 scan_data
 *   2. 用 npm qrcode 渲染成 canvas
 *   3. 每 2s 轮询 wechatQrPoll, 状态文字实时更新
 *   4. 状态 = "confirmed" → 显示成功信息 + 调 onConfirmed callback (父组件触发 reload)
 *   5. 状态 = "expired" → 显示过期 + 一键重试按钮
 *
 * # 故意不做
 *   - 不弹原生 dialog: Tauri WebView 行为不稳, 走 in-app overlay
 *   - 不重试 poll 错误: 网络抖动让下次 poll 自然恢复; 真挂 (expired) 才提示
 *   - 不显示 qrcode_url 全文给员工看: ilink 内部地址, 没意义还吓人
 */

import * as React from "react";
import QRCode from "qrcode";

import { wechatQrStart, wechatQrPoll, type QrPollStatus } from "../../lib/wechat_qr";

interface Props {
  onClose: () => void;
  onConfirmed: (info: { account_id: string; user_id: string }) => void;
}

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; qrcode: string; scan_data: string; status: QrPollStatus }
  | { kind: "confirmed"; account_id: string; user_id: string }
  | { kind: "expired" }
  | { kind: "error"; message: string };

const POLL_INTERVAL_MS = 2000;

export default function WeChatQrLoginModal({ onClose, onConfirmed }: Props) {
  const [phase, setPhase] = React.useState<Phase>({ kind: "loading" });
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const aliveRef = React.useRef(true);

  const startSession = React.useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const s = await wechatQrStart();
      if (!aliveRef.current) return;
      setPhase({
        kind: "ready",
        qrcode: s.qrcode,
        scan_data: s.scan_data,
        status: "wait",
      });
    } catch (e) {
      if (!aliveRef.current) return;
      setPhase({ kind: "error", message: String(e) });
    }
  }, []);

  React.useEffect(() => {
    aliveRef.current = true;
    void startSession();
    return () => {
      aliveRef.current = false;
    };
  }, [startSession]);

  // 渲染二维码到 canvas
  React.useEffect(() => {
    if (phase.kind !== "ready") return;
    const cv = canvasRef.current;
    if (!cv) return;
    QRCode.toCanvas(cv, phase.scan_data, {
      width: 240,
      margin: 2,
      errorCorrectionLevel: "M",
    }).catch((e: unknown) => {
      setPhase({ kind: "error", message: `二维码渲染失败: ${e}` });
    });
  }, [phase]);

  // 轮询 status
  React.useEffect(() => {
    if (phase.kind !== "ready") return;
    if (phase.status === "confirmed" || phase.status === "expired") return;

    const handle = window.setInterval(async () => {
      if (!aliveRef.current) return;
      try {
        const r = await wechatQrPoll(phase.qrcode);
        if (!aliveRef.current) return;
        if (r.status === "confirmed") {
          setPhase({
            kind: "confirmed",
            account_id: r.account_id ?? "",
            user_id: r.user_id ?? "",
          });
          onConfirmed({
            account_id: r.account_id ?? "",
            user_id: r.user_id ?? "",
          });
        } else if (r.status === "expired") {
          setPhase({ kind: "expired" });
        } else {
          setPhase({ ...phase, status: r.status });
        }
      } catch {
        // 不动 phase, 等下次 poll 自然恢复; 真挂会变 expired
      }
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(handle);
  }, [phase, onConfirmed]);

  // 状态文案
  let statusText = "";
  if (phase.kind === "ready") {
    switch (phase.status) {
      case "wait":
        statusText = "请用微信扫一扫上方二维码";
        break;
      case "scaned":
        statusText = "✓ 已扫码 — 请在手机微信里点确认";
        break;
      case "confirmed":
        statusText = "✓ 已确认, 正在保存…";
        break;
      case "expired":
        statusText = "二维码已过期";
        break;
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--catfish-bg-elevated, #1a1d23)",
          border: "1px solid var(--catfish-border, #2a2f37)",
          borderRadius: 8,
          padding: 20,
          width: 360,
          maxWidth: "90vw",
          color: "var(--catfish-text, #e6e8eb)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <h3 style={{ margin: 0, fontSize: 15 }}>📱 微信扫码绑定</h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              marginLeft: "auto",
              background: "transparent",
              border: "none",
              color: "var(--catfish-text-muted, #8b9099)",
              cursor: "pointer",
              fontSize: 18,
              padding: 0,
              lineHeight: 1,
            }}
          >
            ✕
          </button>
        </div>

        {phase.kind === "loading" && (
          <div style={{ textAlign: "center", padding: "60px 0", fontSize: 13 }}>
            正在生成二维码…
          </div>
        )}

        {phase.kind === "error" && (
          <div>
            <div
              style={{
                color: "var(--status-err, #c93a3a)",
                fontSize: 13,
                marginBottom: 10,
              }}
            >
              {phase.message}
            </div>
            <button type="button" onClick={() => void startSession()} style={btnPrimary}>
              重试
            </button>
          </div>
        )}

        {phase.kind === "ready" && (
          <div>
            <div
              style={{
                background: "#fff",
                borderRadius: 6,
                padding: 12,
                display: "flex",
                justifyContent: "center",
              }}
            >
              <canvas ref={canvasRef} />
            </div>
            <p
              style={{
                textAlign: "center",
                marginTop: 12,
                fontSize: 13,
                color:
                  phase.status === "scaned"
                    ? "var(--status-ok, #2a8b3f)"
                    : "var(--catfish-text, #e6e8eb)",
              }}
            >
              {statusText}
            </p>
            <p
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted, #8b9099)",
                marginTop: 8,
                textAlign: "center",
                lineHeight: 1.6,
              }}
            >
              扫码 = 把你的微信号挂到 ClawBot 上, ClawBot 替你监听这个微信收发的消息.
              <br />
              数据本地保存到 <code>~/.hermes/</code>, 不上传中央.
            </p>
          </div>
        )}

        {phase.kind === "confirmed" && (
          <div style={{ textAlign: "center", padding: "30px 0" }}>
            <div style={{ fontSize: 36, marginBottom: 10 }}>🎉</div>
            <div style={{ fontSize: 14, marginBottom: 6 }}>微信绑定成功</div>
            <div
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted, #8b9099)",
                fontFamily: "monospace",
                wordBreak: "break-all",
              }}
            >
              账号 {phase.account_id || "(未知)"}
            </div>
            <p
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted, #8b9099)",
                marginTop: 14,
                lineHeight: 1.6,
              }}
            >
              ClawBot 需要重启才会用新账号. <br />
              终端跑 <code>hermes setup</code> 选 wechat 走一次, 或重启 hermes 服务.
            </p>
            <button
              type="button"
              onClick={onClose}
              style={{ ...btnPrimary, marginTop: 16 }}
            >
              完成
            </button>
          </div>
        )}

        {phase.kind === "expired" && (
          <div style={{ textAlign: "center", padding: "30px 0" }}>
            <div style={{ fontSize: 13, marginBottom: 14 }}>二维码已过期</div>
            <button type="button" onClick={() => void startSession()} style={btnPrimary}>
              重新生成
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const btnPrimary: React.CSSProperties = {
  background: "var(--catfish-accent, #2a7fbb)",
  border: "1px solid var(--catfish-accent, #2a7fbb)",
  color: "#fff",
  padding: "6px 16px",
  borderRadius: 4,
  cursor: "pointer",
  fontSize: 13,
};
