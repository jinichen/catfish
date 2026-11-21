/** 换机迁移 · 直传到新电脑 (旧电脑这边, 11/21, docs/DEVICE-MIGRATION-DESIGN.md §12)。
 *
 * 扫新电脑屏幕上的二维码 (或手输 8 位配对码) → 核对两边的 6 位数字一样 → 打包, 用新电脑的公钥加密, 经公司服务器边传边删 →
 * 新电脑收齐后在那边导入。离开这一页不打断 (后台接着传)。
 */
import * as React from "react";

import {
  directFromQr,
  directLookup,
  directSend,
  directSendCancel,
  directSendStatus,
  formatBytes,
  normalizeCode,
  percent,
  type DirectSendStatus,
  type DirectTarget,
} from "../lib/deviceMigration";
import { ProgressBar } from "./DeviceMigrationDirectReceive";
import { errBox, inputStyle, okBox, plainBtn, primaryBtn } from "./DeviceMigrationParts";
import QrScanner from "./QrScanner";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const ACTIVE = new Set(["packing", "sending", "finishing"]);

export default function DeviceMigrationDirectSend({ files, filesBytes }: { files: string[]; filesBytes: number }) {
  const [mode, setMode] = React.useState<"" | "scan" | "code">("");
  const [code, setCode] = React.useState("");
  const [target, setTarget] = React.useState<DirectTarget | null>(null);
  const [withFiles, setWithFiles] = React.useState(false);
  const [st, setSt] = React.useState<DirectSendStatus | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    directSendStatus().then(setSt).catch(() => {});
  }, []);
  const active = !!st && ACTIVE.has(st.state);
  React.useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      directSendStatus().then(setSt).catch(() => {});
    }, 1000);
    return () => clearInterval(t);
  }, [active]);

  const resolve = async (p: Promise<DirectTarget>) => {
    setBusy(true);
    setError(null);
    try {
      setTarget(await p);
      setMode("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const go = async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      setSt(await directSend(target, withFiles ? files : []));
      setTarget(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    await directSendCancel().catch(() => {});
    setSt(null);
    setTarget(null);
    setMode("");
    setError(null);
  };

  if (st && st.state !== "cancelled") {
    if (st.state === "done") {
      return (
        <div style={okBox}>
          <div style={{ fontWeight: 600, color: "#16A34A" }}>
            ✓ 已经传到 {st.toDevice || "新电脑"}: {st.files} 个文件, {formatBytes(st.size)}
          </div>
          <div>到新电脑上点「开始导入」。这台电脑上的数据没有删除; 建议新电脑导入成功后, 在这台上退出登录。</div>
          <button type="button" style={{ ...plainBtn, marginTop: 6 }} onClick={() => void reset()}>
            好的
          </button>
        </div>
      );
    }
    if (st.state === "failed") {
      return (
        <div>
          <div style={errBox}>🚫 {st.error ?? "直传没成功"}</div>
          <button type="button" style={{ ...plainBtn, marginTop: 8 }} onClick={() => void reset()}>
            重新来
          </button>
        </div>
      );
    }
    const label =
      st.state === "packing"
        ? "正在打包、加密…"
        : st.state === "sending"
          ? `正在传给 ${st.toDevice || "新电脑"}… ${percent(st.sent, st.n)}%`
          : `传完了, 等新电脑收完… ${percent(st.acked, st.n)}%`;
    return (
      <div style={{ lineHeight: 1.7 }}>
        <div>{label}</div>
        {st.state !== "packing" && <ProgressBar value={percent(st.state === "finishing" ? st.acked : st.sent, st.n)} />}
        <div style={muted}>
          {st.size > 0 ? `${formatBytes(st.size)} · ` : ""}新电脑已收 {percent(st.acked, st.n)}% · 两台电脑都别合盖、别关 Companion
        </div>
        <button type="button" style={{ ...plainBtn, marginTop: 6 }} onClick={() => void reset()}>
          取消
        </button>
      </div>
    );
  }

  if (target) {
    return (
      <div style={{ lineHeight: 1.8 }}>
        <div>
          传到: <b>{target.toDevice || "新电脑"}</b>
        </div>
        <div>
          核对数字: <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 18, fontWeight: 600 }}>{target.safety}</span>
        </div>
        <div style={{ ...muted, color: "var(--catfish-orange)" }}>跟新电脑屏幕上的核对数字一样吗? 不一样就别传, 在新电脑上重新开始。</div>
        {filesBytes > 0 && (
          <label style={{ display: "block" }}>
            <input type="checkbox" checked={withFiles} disabled={busy} onChange={(e) => setWithFiles(e.target.checked)} /> 连同文件一起传:
            上传的附件、生成的文件等 ({formatBytes(filesBytes)})
          </label>
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
          <button type="button" style={primaryBtn(busy)} onClick={() => void go()} disabled={busy}>
            {busy ? "开始…" : "一样, 开始传"}
          </button>
          <button type="button" style={plainBtn} onClick={() => setTarget(null)} disabled={busy}>
            不一样 / 换一台
          </button>
        </div>
        {error && <div style={errBox}>🚫 {error}</div>}
      </div>
    );
  }

  return (
    <div style={{ lineHeight: 1.7 }}>
      <div style={muted}>
        先在新电脑上装好 Companion、登录同一个账号, 到「换机迁移 → 从旧电脑直传」出二维码。迁移包用新电脑现场生成的密钥加密,
        经公司服务器边传边删, 服务器看不到内容。不用 U 盘、不用设口令。
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <button type="button" style={primaryBtn(busy)} onClick={() => { setError(null); setMode("scan"); }} disabled={busy}>
          📷 扫新电脑上的二维码
        </button>
        <button type="button" style={plainBtn} onClick={() => { setError(null); setMode("code"); }} disabled={busy}>
          输配对码
        </button>
      </div>
      {mode === "scan" && (
        <div style={{ marginTop: 8 }}>
          <QrScanner
            onResult={(text) => void resolve(directFromQr(text))}
            onError={(msg) => {
              setError(msg);
              setMode("code");
            }}
          />
        </div>
      )}
      {mode === "code" && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <input
            placeholder="新电脑上的 8 位配对码"
            value={code}
            onChange={(e) => setCode(normalizeCode(e.target.value))}
            style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", letterSpacing: 2 }}
            disabled={busy}
            autoFocus
          />
          <button
            type="button"
            style={primaryBtn(busy || code.length !== 9)}
            onClick={() => void resolve(directLookup(code))}
            disabled={busy || code.length !== 9}
          >
            {busy ? "查找中…" : "找到新电脑"}
          </button>
        </div>
      )}
      {error && <div style={errBox}>🚫 {error}</div>}
    </div>
  );
}
