/** 已导入的微信聊天记录 (9/23) —— 隐私 → 微信接入。
 *
 * 取代原来的「导出聊天记录分析」卡片: 导入入口挪到了聊天框 (拖入 ZIP),
 * 首次授权挪到了导入确认框。这里只留事后要做的事:
 *
 *   看    —— 导进来了哪些群、多少条、什么时间段
 *   改    —— 群名、「我是谁」(认错了能改)
 *   删    —— 删掉一个群的全部导入 (连同本机原包)
 *   授权  —— 撤销; 换了模型后在这里重新确认 (不想等下次导入时再确认)
 */
import * as React from "react";
import type { CSSProperties } from "react";
import { invoke } from "@tauri-apps/api/core";

import { useChatStore } from "../../store/chat";

interface ArchiveStatus {
  supported: boolean;
  helperInstalled: boolean;
  helperPath: string;
  sourceType: string | null;
  authorized: boolean;
  currentPickerModel: string | null;
  consentedPickerModel: string | null;
  requiresReauthorization: boolean;
  message: string;
}

interface ImportedGroup {
  group_id: string;
  name: string;
  self_name: string | null;
  members: string[];
  export_count: number;
  message_count: number;
  start: string | null;
  end: string | null;
}

const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "?");

export default function WeChatImportsCard() {
  const pickerModel = useChatStore((state) => state.model);
  const [status, setStatus] = React.useState<ArchiveStatus | null>(null);
  const [groups, setGroups] = React.useState<ImportedGroup[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [renaming, setRenaming] = React.useState<string | null>(null);
  const [draftName, setDraftName] = React.useState("");
  const [pendingDelete, setPendingDelete] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    try {
      const [nextStatus, listing] = await Promise.all([
        invoke<ArchiveStatus>("wechat_archive_status"),
        invoke<{ items: ImportedGroup[] }>("wechat_export_groups"),
      ]);
      setStatus(nextStatus);
      setGroups(listing.items || []);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload, pickerModel]);

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      await reload();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const library = status?.sourceType === "export_library";

  return (
    <section style={cardStyle}>
      <div style={rowStyle}>
        <h3 style={{ margin: 0 }}>导入的微信聊天记录</h3>
        {library && status?.authorized && <span style={okStyle}>已授权</span>}
        {library && status?.requiresReauthorization && <span style={warnStyle}>模型已更换</span>}
      </div>

      <p style={hintStyle}>
        在 Mac 微信 (4.1.13 及以上) 里多选消息 → 合并转发 → 转发到其他应用, 把得到的 ZIP
        拖进聊天框即可。原包只存在本机 <code>~/.catfish/wechat-exports/</code>, 不上传。
        导入时发进对话的整理全文和包里的文档, 跟聊天框上传的文件一样放在
        <code>~/.catfish/uploads/</code>; 在这里删除群不会删掉它们。
      </p>

      {status?.supported && !status.helperInstalled && (
        <p style={warnBoxStyle}>
          聊天记录读取器还没装好 (<code style={{ overflowWrap: "anywhere" }}>{status.helperPath}</code>)。
          重启 Companion 会自动补装。
        </p>
      )}

      {groups.length === 0 ? (
        <p style={hintStyle}>还没有导入过。</p>
      ) : (
        <div style={listStyle}>
          {groups.map((g) => (
            <div key={g.group_id} style={itemStyle}>
              <div style={rowStyle}>
                {renaming === g.group_id ? (
                  <>
                    <input style={inputStyle} value={draftName} maxLength={200}
                      onChange={(e) => setDraftName(e.target.value)} />
                    <button type="button" style={miniBtnStyle} disabled={busy || !draftName.trim()}
                      onClick={() => void run(async () => {
                        await invoke("wechat_export_update_group", { groupId: g.group_id, name: draftName.trim() });
                        setRenaming(null);
                      })}>保存</button>
                    <button type="button" style={miniBtnStyle} onClick={() => setRenaming(null)}>取消</button>
                  </>
                ) : (
                  <>
                    <strong style={nameStyle} title={g.name}>{g.name}</strong>
                    <button type="button" style={miniBtnStyle} disabled={busy}
                      onClick={() => { setRenaming(g.group_id); setDraftName(g.name); }}>改名</button>
                    {pendingDelete === g.group_id ? (
                      <button type="button" style={{ ...miniBtnStyle, color: "var(--catfish-danger, #c0392b)" }}
                        disabled={busy}
                        onClick={() => void run(async () => {
                          await invoke("wechat_export_remove_group", { groupId: g.group_id });
                          setPendingDelete(null);
                        })}>确认删除</button>
                    ) : (
                      <button type="button" style={miniBtnStyle} disabled={busy}
                        onClick={() => setPendingDelete(g.group_id)}>删除</button>
                    )}
                  </>
                )}
              </div>
              <div style={metaStyle}>
                {g.message_count} 条 · {day(g.start)} ~ {day(g.end)} · 导入 {g.export_count} 次 · {g.members.length} 人
              </div>
              <label style={{ ...metaStyle, display: "flex", gap: 6, alignItems: "center" }}>
                我是
                <select style={selectStyle} disabled={busy} value={g.self_name ?? ""}
                  onChange={(e) => void run(() => invoke("wechat_export_update_group", {
                    groupId: g.group_id, selfName: e.target.value,
                  }))}>
                  <option value="">不在这个群里 / 未指定</option>
                  {g.members.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
            </div>
          ))}
        </div>
      )}

      {library && (status?.authorized || status?.requiresReauthorization) && (
        <div style={rowStyle}>
          {status?.requiresReauthorization && (
            <button type="button" style={primaryBtnStyle} disabled={busy || !status.currentPickerModel}
              onClick={() => void run(() => invoke("wechat_archive_enable", { acknowledged: true }))}
              title="导入的聊天内容会交给这个模型分析">
              同意交给当前模型 ({status.currentPickerModel || "未选择"}) 分析
            </button>
          )}
          <button type="button" style={miniBtnStyle} disabled={busy}
            onClick={() => void run(() => invoke("wechat_archive_disable"))}>
            撤销授权
          </button>
          <span style={metaStyle}>撤销后模型读不到这些记录; 导入的原包仍在, 可单独删除。</span>
        </div>
      )}

      {error && <p style={errStyle}>{error}</p>}
    </section>
  );
}

const cardStyle: CSSProperties = { border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", background: "var(--catfish-bg-elevated)", padding: "var(--space-4)", minWidth: 0, display: "flex", flexDirection: "column", gap: 12 };
const rowStyle: CSSProperties = { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" };
const hintStyle: CSSProperties = { margin: 0, fontSize: 13, lineHeight: 1.65, color: "var(--catfish-text-muted)" };
const warnBoxStyle: CSSProperties = { margin: 0, padding: 8, borderRadius: 4, background: "rgba(201, 139, 0, 0.06)", fontSize: 13, lineHeight: 1.6 };
const okStyle: CSSProperties = { color: "var(--status-ok, #2a8b3f)", fontSize: 12 };
const warnStyle: CSSProperties = { color: "var(--status-warn, #c98b00)", fontSize: 12 };
const listStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 8 };
const itemStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 4, padding: 10, border: "1px solid var(--catfish-border)", borderRadius: 8 };
const nameStyle: CSSProperties = { flex: 1, minWidth: 80, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const metaStyle: CSSProperties = { fontSize: 12, color: "var(--catfish-text-muted)" };
const inputStyle: CSSProperties = { flex: 1, minWidth: 0, padding: "4px 8px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", fontSize: 13 };
const selectStyle: CSSProperties = { padding: "2px 6px", border: "1px solid var(--catfish-border)", borderRadius: 4, background: "transparent", color: "var(--catfish-text)", fontSize: 12 };
const miniBtnStyle: CSSProperties = { padding: "3px 8px", border: "1px solid var(--catfish-border)", borderRadius: 4, background: "transparent", color: "var(--catfish-text-muted)", cursor: "pointer", fontSize: 12, flexShrink: 0 };
const primaryBtnStyle: CSSProperties = { ...miniBtnStyle, borderColor: "var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "white" };
const errStyle: CSSProperties = { margin: 0, fontSize: 12, color: "var(--catfish-danger, #c0392b)", whiteSpace: "pre-wrap" };
