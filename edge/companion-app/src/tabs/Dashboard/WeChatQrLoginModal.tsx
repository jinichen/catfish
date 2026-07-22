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
import { config } from "../../lib/env";

// P3.5.201 (P38): Modal 侧不再 invoke Rust hermes_kill. plugin.py P38 已排
// asyncio 3s 后 SIGUSR1 → hermes drain + exit → launchd 拉起. Modal 只做
// 显进度: 3.5s 后 poll /health 直到通 (最多 45s), 通了显 healthy auto-close.
//
// /health 直接 fetch, bypass Rust hermes_status. api_server.py:1157 handler
// 无 _check_auth 保护, 匿名 GET 即可. hermes 断线期 catch swallow, poll 继续
// 到看到 200 为止.
async function probeHermesHealth(): Promise<boolean> {
  try {
    // BL-CSP-PROXY (7/18 鸿波): 走 Rust reqwest 代理, CSP 严格.
    const { fetchViaProxy } = await import("../../lib/http_proxy");
    const base = config.backendUrl.replace(/\/+$/, "");
    const r = await fetchViaProxy(`${base}/health`, { method: "GET" });
    return r.ok;
  } catch {
    return false;
  }
}

interface Props {
  onClose: () => void;
  onConfirmed: (info: { account_id: string; user_id: string }) => void;
}

