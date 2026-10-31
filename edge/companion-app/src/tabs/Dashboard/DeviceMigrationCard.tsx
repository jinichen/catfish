/** 设置 → 隐私 / 本机数据 → 换机迁移 (BL-L9, docs/DEVICE-MIGRATION-DESIGN.md)。
 *
 * 上半: 旧电脑「导出」—— 列出带什么 / 不带什么, 可选项勾选, 设口令, 存成 .catfishmove 文件。
 * 下半: 新电脑「导入」(DeviceMigrationImport, 跟首次启动引导共用)。
 * 迁移包不经过中央; 账号和授权刻意不带, 新电脑重新登录。
 */
import * as React from "react";

import DeviceMigrationImport, { inputStyle, plainBtn, primaryBtn } from "../../components/DeviceMigrationImport";
import {
  entryKey,
  formatBytes,
  labelsOf,
  migrationExport,
  migrationPickSave,
  migrationScan,
  passphraseHint,
  type MigrationExportReport,
  type MigrationScan,
} from "../../lib/deviceMigration";
import { revealInFinder } from "../../lib/tauri";
import { fileManagerName } from "../../lib/platformLabels";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const h4: React.CSSProperties = { margin: "14px 0 6px", fontSize: 13 };

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(d.getDate()).padStart(2, "0")}`;
}

export default function DeviceMigrationCard() {
  const [scan, setScan] = React.useState<MigrationScan | null>(null);
  const [chosen, setChosen] = React.useState<Set<string>>(new Set());
  const [pass, setPass] = React.useState("");
  const [pass2, setPass2] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<MigrationExportReport | null>(null);

  React.useEffect(() => {
    migrationScan().then(setScan).catch((e) => setError(String(e)));
  }, []);

  const by = (c: string) => scan?.entries.filter((e) => e.class === c) ?? [];
  const required = [...by("required"), ...by("merge")];
  const optional = by("optional").filter((e) => e.bytes > 0);
  const secret = by("secret");
  const hint = passphraseHint(pass);
  const mismatch = pass2.length > 0 && pass !== pass2;
  const chosenBytes = optional.filter((e) => chosen.has(entryKey(e))).reduce((n, e) => n + e.bytes, 0);

  const run = async () => {
    setError(null);
    setResult(null);
    const dest = await migrationPickSave(`鲶鱼迁移包-${today()}.catfishmove`).catch(() => null);
    if (!dest) return;
    setBusy(true);
    try {
      setResult(await migrationExport(dest, pass, Array.from(chosen)));
      setPass("");
      setPass2("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 12, fontSize: 12 }}>
      <h3 style={{ margin: 0, fontSize: 15 }}>🧳 换机迁移</h3>
      <div style={{ ...muted, marginTop: 4, lineHeight: 1.6 }}>
        换电脑 / 重装系统时, 把小鲶对你的积累整个搬过去。迁移包用你设的口令加密, 不经过公司服务器, 你自己用 U 盘、隔空投送或网盘带到新电脑。
      </div>

      <h4 style={h4}>① 在旧电脑上导出</h4>
      {!scan && !error && <div style={muted}>正在清点本机数据…</div>}
      {scan && (
        <div style={{ lineHeight: 1.7 }}>
          <div>
            <b>一定带上</b> ({formatBytes(required.reduce((n, e) => n + e.bytes, 0))}): {labelsOf(required)}
          </div>
          {optional.length > 0 && (
            <div>
              <b>可选</b> (可能很大, 勾了才带):
              {optional.map((e) => (
                <label key={entryKey(e)} style={{ marginLeft: 10, whiteSpace: "nowrap" }}>
                  <input
                    type="checkbox"
                    checked={chosen.has(entryKey(e))}
                    disabled={busy}
                    onChange={(ev) => {
                      const next = new Set(chosen);
                      if (ev.target.checked) next.add(entryKey(e));
                      else next.delete(entryKey(e));
                      setChosen(next);
                    }}
                  />{" "}
                  {e.label} ({formatBytes(e.bytes)})
                </label>
              ))}
            </div>
          )}
          <div style={muted}>
            不带: {labelsOf(secret)} —— 账号和授权在新电脑上重新登录, 包丢了也不会泄露; 搜索索引、本地模型等新电脑自己重建。
          </div>
          {scan.unclassified.length > 0 && (
            <div style={{ ...muted, color: "var(--catfish-orange)" }}>
              ⚠️ 有 {scan.unclassified.length} 项还没登记归类, 这次不会带: {scan.unclassified.join("、")}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
            <input type="password" placeholder="设一个迁移口令 (至少 8 位)" value={pass} onChange={(e) => setPass(e.target.value)} style={inputStyle} disabled={busy} />
            <input type="password" placeholder="再输一遍" value={pass2} onChange={(e) => setPass2(e.target.value)} style={inputStyle} disabled={busy} />
          </div>
          {(hint || mismatch) && (
            <div style={{ ...muted, color: "var(--catfish-orange)" }}>{mismatch ? "两次输入不一样" : hint}</div>
          )}
          <div style={muted}>口令丢了包就打不开, 公司和我们都帮不了。请记好, 到新电脑上导入时要用。</div>
          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              style={primaryBtn(busy || pass.length < 8 || pass !== pass2)}
              onClick={() => void run()}
              disabled={busy || pass.length < 8 || pass !== pass2}
            >
              {busy ? `导出中… ${chosenBytes > 0 ? "(附件较多, 要等一会儿)" : ""}` : "选择保存位置并导出"}
            </button>
          </div>
        </div>
      )}
      {result && (
        <div
          style={{
            marginTop: 10,
            padding: "8px 12px",
            background: "rgba(22,163,74,0.05)",
            border: "1px solid rgba(22,163,74,0.3)",
            borderRadius: 4,
            lineHeight: 1.7,
          }}
        >
          <div style={{ fontWeight: 600, color: "#16A34A" }}>
            ✓ 已导出 {result.files} 个文件 ({formatBytes(result.bytes)})
          </div>
          <code style={{ fontSize: 11 }}>{result.path}</code>{" "}
          <button type="button" style={plainBtn} onClick={() => void revealInFinder(result.path).catch(() => {})}>
            在{fileManagerName()}中显示
          </button>
          <div style={muted}>这台电脑上的数据没有删除。建议在新电脑导入成功后, 在这台电脑上退出登录。</div>
        </div>
      )}
      {error && !result && (
        <div
          style={{
            marginTop: 10,
            padding: "6px 10px",
            background: "rgba(220,38,38,0.05)",
            border: "1px solid rgba(220,38,38,0.3)",
            borderRadius: 4,
            color: "var(--status-err)",
          }}
        >
          🚫 {error}
        </div>
      )}

      <h4 style={h4}>② 在新电脑上导入</h4>
      <div style={{ ...muted, marginBottom: 6 }}>新电脑装好 Companion、登录同一个账号后, 在这里选迁移包导入。</div>
      <DeviceMigrationImport />
    </div>
  );
}
