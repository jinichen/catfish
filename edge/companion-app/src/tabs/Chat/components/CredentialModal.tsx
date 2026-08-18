/** 登录密码管理 —— 📚 → 登录密码。
 *
 * # 它现在的职责变了 (8/18)
 *
 * 以前这里是**第一次存密码**的地方：起个名字、填密码、复制引用串，再回教学
 * 流程里告诉模型用哪个 ref。中间那次人工搬运是所有麻烦的根 —— 搬错了对不上，
 * 搬对了又被 `_infer_params` 焊进冻结的 `script.py`。8/17 鸿波改了 EIS 密码，
 * UI 存进 `catfish-teaching:http://eis.ffcs.cn`，冻结的 skill 读的却是 4/28
 * 那条 `eis_password`，登录报"账号或密码错误"，两边谁也不知道谁。
 *
 * 第一次存密码已经挪进教学过程本身（`InlineCredentialPrompt`）：要密码的那
 * 一刻就地弹框，站点自动取当前页。这里只剩两件**事后**的事：
 *
 *   改密码   —— 不用重新教一遍。`resolve_secret` 每次都现查钥匙串（没有缓存），
 *               改完下一次跑就是新的。
 *   加入口   —— eis.ffcs.cn → neis.ffcs.cn 这种跳转，把第二个入口挂到同一条
 *               凭据上，共用一个密码。
 *
 * # 没有"名称"输入框了
 *
 * 新增时只填**网站**，label 就是 hostname。那个自由文本的"名称"字段正是 8/17
 * 那串套娃引用长出来的地方：员工把「复制引用」的结果粘回名称框 → 再存一次又
 * 包一层 → 每点一次多一层。字段没了，那个循环就不存在了。
 *
 * # 红线
 *
 * 密码只经 `saveTeachingCredential`（Tauri IPC → Rust → 系统凭据库）。
 * UI 从不回显密码，索引文件里也只有标签 / 引用 / 站点。
 */

import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import {
  addTeachingCredentialSite,
  deleteTeachingCredential,
  listTeachingCredentials,
  saveTeachingCredential,
  type TeachingCredential,
} from "../../../lib/tauri";

