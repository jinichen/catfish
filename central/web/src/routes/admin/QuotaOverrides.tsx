/** /admin/quota 的 overrides 两段 —— 员工特殊配额 + 部门特殊配额 (8/1 拆出).
 *
 * 拆分理由见 quotaShared.tsx 文件头 (原文件 812 行, 已越军规红线)。
 *
 * 这两段跟 defaults 那三段的区别: defaults 是"没有特殊规定时用哪个数",
 * overrides 是"这个人 / 这个部门单独定"。删一条 override 的后果是**回落到
 * defaults**, 不是变成不限 —— 确认对话框里必须说清楚这一点。
 */

import { useState } from "react";

import { ConfirmDialog } from "../../components/Dialog";
import { quotaConfigApi } from "../../lib/quota_config";
import {
  BTN,
  BTN_DANGER,
  BTN_PRIMARY,
  fmtTokens,
  INPUT,
  MiniSection,
  NumericRow,
  TABLE_ADD,
  TABLE_HEAD,
  TABLE_ROW,
  TPL_SIMPLE,
  TPL_USER,
} from "./quotaShared";

// ── Section 4a: overrides.users ──────────────────────

export function UserOverridesSection({
  rows,
  onError,
  onSuccess,
}: {
  rows: Record<string, { tokens_per_minute: number; tokens_per_day: number }>;
  onError: (e: unknown) => void;
  onSuccess: (msg: string) => void;
}) {
  const [newEmail, setNewEmail] = useState("");
  const [newTpm, setNewTpm] = useState("1000000");
  const [newTpd, setNewTpd] = useState("100000000");
  const entries = Object.entries(rows);

  const add = async () => {
    if (!newEmail.includes("@")) return;
    try {
      await quotaConfigApi.putUserOverride(newEmail.trim(), {
        tokens_per_minute: parseInt(newTpm, 10) || 0,
        tokens_per_day: parseInt(newTpd, 10) || 0,
      });
      onSuccess(`员工 ${newEmail} 特殊配额已存`);
      setNewEmail("");
    } catch (e) {
      onError(e);
    }
  };

  return (
    <MiniSection title="用户特殊配额">
      <div style={{ ...TABLE_HEAD, gridTemplateColumns: TPL_USER }}>
        <div>邮箱</div>
        <div>每分钟上限</div>
        <div>每日上限</div>
        <div></div>
        <div></div>
        <div></div>
      </div>
      {entries.length === 0 && (
        <div
          style={{
            color: "var(--text-muted)",
            padding: "8px 0",
            fontSize: 11,
            textAlign: "center",
          }}
        >
          没有特殊员工
        </div>
      )}
      {entries.map(([email, cfg]) => (
        <UserOverrideRow
          key={email}
          email={email}
          currentTpm={cfg.tokens_per_minute}
          currentTpd={cfg.tokens_per_day}
          onError={onError}
          onSuccess={onSuccess}
        />
      ))}
      <div style={{ ...TABLE_ADD, gridTemplateColumns: TPL_USER }}>
        <input
          type="text"
          placeholder="员工邮箱"
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
          style={INPUT}
        />
        <input
          type="number"
          value={newTpm}
          onChange={(e) => setNewTpm(e.target.value)}
          min={0}
          style={INPUT}
        />
        <input
          type="number"
          value={newTpd}
          onChange={(e) => setNewTpd(e.target.value)}
          min={0}
          style={INPUT}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {fmtTokens(parseInt(newTpd, 10) || 0)}
        </span>
        <button onClick={add} disabled={!newEmail.includes("@")} style={BTN_PRIMARY}>
          + 加
        </button>
        <span />
      </div>
    </MiniSection>
  );
}

