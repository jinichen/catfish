/** 换机迁移 · 导入 (新电脑)。设置页「换机迁移」和首次启动引导共用。
 *
 * 两种: 「从旧电脑直传」(11/21, 扫码, DeviceMigrationDirectReceive) / 「用迁移包文件」(选包 → 输口令)。
 * 之后一样: 先看包里有什么 (同一个人? 版本够新? 这台已有数据?) → 确认导入 →
 * 列出「还要重新做的事」(凭证刻意不带) → 重启 Companion。
 */
import * as React from "react";
import { Warning } from "@phosphor-icons/react";

import { migrationImport, migrationInspect, migrationPickOpen, type MigrationImportReport, type MigrationInspectReport } from "../lib/deviceMigration";
import DeviceMigrationDirectReceive from "./DeviceMigrationDirectReceive";
import { errBox, ImportDone, inputStyle, PackageCheck, plainBtn, primaryBtn } from "./DeviceMigrationParts";
import { Ico } from "./icons";

export { inputStyle, plainBtn, primaryBtn } from "./DeviceMigrationParts";

const tabBtn = (on: boolean): React.CSSProperties => ({
  ...plainBtn,
  background: on ? "var(--catfish-cyan-tint)" : "transparent",
  color: on ? "var(--catfish-cyan)" : "var(--catfish-text)",
  borderColor: on ? "var(--catfish-cyan)" : "var(--catfish-border)",
});

export default function DeviceMigrationImport({ beforeRestart }: { beforeRestart?: () => void }) {
  const [mode, setMode] = React.useState<"direct" | "file">("direct");
  return (
    <div style={{ fontSize: 12 }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        <button type="button" style={tabBtn(mode === "direct")} onClick={() => setMode("direct")}>
          从旧电脑直传 (推荐)
        </button>
        <button type="button" style={tabBtn(mode === "file")} onClick={() => setMode("file")}>
          用迁移包文件
        </button>
      </div>
      {mode === "direct" ? <DeviceMigrationDirectReceive beforeRestart={beforeRestart} /> : <FileImport beforeRestart={beforeRestart} />}
    </div>
  );
}

/** 用迁移包文件: 选包 → 输口令 → 查看 → 导入 */
function FileImport({ beforeRestart }: { beforeRestart?: () => void }) {
  const [path, setPath] = React.useState<string | null>(null);
  const [pass, setPass] = React.useState("");
  const [info, setInfo] = React.useState<MigrationInspectReport | null>(null);
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

  const run = async (overwrite: boolean) => {
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

  if (done) return <ImportDone done={done} beforeRestart={beforeRestart} />;

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
      {info && <PackageCheck info={info} busy={busy === "import"} onImport={(o) => void run(o)} />}
      {error && <div style={errBox}><Ico as={Warning} />{error}</div>}
    </div>
  );
}
