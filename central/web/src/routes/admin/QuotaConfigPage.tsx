/** /admin/quota — 配额规则 编辑 UI (P3.5.93 6/23 鸿波).
 *
 * P3.5.93.1 (6/23): UI compact 化 (鸿波 catch "空间利用率太低").
 * - 全 fontSize 13 → 12; INPUT padding 紧凑
 * - 表格行高 ~40px → ~28px
 * - "+ 加" form inline 同行而不是单独行
 * - 大屏 2-col grid (per_model + per_dept 并排), 小屏自动 stack
 * - section 间距收紧
 *
 * 替换原 AdminQuota static placeholder. sysadmin only.
 * 治本 audit-5 dead UI 收口: 部门 quota 编辑收到这页改 quotas.yaml.
 */

import { useEffect, useState } from "react";

import { PageShell } from "../../components/PageShell";
import { RoleGate } from "../../components/RoleGate";
import {
  quotaConfigApi,
  type QuotaConfigResponse,
  type DefaultPerUser,
} from "../../lib/quota_config";
// 8/1 拆文件: 812 行已越军规红线, 见 quotaShared.tsx 文件头。
import { DeptOverridesSection, UserOverridesSection } from "./QuotaOverrides";
import {
  BTN,
  BTN_PRIMARY,
  fmtTokens,
  INPUT,
  MiniSection,
  NumericRow,
  TABLE_ADD,
  TABLE_HEAD,
  TPL_SIMPLE,
} from "./quotaShared";

export function QuotaConfigPage() {
  return (
    <RoleGate require={["sysadmin"]}>
      <QuotaConfigEditor />
    </RoleGate>
  );
}

function QuotaConfigEditor() {
  const [data, setData] = useState<QuotaConfigResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [savingMsg, setSavingMsg] = useState<string | null>(null);

  const refresh = async () => {
    setErr(null);
    try {
      setData(await quotaConfigApi.get());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  if (err) {
    return (
      <MiniSection title="错误">
        <div style={{ color: "#d9534f", fontSize: 12 }}>{err}</div>
        <button onClick={refresh} style={{ ...BTN, marginTop: 8 }}>重试</button>
      </MiniSection>
    );
  }
  if (!data) return <MiniSection title="加载中…"><span /></MiniSection>;

  const onError = (e: unknown) => {
    setErr(e instanceof Error ? e.message : String(e));
    setSavingMsg(null);
  };
  const onSuccess = (msg: string) => {
    setSavingMsg(msg);
    void refresh();
    setTimeout(() => setSavingMsg(null), 2500);
  };

  const defaultPerUser = data.config?.defaults?.per_user;
  const perModel = data.config?.defaults?.per_model ?? {};
  const perDept = data.config?.defaults?.per_department ?? {};
  const userOverrides = data.config?.overrides?.users ?? {};
  const deptOverrides = data.config?.overrides?.departments ?? {};

  return (
    <PageShell>
      {/* P3.5.93.3 (6/23): 顶部全中文紧凑提示, 砍英文术语 */}
      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          padding: "4px 8px",
          background: "var(--bg-elev)",
          border: "1px solid var(--border-soft)",
          borderRadius: 4,
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <strong style={{ color: "var(--text)" }}>🎯 配额规则</strong>
        <span>0 = 不限 · 网关自动生效 · 注释保留 · 特殊配额优先于默认</span>
        {savingMsg && (
          <span style={{ color: "#16a34a", marginLeft: "auto" }}>✓ {savingMsg}</span>
        )}
      </div>

      {/* defaults.per_user singleton 单行 — singleton 数据本身 1 行, 不需要并排 */}
      <DefaultPerUserSection
        current={defaultPerUser}
        onError={onError}
        onSuccess={onSuccess}
      />

      {/* 4 个 table-style section 2x2 grid: 大屏并排, 中屏自动 stack */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(440px, 1fr))",
          gap: 8,
        }}
      >
        <PerModelSection rows={perModel} onError={onError} onSuccess={onSuccess} />
        <PerDepartmentSection
          rows={perDept}
          onError={onError}
          onSuccess={onSuccess}
        />
        <UserOverridesSection
          rows={userOverrides}
          onError={onError}
          onSuccess={onSuccess}
        />
        <DeptOverridesSection
          rows={deptOverrides}
          onError={onError}
          onSuccess={onSuccess}
        />
      </div>
    </PageShell>
  );
}