function UserOverrideRow({
  email,
  currentTpm,
  currentTpd,
  onError,
  onSuccess,
}: {
  email: string;
  currentTpm: number;
  currentTpd: number;
  onError: (e: unknown) => void;
  onSuccess: (msg: string) => void;
}) {
  const [tpm, setTpm] = useState(String(currentTpm));
  const [tpd, setTpd] = useState(String(currentTpd));
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [delErr, setDelErr] = useState<string | null>(null);
  const changed = parseInt(tpm, 10) !== currentTpm || parseInt(tpd, 10) !== currentTpd;

  const save = async () => {
    setBusy(true);
    try {
      await quotaConfigApi.putUserOverride(email, {
        tokens_per_minute: parseInt(tpm, 10) || 0,
        tokens_per_day: parseInt(tpd, 10) || 0,
      });
      onSuccess(`员工 ${email} 特殊配额已存`);
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  };
  const del = async () => {
    setBusy(true);
    setDelErr(null);
    try {
      await quotaConfigApi.deleteUserOverride(email);
      setConfirming(false);
      onSuccess(`员工 ${email} 特殊配额已删`);
    } catch (e) {
      // ⚠ 不走 onError。onError 会把错误抛到 QuotaConfigEditor 的 err 上,
      // 而那个是**早返回**的 (`if (err) return <MiniSection title="错误">`)
      // —— 整个编辑器会被一张错误卡换掉, 其他行没保存的输入一起丢。
      // 一次删除失败不该有这么大的爆炸半径, 错误留在对话框里。
      setDelErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...TABLE_ROW, gridTemplateColumns: TPL_USER }}>
      <div style={{ fontFamily: "monospace", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {email}
      </div>
      <input
        type="number"
        value={tpm}
        onChange={(e) => setTpm(e.target.value)}
        min={0}
        style={INPUT}
      />
      <input
        type="number"
        value={tpd}
        onChange={(e) => setTpd(e.target.value)}
        min={0}
        style={INPUT}
      />
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{fmtTokens(parseInt(tpd, 10) || 0)}</span>
      <button onClick={save} disabled={!changed || busy} style={changed ? BTN_PRIMARY : BTN}>
        {busy ? "…" : "存"}
      </button>
      <button onClick={() => setConfirming(true)} disabled={busy} style={BTN_DANGER}>
        删
      </button>
      {confirming && (
        <ConfirmDialog
          danger
          title={`删掉 ${email} 的特殊配额?`}
          confirmLabel="删除"
          busy={busy}
          onCancel={() => {
            setConfirming(false);
            setDelErr(null);
          }}
          onConfirm={() => void del()}
        >
          这个人之后<b>按默认配额算</b>，不是变成不限 —— 如果默认值比他现在
          的特殊配额低，他的额度是<b>降</b>了。
          {delErr && (
            <div style={{ marginTop: 8, color: "var(--status-err)" }}>
              删除失败：{delErr}
            </div>
          )}
        </ConfirmDialog>
      )}
    </div>
  );
}

// ── Section 4b: overrides.departments ────────────────

export function DeptOverridesSection({
  rows,
  onError,
  onSuccess,
}: {
  rows: Record<string, { tokens_per_day: number }>;
  onError: (e: unknown) => void;
  onSuccess: (msg: string) => void;
}) {
  const [newName, setNewName] = useState("");
  const [newTpd, setNewTpd] = useState("0");
  const entries = Object.entries(rows);

  const add = async () => {
    if (!newName.trim()) return;
    try {
      await quotaConfigApi.putDeptOverride(newName.trim(), parseInt(newTpd, 10) || 0);
      onSuccess(`部门 ${newName} 特殊配额已存`);
      setNewName("");
      setNewTpd("0");
    } catch (e) {
      onError(e);
    }
  };

  return (
    <MiniSection title="部门特殊配额">
      <div style={{ ...TABLE_HEAD, gridTemplateColumns: TPL_SIMPLE }}>
        <div>部门</div>
        <div>每日上限</div>
        <div></div>
        <div></div>
        <div></div>
      </div>
      {entries.length === 0 && (
        <div
          style={{
            color: "var(--text-muted)",
            padding: "8px 0",
            fontSize: 11,
            textAlign: "center",
          }}
        >
          没有特殊部门
        </div>
      )}
      {entries.map(([name, cfg]) => (
        <NumericRow
          key={name}
          tpl={TPL_SIMPLE}
          label={name}
          current={cfg.tokens_per_day}
          onSave={(v) => quotaConfigApi.putDeptOverride(name, v)}
          onDelete={() => quotaConfigApi.deleteDeptOverride(name)}
          successMsg={(action) => `部门 ${name} 特殊配额已${action}`}
          onError={onError}
          onSuccess={onSuccess}
        />
      ))}
      <div style={{ ...TABLE_ADD, gridTemplateColumns: TPL_SIMPLE }}>
        <input
          type="text"
          placeholder="部门名"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          style={INPUT}
        />
        <input
          type="number"
          value={newTpd}
          onChange={(e) => setNewTpd(e.target.value)}
          min={0}
          style={INPUT}
        />
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {fmtTokens(parseInt(newTpd, 10) || 0)}
        </span>
        <button onClick={add} disabled={!newName.trim()} style={BTN_PRIMARY}>
          + 加
        </button>
        <span />
      </div>
    </MiniSection>
  );
}