// P3.5.201: 砍了 killing 状态 (hermes plugin.py P38 自己 SIGUSR1, Modal 不
// 主动 kill). 只保留 idle → waiting → healthy/timeout.
type RestartPhase =
  | { kind: "idle" }
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
// P3.5.200 (P37 7/8 鸿波 从时间实测锁): hermes 冷启动 (被 kill 后 launchd
// 501 recovery + Python 大量 import + gateway 绑 8642) 员工 mac 上实测
// 20+ 秒. warm 环境 (fs cache 热) 只需 3 秒, 但 P32 场景就是冷启动. 20s
// deadline 卡在临界 (hermes P36 log fire 时 TCP 端口还没绑起来 curl /health
// 返 000). 45s 足够 hermes 冷启动 + 8642 绑 + /health 200 全流程 +
// 留 buffer. UX 上员工看 loading 45s 也可接受 (比出错重来快).
const HERMES_HEALTHZ_TIMEOUT_MS = 45_000;

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

  // P3.5.201 (P38 7/8 鸿波军规审判 — 撤 P32-P37 中间层):
  //
  // # 老路径的错
  //
  // P32-P37 让 Companion 在 Modal 侧 invoke Rust `hermes_kill` → pgrep + kill -9
  // + `sh -lc 'hermes gateway start'` + poll `/health` 20s→45s. 8 段代码, 3 层
  // 依赖 (Rust command / shell PATH / launchd 501 recovery). 员工反馈"是不是把
  // 问题搞复杂了".
  //
  // # hermes 原生就有 SIGUSR1 graceful restart (audit 结果)
  //
  // - gateway/run.py:19362-19364 gateway 注册 SIGUSR1 handler
  // - gateway/run.py:5973 request_restart(via_service=True) drain in-flight
  // - launchd `<KeepAlive>true</>` (员工机 plutil 验) 自动拉起
  // - 员工机实测 kill -USR1 → 3s launchd 起新 PID
  //
  // # P38 新路径
  //
  // 1. plugin.py P30 confirmed → P31 sync .env → schedule asyncio 3s 后 SIGUSR1
  // 2. hermes drain + exit → launchd 自动拉起 → 新 hermes 读新 .env
  // 3. Modal 只做**显进度**: 收 confirmed → 显 "hermes 后台切换中" → poll
  //    /health (直接 fetch, 不走 Rust) → 通了显 "✓ 切换完成" → auto-close
  //
  // Companion 从 hermes 生命周期主导者退回**观察者**. 装机零手动.
  //
  // # 边界
  //
  // 1. restartStartedRef 保证 Modal 生命周期内只跑一次
  // 2. setPhase 用 functional updater 避 stale closure
  // 3. hermes SIGUSR1 → drain in-flight (包括当前 confirmed 那个 request 的
  //    response flush) → exit. 前端等 3-5s 就 poll 开始.
  // 4. poll /health 直接 fetch (bypass Rust hermes_status /healthz bug, 反正
  //    /health 无 auth 保护), catch 忽略 (hermes 断线期间连不上, 正常)
  // 5. 45s 内 healthy → 显成功 auto-close. 否则显 "hermes 可能还在起, 检查
  //    Dashboard 状态卡片" 不算失败, 员工可自己关.
  const restartStartedRef = React.useRef(false);
  React.useEffect(() => {
    if (phase.kind !== "confirmed") return;
    if (restartStartedRef.current) return;
    restartStartedRef.current = true;

    (async () => {
      if (!aliveRef.current) return;
      // 立即切 waiting — plugin.py P38 已排 3s SIGUSR1, hermes 后台开始 restart
      setPhase((prev) =>
        prev.kind === "confirmed"
          ? { ...prev, restart: { kind: "waiting" } }
          : prev,
      );

      // 从收到 confirmed 到 hermes drain + exit + launchd 拉起 + 8642 绑好
      // 通常 5-15 秒 (kill -USR1 → 3s launchd 拉起 → 5-10s python import).
      // 前几秒 hermes 还没 exit /health 依然 200, 之后断线期 000, 再之后新 hermes
      // 起来 200. poll 一直到看到 /health 200 为止 (不 track 中间断线).
      //
      // 但有个坑: hermes 没 exit 前 /health 一直 200, poll 立刻通然后 close
      // — 员工其实还没切好账号. 加个 minWait 3.5s (>P38 排的 3s) 让 hermes
      // 先 exit, 再开始 poll 才有意义.
      await new Promise((r) => setTimeout(r, 3500));
      if (!aliveRef.current) return;

      const deadline = Date.now() + HERMES_HEALTHZ_TIMEOUT_MS;
      let healthy = false;
      while (Date.now() < deadline) {
        if (!aliveRef.current) return;
        await new Promise((r) => setTimeout(r, HERMES_HEALTHZ_POLL_MS));
        if (await probeHermesHealth()) {
          healthy = true;
          break;
        }
      }
      if (!aliveRef.current) return;
      if (healthy) {
        setPhase((prev) =>
          prev.kind === "confirmed"
            ? { ...prev, restart: { kind: "healthy" } }
            : prev,
        );
        window.setTimeout(() => {
          if (aliveRef.current) onClose();
        }, 1200);
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
              {/* P3.5.201 撤 idle UI 分支: idle 只闪现几毫秒, useEffect 立即
                  切 waiting. 显 idle 造成 flash of unstyled content — 员工反正
                  看不到, 删了让 UI 直接 waiting. */}
              {phase.restart.kind === "waiting" && (
                <span>
                  ⏳ hermes 后台切换账号中 (通常 10-30 秒)…
                  <br />
                  <span style={{ opacity: 0.7, fontSize: 11 }}>
                    hermes 优雅 drain in-flight 请求 + launchd 自动拉起. chat
                    可能短暂延迟, 起来后自动恢复.
                  </span>
                </span>
              )}
              {phase.restart.kind === "healthy" && (
                <span style={{ color: "var(--status-ok, #2a8b3f)" }}>
                  ✓ hermes 已用新账号启动, ClawBot 应该能收发消息了
                </span>
              )}
              {phase.restart.kind === "timeout" && (
                <span style={{ color: "var(--status-warn, #c98b00)" }}>
                  hermes 45s 内没探到健康, 可能还在起.
                  <br />
                  <span style={{ opacity: 0.85, fontSize: 11 }}>
                    Dashboard → 服务/配额 卡片看 hermes 状态. 极少数需要手动
                    <code> hermes gateway restart</code> 强制重启.
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
