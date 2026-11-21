/** 换机迁移 · 从旧电脑直传 (新电脑这边, 11/21, docs/DEVICE-MIGRATION-DESIGN.md §12)。
 *
 * 点「开始」→ 这台现场生成一把一次性密钥, 屏幕上出二维码 + 8 位配对码 + 6 位核对数字 →
 * 旧电脑扫码 (或手输配对码, 再核对两边数字一样) → 迁移包用这台的公钥加密, 经公司服务器边传边删 →
 * 收齐、解密、逐文件校验 → 跟导入迁移包文件一样: 看包里有什么、确认导入、重启。
 * 离开这一页不打断 (后台接着收), 回来接着看进度。
 */
import * as React from "react";
import QRCode from "qrcode";

import {
  directImport,
  directReceiveCancel,
  directReceiveStart,
  directReceiveStatus,
  formatBytes,
  percent,
  type DirectRecvStatus,
  type MigrationImportReport,
} from "../lib/deviceMigration";
import { errBox, ImportDone, PackageCheck, plainBtn, primaryBtn } from "./DeviceMigrationParts";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const ACTIVE = new Set(["waiting", "receiving", "verifying"]);

export function ProgressBar({ value }: { value: number }) {
  return (
    <div style={{ height: 6, background: "var(--catfish-border)", borderRadius: 3, overflow: "hidden", margin: "6px 0", maxWidth: 360 }}>
      <div style={{ width: `${value}%`, height: "100%", background: "var(--catfish-cyan)", transition: "width .3s" }} />
    </div>
  );
}

function Qr({ text }: { text: string }) {
  const ref = React.useRef<HTMLCanvasElement>(null);
  React.useEffect(() => {
    if (ref.current) void QRCode.toCanvas(ref.current, text, { width: 200, margin: 1, errorCorrectionLevel: "M" });
  }, [text]);
  return <canvas ref={ref} width={200} height={200} style={{ background: "white", borderRadius: 4 }} aria-label="换机直传二维码" />;
}

export default function DeviceMigrationDirectReceive({ beforeRestart }: { beforeRestart?: () => void }) {
  const [st, setSt] = React.useState<DirectRecvStatus | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState<MigrationImportReport | null>(null);

  // 回到这一页: 接着看上次没完的
  React.useEffect(() => {
    directReceiveStatus().then(setSt).catch(() => {});
  }, []);
  const active = !!st && ACTIVE.has(st.state);
  React.useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      directReceiveStatus().then(setSt).catch(() => {});
    }, 1000);
    return () => clearInterval(t);
  }, [active]);

  const start = async () => {
    setError(null);
    setBusy(true);
    try {
      setSt(await directReceiveStart());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const cancel = async () => {
    await directReceiveCancel().catch(() => {});
    setSt(null);
  };
  const runImport = async (overwrite: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setDone(await directImport(overwrite));
    } catch (e) {
      setError(String(e));
      directReceiveStatus().then(setSt).catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  if (done) return <ImportDone done={done} beforeRestart={beforeRestart} />;

  if (!st || st.state === "cancelled") {
    return (
      <div style={{ lineHeight: 1.7 }}>
        <div style={muted}>
          这台出一个二维码, 在旧电脑上扫一下 (没摄像头就输配对码)。两台都要登录同一个账号。
          <br />
          资料加密后经公司服务器转过来, 服务器看不到内容、传完就删。
        </div>
        <button type="button" style={{ ...primaryBtn(busy), marginTop: 6 }} onClick={() => void start()} disabled={busy}>
          {busy ? "准备中…" : "开始: 在这台出二维码"}
        </button>
        {error && <div style={errBox}>🚫 {error}</div>}
      </div>
    );
  }

  if (st.state === "waiting") {
    return (
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <Qr text={st.qr} />
        <div style={{ lineHeight: 1.8, minWidth: 220 }}>
          <div style={{ fontWeight: 600 }}>在旧电脑上扫这个码</div>
          <div style={muted}>旧电脑: 设置 → 隐私 / 本机数据 → 换机迁移 → 这台是旧电脑 → 扫码</div>
          <div style={{ marginTop: 6 }}>
            没有摄像头就输配对码:{" "}
            <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 18, letterSpacing: 2, fontWeight: 600 }}>{st.code}</span>
          </div>
          <div>
            核对数字: <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 15, fontWeight: 600 }}>{st.safety}</span>
            <span style={muted}> (旧电脑上显示的要跟这个一样)</span>
          </div>
          <div style={muted}>半小时内有效。等旧电脑开始传…</div>
          <button type="button" style={{ ...plainBtn, marginTop: 6 }} onClick={() => void cancel()}>
            取消
          </button>
        </div>
      </div>
    );
  }

  if (st.state === "receiving" || st.state === "verifying") {
    const p = percent(st.got, st.n);
    return (
      <div style={{ lineHeight: 1.7 }}>
        <div>
          正在从 {st.fromDevice || "旧电脑"} 接收{st.state === "verifying" ? ", 收齐了, 正在解密校验…" : `… ${p}%`}
        </div>
        <ProgressBar value={st.state === "verifying" ? 100 : p} />
        <div style={muted}>
          {formatBytes(Math.min(st.got * 1024 * 1024, st.size))} / {formatBytes(st.size)} · 核对数字 {st.safety} · 两台电脑都别合盖、别关 Companion
        </div>
        <button type="button" style={{ ...plainBtn, marginTop: 6 }} onClick={() => void cancel()}>
          取消
        </button>
      </div>
    );
  }

  if (st.state === "ready" && st.info) {
    return (
      <div>
        <div style={{ color: "#16A34A", fontWeight: 600 }}>✓ 收齐了, 校验通过 (从 {st.fromDevice || "旧电脑"})</div>
        <PackageCheck info={st.info} busy={busy} onImport={(o) => void runImport(o)} />
        {error && <div style={errBox}>🚫 {error}</div>}
      </div>
    );
  }

  return (
    <div>
      <div style={errBox}>🚫 {st.error ?? error ?? "直传没成功"}</div>
      <button type="button" style={{ ...primaryBtn(busy), marginTop: 8 }} onClick={() => void start()} disabled={busy}>
        重新开始
      </button>
    </div>
  );
}
