/** 换机迁移 · 导入 (新电脑)。设置页「换机迁移」和首次启动引导共用。
 *
 * 选包 → 输口令 → 先看包里有什么 (同一个人? 版本够新? 这台已有数据?) → 确认导入 →
 * 列出「还要重新做的事」(凭证刻意不带) → 重启 Companion。
 */
import * as React from "react";

import {
  formatBytes,
  migrationImport,
  migrationInspect,
  migrationPickOpen,
  migrationRestartApp,
  type MigrationImportReport,
  type MigrationInspectReport,
} from "../lib/deviceMigration";

const box: React.CSSProperties = {
  marginTop: 10,
  padding: "8px 12px",
  borderRadius: 4,
  fontSize: 12,
  lineHeight: 1.7,
};
const okBox: React.CSSProperties = { ...box, background: "rgba(22,163,74,0.05)", border: "1px solid rgba(22,163,74,0.3)" };
const errBox: React.CSSProperties = {
  ...box,
  background: "rgba(220,38,38,0.05)",
  border: "1px solid rgba(220,38,38,0.3)",
  color: "var(--status-err)",
};
export const primaryBtn = (busy: boolean): React.CSSProperties => ({
  background: "var(--catfish-cyan)",
  color: "white",
  border: "none",
  borderRadius: 4,
  padding: "6px 14px",
  fontSize: 13,
  cursor: busy ? "default" : "pointer",
  opacity: busy ? 0.6 : 1,
});
export const plainBtn: React.CSSProperties = {
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  padding: "5px 12px",
  fontSize: 12,
  cursor: "pointer",
  color: "var(--catfish-text)",
};
export const inputStyle: React.CSSProperties = { padding: "4px 8px", fontSize: 13, width: 240 };

const OS: Record<string, string> = { macos: "Mac", windows: "Windows", linux: "Linux" };

export default function DeviceMigrationImport({ beforeRestart }: { beforeRestart?: () => void }) {
  const [path, setPath] = React.useState<string | null>(null);
  const [pass, setPass] = React.useState("");
  const [info, setInfo] = React.useState<MigrationInspectReport | null>(null);
  const [overwrite, setOverwrite] = React.useState(false);
  const [busy, setBusy] = React.useState<"" | "inspect" | "import">("");
  const [error, setError] = React.useState<string | null>(null);
  const [done, setDone] = React.useState<MigrationImportReport | null>(null);

  const pick = async () => {
    const p = await migrationPickOpen().catch(() => null);
    if (p) {
      setPath(p);
      setInfo(null);
      setError(null);
    }
  };

  const inspect = async () => {
    if (!path) return;
    setBusy("inspect");
    setError(null);
    try {
      setInfo(await migrationInspect(path, pass));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  };

  const run = async () => {
    if (!path) return;
    setBusy("import");
    setError(null);
    try {
      setDone(await migrationImport(path, pass, overwrite));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  };

  if (done) {
    return (
      <div style={okBox}>
        <div style={{ fontWeight: 600, color: "#16A34A" }}>
          ✓ 导入完成: {done.files} 个文件, {formatBytes(done.bytes)}
        </div>
        {done.pathsRewritten > 0 && <div>· 文件里记着的旧电脑路径改成了这台的 ({done.pathsRewritten} 处)</div>}
        {done.keptExisting > 0 && <div>· 有 {done.keptExisting} 个技能这台已经自带, 保留了这台的版本</div>}
        {done.backupDir && (
          <div>
            · 这台原来的数据挪到了 <code style={{ fontSize: 11 }}>{done.backupDir}</code>
          </div>
        )}
        {done.warnings.map((w) => (
          <div key={w} style={{ color: "var(--catfish-orange)" }}>⚠️ {w}</div>
        ))}
        <div style={{ marginTop: 6, fontWeight: 600 }}>还需要你重新做的 (账号和授权刻意没带过来):</div>
        {done.redo.map((r) => (
          <div key={r}>· {r}</div>
        ))}
        <div style={{ marginTop: 8 }}>
          <button
            type="button"
            style={primaryBtn(false)}
            onClick={() => {
              beforeRestart?.();
              void migrationRestartApp();
            }}
          >
            重启 Companion, 开始用
          </button>
        </div>
      </div>
    );
  }

  const blocked = info && (!info.samePerson || !info.versionOk);
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" style={plainBtn} onClick={() => void pick()} disabled={!!busy}>
          选择迁移包…
        </button>
        <span style={{ color: "var(--catfish-text-muted)", wordBreak: "break-all" }}>{path ?? "还没选"}</span>
      </div>
      {path && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
          <input
            type="password"
            placeholder="导出时设的迁移口令"
            value={pass}
            onChange={(e) => {
              setPass(e.target.value);
              setInfo(null);
            }}
            style={inputStyle}
            disabled={!!busy}
          />
          <button type="button" style={primaryBtn(!!busy || !pass)} onClick={() => void inspect()} disabled={!!busy || !pass}>
            {busy === "inspect" ? "解密校验中…" : "查看包里有什么"}
          </button>
        </div>
      )}
      {info && (
        <div style={{ ...box, border: "1px solid var(--catfish-border)", background: "var(--catfish-bg)" }}>
          <div>
            {info.userEmail} · {OS[info.sourceOs] ?? info.sourceOs} 上的 Companion {info.companionVersion} ·{" "}
            {info.createdAt.slice(0, 16).replace("T", " ")} 导出
          </div>
          <div>
            {info.files} 个文件, {formatBytes(info.bytes)}{info.withFiles ? " (连同文件)" : ""}
          </div>
          {!info.samePerson && (
            <div style={{ color: "var(--status-err)" }}>
              🚫 这是 {info.userEmail} 的迁移包, 现在登录的是 {info.loggedInAs}。迁移包只能导给本人。
            </div>
          )}
          {!info.versionOk && (
            <div style={{ color: "var(--status-err)" }}>🚫 迁移包来自更新版本的 Companion, 请先把这台升级到最新。</div>
          )}
          {info.targetHasData && !blocked && (
            <label style={{ display: "block", marginTop: 4, color: "var(--catfish-orange)" }}>
              <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> 这台已经有鲶鱼数据:
              先备份当前数据再覆盖 (备份留在 ~/.catfish 里, 不会删)
            </label>
          )}
          {!blocked && (
            <div style={{ marginTop: 8 }}>
              <button
                type="button"
                style={primaryBtn(!!busy || (info.targetHasData && !overwrite))}
                onClick={() => void run()}
                disabled={!!busy || (info.targetHasData && !overwrite)}
              >
                {busy === "import" ? "导入中… (别关 Companion)" : "开始导入"}
              </button>
            </div>
          )}
        </div>
      )}
      {error && <div style={errBox}>🚫 {error}</div>}
    </div>
  );
}
