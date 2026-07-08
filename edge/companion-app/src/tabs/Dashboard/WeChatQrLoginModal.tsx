/** BL-WECHAT-CATFISH-BIND v3 (5/26 鸿波): 微信二维码登录 modal.
 *
 * # 流程
 *   1. mount → 调 wechatQrStart 拿 scan_data
 *   2. 用 npm qrcode 渲染成 canvas
 *   3. 每 2s 轮询 wechatQrPoll, 状态文字实时更新
 *   4. 状态 = "confirmed" → 显示成功信息 + 调 onConfirmed callback (父组件触发 reload)
 *      + 1.8s 后自动 onClose (P3.5.198.c 7/8 鸿波: v3 老逻辑要员工手动点"完成",
 *      员工反馈"扫码成功后还留着窗口不合理" — 自动关掉减少交互步骤)
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
import { hermesKill, hermesStatus } from "../../lib/tauri";

// P3.5.198.h 一并根治 (7/8 鸿波军规审判):
//   hermes.rs probe_healthz 里 URL 走 `/healthz`, 但 hermes v0.18 (api_server.py:
//   4519-4521) 只有 `/health` + `/v1/health`, 没有 `/healthz`. 已改 hermes.rs
//   commands/hermes.rs:probe_healthz → /health, 一处修完, 全局 hermes_status
//   Tauri command 都正确了 (Dashboard 服务状态栏 + P32 auto-restart 都受益).

interface Props {
  onClose: () => void;
  onConfirmed: (info: { account_id: string; user_id: string }) => void;
}

type RestartPhase =
  | { kind: "idle" }
  | { kind: "killing" }
  | { kind: "waiting" }
  | { kind: "healthy" }
  | { kind: "timeout" };

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; qrcode: string; scan_data: string; status: QrPollStatus }
  | {
      kind: "confirmed";
      account_id: string;
      user_id: string;
      restart: RestartPhase;
    }
  | { kind: "expired" }
  | { kind: "error"; message: string };

const POLL_INTERVAL_MS = 2000;
const HERMES_HEALTHZ_POLL_MS = 1000;
const HERMES_HEALTHZ_TIMEOUT_MS = 20_000;

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
            restart: { kind: "idle" },
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

  // P3.5.198.g (7/8 鸿波军规审判 — P32): confirmed 后立即自动 kill hermes 让
  // launchd 拉起新进程用 P31 同步过的 .env 里最新 credential.
  //
  // # 真因 (7/8 06:xx audit 5 credentials 全部 Session expired 后严格审出)
  //
  // ilink 侧 bot_token 有短期 idle timeout (估计 5-10 min). P30 落盘 + P31
  // sync .env 之后, 如果员工不立刻 restart hermes, credential 在盘上悬挂 15 min+
  // 就被 ilink 侧作废. 5/7 和 5/23 老 credential 都是 hermes qr_login CLI 生成
  // 时立即 WeixinAdapter 就用 (`.context-tokens.json` 15-22 min 内出现证据链),
  // 但今天 5 个新 credential 全都没 `.context-tokens.json` — 都被员工手动
  // restart 之前的窗口耗死.
  //
  // # P32 强制立刻切换 credential
  //
  // 老 UX: 显 "🎉" + 1.8s auto-close (员工要自己开 terminal 敲 restart)
  // 新 UX: 显 "🎉" → 立刻 invoke("hermes_kill") → launchd 拉起新 hermes 起来
  //        (2-5s) → 前端 poll hermes_status.healthy 直到 true → 显示成功
  //        → 1s 后 close.
  //
  // # 副作用
  //
  // hermes 被 kill 期间 (~3-8s), 员工正在跑的其他 chat/SSE/poll 会短暂断线.
  // 这是必要副作用 — 员工扫码的目的就是切账号, 不切等于没扫. UI 明确提示.
  //
  // # 边界
  //
  // - hermes_kill 失败 (pgrep 找不到进程) → 显示错误, 不自动关 modal, 让员工
  //   手动重启 (fallback 到荒唐路径, 但至少可用)
  // - poll healthz 超时 (20s 内 hermes 起不来) → 显示"重启超时", 让员工检查
  //   hermes log 手动处理
  // - aliveRef: modal 提前关掉就中断 poll, 不 setState (防 unmount warning)
  // P32 加固 (P3.5.198.i 7/8 鸿波 catch log 铁证 useEffect 没 fire):
  //   老 useEffect deps 用 `phase.kind === "confirmed" ? phase.account_id : null`
  //   三元表达式, eslint-disable 掉 exhaustive-deps 后 React 判等有微妙问题.
  //   `setPhase({ ...phase, ... })` 里 phase 是 useEffect closure stale 值,
  //   连续多次 setPhase 用同一份 stale phase 展开. 现在改双保险:
  //     1. restartStartedRef 保证整个 modal 生命周期内 P32 flow 只跑一次
  //     2. setPhase 全部用 functional updater (prev => ...) 拿 React 最新 state
  //     3. deps 简化为 [phase.kind], 只在 loading/ready/confirmed/expired/error
  //        之间切换时 fire
  const restartStartedRef = React.useRef(false);
  React.useEffect(() => {
    if (phase.kind !== "confirmed") return;
    if (restartStartedRef.current) return;
    restartStartedRef.current = true;

    (async () => {
      // step 1: kill hermes → launchd 2-5s 自动拉起
      if (!aliveRef.current) return;
      setPhase((prev) =>
        prev.kind === "confirmed"
          ? { ...prev, restart: { kind: "killing" } }
          : prev,
      );
      try {
        await hermesKill();
      } catch (e) {
        console.warn("[P32] hermes_kill 失败:", e);
        if (!aliveRef.current) return;
        setPhase((prev) =>
          prev.kind === "confirmed"
            ? { ...prev, restart: { kind: "timeout" } }
            : prev,
        );
        return;
      }
      if (!aliveRef.current) return;
      setPhase((prev) =>
        prev.kind === "confirmed"
          ? { ...prev, restart: { kind: "waiting" } }
          : prev,
      );

      // step 2: poll hermes_status.healthy 直到通 (最多 20s). hermes.rs 里
      // P3.5.198.h 已修 URL 从 /healthz → /health.
      const deadline = Date.now() + HERMES_HEALTHZ_TIMEOUT_MS;
      let healthy = false;
      while (Date.now() < deadline) {
        if (!aliveRef.current) return;
        await new Promise((r) => setTimeout(r, HERMES_HEALTHZ_POLL_MS));
        try {
          const s = await hermesStatus();
          if (s.healthy) {
            healthy = true;
            break;
          }
        } catch {
          // hermes 还在起, 忽略 tick 继续 poll
        }
      }
      if (!aliveRef.current) return;
      if (healthy) {
        setPhase((prev) =>
          prev.kind === "confirmed"
            ? { ...prev, restart: { kind: "healthy" } }
            : prev,
        );
        // step 3: 给员工看 1s "已启用" 提示, 再 close
        window.setTimeout(() => {
          if (aliveRef.current) onClose();
        }, 1000);
      } else {
        setPhase((prev) =>
          prev.kind === "confirmed"
            ? { ...prev, restart: { kind: "timeout" } }
            : prev,
        );
      }
    })();
  }, [phase.kind, onClose]);

  // P3.5.198.e (7/8 鸿波): scaned 状态卡 15s 显示"已绑过"提示.
  //
  // # audit 起因
  // 员工反馈手机扫码后没弹出"确认登录"框, 直接跳到 ClawBot chat, modal 卡在
  // "已扫码 — 请在手机微信里点确认" 无限期. 结合 hermes 端 F1 提醒能发出说明
  // credential 早就在, ~/.hermes/weixin/accounts/*.json 已经有. 已绑用户扫码
  // ilink 只跳 chat 不给 confirm, poll status 一直 scaned, 前端等 confirmed 无
  // 望. 与其让员工困在这界面, 加个 15s 计时器显 UX 提示"你可能已绑过, 关这个
  // 窗口就好".
  //
  // # 边界
  // - 只在 ready + scaned 时计时, 切 confirmed / expired / error 自动清 timer
  // - 首次扫码 (真新用户) 从 scaned → confirmed 一般 < 5s, 15s hint 不会误伤
  // - 不 auto-close scaned 状态 (万一员工真在手机上翻找确认框, 别给他关掉)
  const [scanedTooLong, setScanedTooLong] = React.useState(false);
  React.useEffect(() => {
    if (phase.kind !== "ready" || phase.status !== "scaned") {
      setScanedTooLong(false);
      return;
    }
    const t = window.setTimeout(() => {
      if (aliveRef.current) setScanedTooLong(true);
    }, 15000);
    return () => window.clearTimeout(t);
  }, [phase]);

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
            {/* P3.5.198.e: scaned 卡 15s 提示 (已绑用户扫码不会跳 confirm). */}
            {phase.status === "scaned" && scanedTooLong && (
              <div
                style={{
                  fontSize: 12,
                  color: "var(--status-warn, #c98b00)",
                  marginTop: 10,
                  padding: 8,
                  background: "rgba(201, 139, 0, 0.08)",
                  borderRadius: 4,
                  lineHeight: 1.6,
                }}
              >
                手机没弹"确认登录"框? 可能你的微信号<strong>之前已经绑过 ClawBot</strong>,
                这次扫码微信直接跳到了 chat, 无需再确认. 直接关闭此窗口就好.
                <br />
                <span style={{ opacity: 0.7 }}>
                  已有 credential 在 <code>~/.hermes/weixin/accounts/</code>
                </span>
              </div>
            )}
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

            {/* P32: 重启 hermes 让 WeixinAdapter 立刻用新 credential 的进度显示. */}
            <div
              style={{
                marginTop: 18,
                padding: 10,
                background: "rgba(42, 127, 187, 0.08)",
                borderRadius: 4,
                fontSize: 12,
                lineHeight: 1.7,
                color: "var(--catfish-text, #e6e8eb)",
              }}
            >
              {phase.restart.kind === "idle" && (
                <span>准备重启 hermes 让 ClawBot 用新账号…</span>
              )}
              {phase.restart.kind === "killing" && (
                <span>⏳ 正在停 hermes…</span>
              )}
              {phase.restart.kind === "waiting" && (
                <span>
                  ⏳ hermes 正在起来 (通常 3–8 秒)…
                  <br />
                  <span style={{ opacity: 0.7, fontSize: 11 }}>
                    此期间 chat / 邮件 会短暂断线, 起来后自动恢复
                  </span>
                </span>
              )}
              {phase.restart.kind === "healthy" && (
                <span style={{ color: "var(--status-ok, #2a8b3f)" }}>
                  ✓ hermes 已用新账号启动, ClawBot 应该能收发消息了
                </span>
              )}
              {phase.restart.kind === "timeout" && (
                <span style={{ color: "var(--status-err, #c93a3a)" }}>
                  ⚠ 自动重启失败 / 超时 (20s).
                  <br />
                  <span style={{ opacity: 0.85, fontSize: 11 }}>
                    请手动敲 <code>hermes gateway stop && hermes gateway start</code>{" "}
                    完成切换, 或查看 hermes log.
                  </span>
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={onClose}
              style={{ ...btnPrimary, marginTop: 16 }}
            >
              {phase.restart.kind === "healthy" ? "完成" : "关闭"}
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
