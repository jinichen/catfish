/** 设置 → 误删保护 (11/21, docs/DEVICE-MIGRATION-DESIGN.md §13)。
 *
 * 每天在本机自动做一份加密快照 (跟换机迁移包同一份清单: 日志、记忆、知识库、对话、技能、待办 …; 不含附件这类大文件),
 * 留最近几份。误删了 → 挑一份、勾要还原的几项 → 还原 (当前数据先挪进备份目录, 不会丢)。
 * 快照用只在这台电脑钥匙串里的密钥加密; 外发记录 / 审计类不还原 (只追加的链, 退回去会被当成被人改过)。
 */
import * as React from "react";

import { errBox, okBox, plainBtn, primaryBtn } from "../../components/DeviceMigrationParts";
import {
  entryKey,
  formatBytes,
  restorable,
  snapshotsMakeNow,
  snapshotsRestore,
  snapshotsSet,
  snapshotsStatus,
  type MigrationImportReport,
  type SnapshotInfo,
  type SnapshotsStatus,
} from "../../lib/deviceMigration";
import { Warning, XCircle } from "@phosphor-icons/react";
import { Ico } from "../../components/icons";

const muted: React.CSSProperties = { fontSize: 11, color: "var(--catfish-text-muted)" };
const h4: React.CSSProperties = { margin: "14px 0 6px", fontSize: 13 };
// 原生下拉默认是浅底黑字, 暗色主题下要跟着 token 走
const selectStyle: React.CSSProperties = {
  background: "var(--catfish-bg)",
  color: "var(--catfish-text)",
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  padding: "1px 4px",
};

export function when(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}