// ── Section 1: defaults.per_user (singleton, inline 紧凑) ──

function DefaultPerUserSection({
  current,
  onError,
  onSuccess,
}: {
  current: DefaultPerUser | undefined;
  onError: (e: unknown) => void;
  onSuccess: (msg: string) => void;
}) {
  const [tpm, setTpm] = useState(String(current?.tokens_per_minute ?? 3_000_000));
  const [tpd, setTpd] = useState(String(current?.tokens_per_day ?? 300_000_000));
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setTpm(String(current?.tokens_per_minute ?? 3_000_000));
    setTpd(String(current?.tokens_per_day ?? 300_000_000));
  }, [current]);

  const save = async () => {
    setSaving(true);
    try {
      await quotaConfigApi.putDefaultPerUser({
        tokens_per_minute: parseInt(tpm, 10) || 0,
        tokens_per_day: parseInt(tpd, 10) || 0,
      });
      onSuccess("全员默认配额已保存");
    } catch (e) {
      onError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <MiniSection title="全员默认配额">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 130px 60px auto 130px 60px 60px",
          gap: 8,
          alignItems: "center",
          fontSize: 12,
        }}
      >
        <label style={{ color: "var(--text-muted)" }}>每分钟上限</label>
        <input
          type="number"
          value={tpm}
          onChange={(e) => setTpm(e.target.value)}
          min={0}
          style={INPUT}
        />
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          {fmtTokens(parseInt(tpm, 10) || 0)}
        </span>
        <label style={{ color: "var(--text-muted)" }}>每日上限</label>
        <input
          type="number"
          value={tpd}
          onChange={(e) => setTpd(e.target.value)}
          min={0}
          style={INPUT}
        />
        <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
          {fmtTokens(parseInt(tpd, 10) || 0)}
        </span>
        <button onClick={save} disabled={saving} style={BTN_PRIMARY}>
          {saving ? "…" : "保存"}
        </button>
      </div>
    </MiniSection>
  );
}

// ── Section 2: defaults.per_model ────────────────────

function PerModelSection({
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
      await quotaConfigApi.putPerModel(newName.trim(), parseInt(newTpd, 10) || 0);
      onSuccess(`模型 ${newName} 已存`);
      setNewName("");
      setNewTpd("0");
    } catch (e) {
      onError(e);
    }
  };

  return (
    <MiniSection title="单模型上限">
      <div style={{ ...TABLE_HEAD, gridTemplateColumns: TPL_SIMPLE }}>
        <div>模型</div>
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
          所有模型不限
        </div>
      )}
      {entries.map(([name, cfg]) => (
        <NumericRow
          key={name}
          tpl={TPL_SIMPLE}
          label={name}
          current={cfg.tokens_per_day}
          onSave={(v) => quotaConfigApi.putPerModel(name, v)}
          onDelete={() => quotaConfigApi.deletePerModel(name)}
          successMsg={(action) => `模型 ${name} 已${action}`}
          onError={onError}
          onSuccess={onSuccess}
        />
      ))}
      <div style={{ ...TABLE_ADD, gridTemplateColumns: TPL_SIMPLE }}>
        <input
          type="text"
          placeholder="新模型名"
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

// ── Section 3: defaults.per_department ───────────────

function PerDepartmentSection({
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
      await quotaConfigApi.putPerDepartment(newName.trim(), parseInt(newTpd, 10) || 0);
      onSuccess(`部门 ${newName} 默认配额已存`);
      setNewName("");
      setNewTpd("0");
    } catch (e) {
      onError(e);
    }
  };

  return (
    <MiniSection title="部门默认上限">
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
          所有部门走全员默认
        </div>
      )}
      {entries.map(([name, cfg]) => (
        <NumericRow
          key={name}
          tpl={TPL_SIMPLE}
          label={name}
          current={cfg.tokens_per_day}
          onSave={(v) => quotaConfigApi.putPerDepartment(name, v)}
          onDelete={() => quotaConfigApi.deletePerDepartment(name)}
          successMsg={(action) => `部门 ${name} 默认配额已${action}`}
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