export default function CredentialModal({ onClose }: { onClose: () => void }) {
  const [saved, setSaved] = useState<TeachingCredential[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  /** 正在改哪条的密码 (label)。空 = 没在改。 */
  const [editing, setEditing] = useState("");
  const [password, setPassword] = useState("");
  /** 正在给哪条加入口 (label)。 */
  const [addingTo, setAddingTo] = useState("");
  const [newSite, setNewSite] = useState("");
  const [pendingDelete, setPendingDelete] = useState("");
  /** 新增一条 */
  const [newHost, setNewHost] = useState("");
  const [newPassword, setNewPassword] = useState("");

  const refresh = useCallback(async () => {
    try {
      setSaved(await listTeachingCredentials());
    } catch (e) {
      // 列不出来不影响存 —— 密码在系统凭据库里，索引只是方便看。
      console.warn("[teaching-credential] 列表读取失败:", e);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** 包一层：统一清错误 / 转圈 / 刷新 / 收起展开的那一格。 */
  const run = async (fn: () => Promise<void>) => {
    setError("");
    setBusy(true);
    try {
      await fn();
      await refresh();
      setEditing("");
      setAddingTo("");
      setPassword("");
      setNewSite("");
    } catch (e) {
      // 失败**不收起** —— 错误信息 (例如"neis.ffcs.cn 已经归「EIS」管了")
      // 要留在原地让人看见，而且输入框里的东西不该没了。
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={backdropStyle} role="dialog" aria-modal="true" aria-label="登录密码">
      <div style={modalStyle}>
        <div style={headerStyle}>
          <strong>登录密码</strong>
          <button type="button" onClick={onClose} style={btnStyle}>关闭</button>
        </div>
        <p style={hintStyle}>
          密码只保存在本机系统凭据库，不会写入聊天、配置文件或终端。
          <br />
          改完密码<strong>不用重新教一遍</strong> —— 教学时是按网站现查的。
        </p>

        {saved.length === 0 && (
          <p style={hintStyle}>
            还没存过。教学时遇到要输密码的地方会就地问你，存一次就好 ——
            不用先来这里准备。
          </p>
        )}

        <div style={listStyle}>
          {saved.map((c) => (
            <div key={c.label} style={cardStyle}>
              <div style={cardHeadStyle}>
                <span style={labelStyle} title={c.reference}>{c.label}</span>
                <button type="button" style={miniBtnStyle} disabled={busy}
                  onClick={() => { setEditing(editing === c.label ? "" : c.label); setAddingTo(""); }}>
                  改密码
                </button>
                <button type="button" style={miniBtnStyle} disabled={busy}
                  onClick={() => { setAddingTo(addingTo === c.label ? "" : c.label); setEditing(""); }}>
                  加入口
                </button>
                <button type="button" style={miniBtnStyle}
                  onClick={() => navigator.clipboard?.writeText(c.reference)}
                  title={c.reference}>
                  复制引用
                </button>
                {pendingDelete === c.label ? (
                  <button type="button" style={{ ...miniBtnStyle, color: "var(--catfish-danger, #c0392b)" }}
                    onClick={() => void run(async () => {
                      await deleteTeachingCredential(c.label);
                      setPendingDelete("");
                    })}>
                    确认删除
                  </button>
                ) : (
                  <button type="button" style={miniBtnStyle}
                    onClick={() => setPendingDelete(c.label)}>删除</button>
                )}
              </div>

              {/* 这条管哪些网站 —— 教学时按这个匹配当前页。 */}
              <div style={sitesRowStyle}>
                {c.sites?.length ? (
                  c.sites.map((s) => <span key={s} style={chipStyle}>{s}</span>)
                ) : (
                  <span style={{ ...hintStyle, fontSize: 11 }}>
                    还没配网站 —— 教学时匹配不到，点「加入口」补一个
                  </span>
                )}
              </div>

              {editing === c.label && (
                <div style={rowStyle}>
                  <input type="password" value={password} autoFocus autoComplete="off"
                    onChange={(e) => setPassword(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && password && !busy) {
                        // sites 不传 —— Rust 侧 None 表示"这次不提站点，原样留着"。
                        // 传 [] 会**清空**它配的所有入口。
                        void run(async () => { await saveTeachingCredential(c.label, password); });
                      }
                    }}
                    placeholder="新密码" style={inputStyle} />
                  <button type="button" disabled={busy || !password} style={primaryBtnStyle}
                    onClick={() => void run(async () => { await saveTeachingCredential(c.label, password); })}>
                    {busy ? "保存中…" : "覆盖"}
                  </button>
                </div>
              )}

              {addingTo === c.label && (
                <div style={rowStyle}>
                  <input value={newSite} autoFocus placeholder="例：neis.ffcs.cn"
                    onChange={(e) => setNewSite(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && newSite && !busy) {
                        void run(async () => { await addTeachingCredentialSite(c.label, newSite); });
                      }
                    }}
                    style={inputStyle} />
                  <button type="button" disabled={busy || !newSite} style={primaryBtnStyle}
                    onClick={() => void run(async () => { await addTeachingCredentialSite(c.label, newSite); })}>
                    加上
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>

        {/* 新增。只问网站 + 密码 —— 没有"名称"这个自由字段, 见文件头。 */}
        <details style={{ fontSize: 12 }}>
          <summary style={{ cursor: "pointer", color: "var(--catfish-text-muted)" }}>
            手工新增一条（一般不用 —— 教学时会自动问）
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
            <input value={newHost} placeholder="网站，例：eis.ffcs.cn"
              onChange={(e) => setNewHost(e.target.value)} style={inputStyle} />
            <input type="password" value={newPassword} placeholder="密码" autoComplete="off"
              onChange={(e) => setNewPassword(e.target.value)} style={inputStyle} />
            <button type="button" disabled={busy || !newHost.trim() || !newPassword}
              style={primaryBtnStyle}
              onClick={() => void run(async () => {
                const host = newHost.trim();
                // label 用 hostname 本身，跟教学时就地存的那条保持一致。
                await saveTeachingCredential(host, newPassword, [host]);
                setNewHost("");
                setNewPassword("");
              })}>
              保存到系统凭据库
            </button>
          </div>
        </details>

        {error && <div style={errStyle}>{error}</div>}
      </div>
    </div>
  );
}

const backdropStyle: CSSProperties = { position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", background: "rgba(0,0,0,.28)" };
const modalStyle: CSSProperties = { width: "min(480px, calc(100vw - 32px))", maxHeight: "min(640px, calc(100vh - 64px))", overflowY: "auto", display: "flex", flexDirection: "column", gap: 12, padding: 20, borderRadius: 12, background: "var(--catfish-bg)", boxShadow: "0 12px 40px rgba(0,0,0,.25)" };
const headerStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "center" };
const hintStyle: CSSProperties = { margin: 0, color: "var(--catfish-text-muted)", fontSize: 12, lineHeight: 1.5 };
const listStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 8 };
const cardStyle: CSSProperties = { display: "flex", flexDirection: "column", gap: 6, padding: 9, border: "1px solid var(--catfish-border)", borderRadius: 8 };
const cardHeadStyle: CSSProperties = { display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" };
const labelStyle: CSSProperties = { flex: 1, minWidth: 90, fontSize: 13, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const sitesRowStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" };
const rowStyle: CSSProperties = { display: "flex", gap: 6, alignItems: "center" };
const chipStyle: CSSProperties = { padding: "2px 8px", border: "1px solid var(--catfish-border)", borderRadius: 999, fontSize: 11, color: "var(--catfish-text-muted)" };
const inputStyle: CSSProperties = { flex: 1, minWidth: 0, padding: "6px 9px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", fontSize: 13 };
const btnStyle: CSSProperties = { padding: "6px 10px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", cursor: "pointer" };
const miniBtnStyle: CSSProperties = { padding: "3px 7px", border: "1px solid var(--catfish-border)", borderRadius: 4, background: "transparent", color: "var(--catfish-text-muted)", cursor: "pointer", fontSize: 11, flexShrink: 0 };
const primaryBtnStyle: CSSProperties = { ...btnStyle, padding: "6px 12px", fontSize: 12, borderColor: "var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "white", flexShrink: 0 };
const errStyle: CSSProperties = { fontSize: 12, color: "var(--catfish-danger, #c0392b)", whiteSpace: "pre-wrap" };