export default function SnapshotsCard() {
  const [st, setSt] = React.useState<SnapshotsStatus | null>(null);
  const [busy, setBusy] = React.useState<"" | "make" | "restore">("");
  const [error, setError] = React.useState<string | null>(null);
  const [made, setMade] = React.useState<SnapshotInfo | null>(null);
  const [pick, setPick] = React.useState<SnapshotInfo | null>(null);
  const [only, setOnly] = React.useState<Set<string>>(new Set());
  const [confirm, setConfirm] = React.useState(false);
  const [done, setDone] = React.useState<MigrationImportReport | null>(null);

  const refresh = React.useCallback(() => {
    snapshotsStatus().then(setSt).catch((e) => setError(String(e)));
  }, []);
  React.useEffect(refresh, [refresh]);

  const set = async (enabled: boolean, keep: number) => {
    setError(null);
    try {
      setSt(await snapshotsSet(enabled, keep));
    } catch (e) {
      setError(String(e));
    }
  };
  const makeNow = async () => {
    setBusy("make");
    setError(null);
    setMade(null);
    try {
      setMade(await snapshotsMakeNow());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
      refresh();
    }
  };
  const restore = async () => {
    if (!pick) return;
    setBusy("restore");
    setError(null);
    try {
      setDone(await snapshotsRestore(pick.name, [...only]));
      setPick(null);
      setConfirm(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  };

  if (done) {
    return (
      <div style={{ background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "var(--space-4)", fontSize: 12, lineHeight: 1.7 }}>
        <div style={okBox}>
          <div style={{ fontWeight: 600, color: "#16A34A" }}>✓ 还原好了: {done.files} 个文件</div>
          {done.backupDir && (
            <div>
              · 还原前的数据挪到了 <code style={{ fontSize: 11 }}>{done.backupDir}</code>
            </div>
          )}
          {done.warnings.map((w) => (
            <div key={w} style={{ color: "var(--catfish-orange)" }}><Ico as={Warning} />{w}</div>
          ))}
          <div style={muted}>知识库 / 待办这些页面刷新一下就能看到; 对话、记忆、技能已经让小鲶重新加载了。</div>
          <button type="button" style={{ ...plainBtn, marginTop: 6 }} onClick={() => { setDone(null); refresh(); }}>
            好的
          </button>
        </div>
      </div>
    );
  }
  if (!st) return <div style={{ background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "var(--space-4)", ...muted }}>{error ?? "读取中…"}</div>;

  const s = st.settings;
  const choices = pick ? pick.entries.filter(restorable) : [];
  return (
    <div style={{ background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "var(--space-4)", fontSize: 12, lineHeight: 1.7 }}>
      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>误删保护</h3>
      <div style={{ ...muted, marginTop: 4 }}>
        每天在这台电脑上自动存一份快照: 工作日志、记忆和画像、知识库、对话历史、技能、待办等 (不含附件这类大文件)。
        快照加密存放, 密钥只在这台电脑的钥匙串里, 不上传。误删了东西, 挑一份快照把那几项还原回来。
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
        <label>
          <input type="checkbox" checked={s.enabled} onChange={(e) => void set(e.target.checked, s.keep)} /> 每天自动做一份
        </label>
        <label>
          保留最近{" "}
          <select value={s.keep} onChange={(e) => void set(s.enabled, Number(e.target.value))} style={selectStyle}>
            {[1, 2, 3, 5, 7].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>{" "}
          份
        </label>
        <button type="button" style={primaryBtn(!!busy || st.busy)} onClick={() => void makeNow()} disabled={!!busy || st.busy}>
          {busy === "make" ? "正在做快照… (几分钟)" : "现在做一份"}
        </button>
      </div>
      {s.lastError && !made && <div style={{ ...muted, color: "var(--catfish-orange)" }}><Ico as={Warning} />上次没做成: {s.lastError}</div>}
      {made && (
        <div style={okBox}>
          ✓ 做好了: {when(made.createdAt)}, {made.files} 个文件, {formatBytes(made.bytes)}
        </div>
      )}

      <h4 style={h4}>已有的快照</h4>
      {st.snapshots.length === 0 && <div style={muted}>还没有。{s.enabled ? "开着自动的话, Companion 开着时每天会做一份。" : ""}</div>}
      {st.snapshots.map((x) => (
        <div key={x.name} style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ minWidth: 140 }}>{when(x.createdAt)}</span>
          <span style={muted}>
            {x.files} 个文件 · {formatBytes(x.bytes)}
          </span>
          <button
            type="button"
            style={plainBtn}
            disabled={!!busy}
            onClick={() => {
              setPick(x);
              setOnly(new Set());
              setConfirm(false);
              setDone(null);
            }}
          >
            从这份还原…
          </button>
        </div>
      ))}

      {pick && (
        <div style={{ marginTop: 10, padding: "8px 12px", border: "1px solid var(--catfish-border)", borderRadius: 4 }}>
          <div style={{ fontWeight: 600 }}>从 {when(pick.createdAt)} 的快照还原哪几项?</div>
          <div style={muted}>只还原勾上的; 没勾的保持现在的样子。</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: "0 12px", marginTop: 4 }}>
            {choices.map((e) => {
              const k = entryKey(e);
              return (
                <label key={k}>
                  <input
                    type="checkbox"
                    checked={only.has(k)}
                    onChange={(ev) => {
                      const n = new Set(only);
                      if (ev.target.checked) n.add(k);
                      else n.delete(k);
                      setOnly(n);
                      setConfirm(false);
                    }}
                  />{" "}
                  {e.label} <span style={muted}>({formatBytes(e.bytes)})</span>
                </label>
              );
            })}
          </div>
          {only.size > 0 && !confirm && (
            <button type="button" style={{ ...primaryBtn(false), marginTop: 8 }} onClick={() => setConfirm(true)}>
              还原这 {only.size} 项…
            </button>
          )}
          {confirm && (
            <div style={{ marginTop: 8, color: "var(--catfish-orange)" }}>
              勾上的这几项会回到 {when(pick.createdAt)} 的样子, 那之后新增的会不见 (现在的先挪进备份目录, 不会删)。确定?{" "}
              <button type="button" style={primaryBtn(busy === "restore")} onClick={() => void restore()} disabled={busy === "restore"}>
                {busy === "restore" ? "还原中…" : "确定还原"}
              </button>{" "}
              <button type="button" style={plainBtn} onClick={() => setConfirm(false)} disabled={busy === "restore"}>
                先不了
              </button>
            </div>
          )}
          <div style={{ marginTop: 6 }}>
            <button type="button" style={plainBtn} onClick={() => setPick(null)} disabled={busy === "restore"}>
              关掉
            </button>
          </div>
        </div>
      )}
      {error && <div style={errBox}><Ico as={XCircle} />{error}</div>}
      <div style={{ ...muted, marginTop: 10 }}>
        存在 <code>{st.dir}</code>。换电脑请用「换机迁移」—— 快照只在这台电脑上打得开。
      </div>
    </div>
  );
}
