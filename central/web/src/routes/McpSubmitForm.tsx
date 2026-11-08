/** 员工提交 MCP 连接器 (11/08 分享治理 §3.4)。
 *
 * 原来只有 admin 能登记连接器, 员工不能提交。现在: 贴 manifest yaml + 范围 + 给哪些部门 + 说明 +
 * 测试账号怎么拿 → 进待审核; 部门范围由本部门主管审, 全公司由管理员审; 通过后进 registry, 同事可订阅。
 * 没写「测试账号怎么拿」的, 审批人只能看清单不能试用 —— 不允许审批人拿自己的生产凭据试别人交的连接器。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Card } from "../components/Card";
import { SCOPE_LABEL, type ShareScope } from "../lib/hub";
import { submitConnector } from "../lib/mcp";
import { useAuthStore } from "../store/auth";

const MANIFEST_EXAMPLE = `id: crm-readonly
name: CRM 只读
version: "0.1.0"
description: 查客户 / 查订单 (只读)
provider: internal
status: active
auth_type: api_key
mcp_command:
  type: uvx
  package: mcp-server-crm
  args: []
  env:
    CRM_URL: "\${CRM_URL}"
    CRM_API_KEY: "\${SECRET_REF:crm-api-key}"
tools:
  - name: crm_find_customer
    description: 按名字 / 手机号查客户
  - name: crm_list_orders
    description: 列某客户的订单`;

const input = {
  width: "100%",
  padding: "6px 10px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  fontSize: 13,
  fontFamily: "inherit",
  boxSizing: "border-box",
} as const;
const label = { marginBottom: 4, fontSize: 13 } as const;
const hint = { fontSize: 11, color: "var(--text-muted)", marginTop: 4, lineHeight: 1.6 } as const;

export function McpSubmitForm() {
  const navigate = useNavigate();
  const me = useAuthStore((s) => s.me);
  const [yaml, setYaml] = useState("");
  const [scope, setScope] = useState<ShareScope>("department");
  const [departments, setDepartments] = useState(me?.department || "");
  const [note, setNote] = useState("");
  const [trialCreds, setTrialCreds] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const deptList = departments.split(/[,，、\s]+/).map((d) => d.trim()).filter(Boolean);
  const canSubmit = yaml.trim().length > 0 && (scope === "company" || deptList.length > 0);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setResult(null);
    try {
      const r = await submitConnector({
        manifest_yaml: yaml,
        scope,
        departments: scope === "department" ? deptList : [],
        note: note.trim(),
        trial_credentials: trialCreds.trim(),
      });
      const who = scope === "company" ? "管理员" : "部门主管";
      setResult({
        ok: true,
        text: r.note || `已提交「${r.submission?.name ?? r.submission?.id ?? ""}」，等${who}审核通过后同事才能订阅。可以在连接器列表页「我提交的」里看进度。`,
      });
    } catch (err) {
      setResult({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="提交连接器">
      <div style={{ ...hint, fontSize: 12, marginBottom: "var(--space-3)" }}>
        提交后先进「待审核」：本部门范围由部门主管审，全公司范围由管理员审。审核通过后同事才能在市场里看到、订阅。
      </div>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
        <label>
          <div style={label}>连接器清单（manifest，yaml 格式）</div>
          <textarea
            rows={14}
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            placeholder={MANIFEST_EXAMPLE}
            style={{ ...input, fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
          <div style={hint}>
            id / name / version / description / mcp_command / tools 必填；密钥写成 <code>{"${SECRET_REF:名字}"}</code> 占位，不要把真密钥贴进来。
          </div>
        </label>

        <div>
          <div style={label}>范围（发给谁用）</div>
          <div style={{ display: "flex", gap: "var(--space-4)", fontSize: 13 }}>
            {(["department", "company"] as ShareScope[]).map((v) => (
              <label key={v} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                <input type="radio" name="mcp-scope" value={v} checked={scope === v} onChange={() => setScope(v)} />
                {SCOPE_LABEL[v]}
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  {v === "department" ? "（由部门主管审核）" : "（由管理员审核）"}
                </span>
              </label>
            ))}
          </div>
        </div>

        {scope === "department" && (
          <label>
            <div style={label}>给哪些部门</div>
            <input
              type="text"
              value={departments}
              onChange={(e) => setDepartments(e.target.value)}
              placeholder={me?.department || "部门名，多个用逗号隔开"}
              style={input}
            />
            <div style={{ ...hint, color: deptList.length ? "var(--text-muted)" : "var(--status-err)" }}>
              {deptList.length ? "多个部门用逗号隔开；每个部门由它自己的主管审。" : "至少写一个部门（默认是你自己的部门）。"}
            </div>
          </label>
        )}

        <label>
          <div style={label}>说明（给审核的人看，选填）</div>
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value.slice(0, 300))}
            placeholder="它接的是哪个系统、哪些人会用、有什么要注意的…"
            style={input}
          />
        </label>

        <label>
          <div style={label}>测试账号怎么拿（选填，但不写审批人就不能试用）</div>
          <textarea
            rows={2}
            value={trialCreds}
            onChange={(e) => setTrialCreds(e.target.value.slice(0, 500))}
            placeholder="例：测试环境地址 https://crm-test.example.com，测试账号找 IT 的小王申请；别写真密码"
            style={input}
          />
          <div style={hint}>
            审批人试用只允许连测试账号，不能拿自己的正式账号试别人提交的连接器。没写的话审批人只能看清单，不能装到自己电脑上试。
          </div>
        </label>

        <div style={{ display: "flex", gap: "var(--space-2)" }}>
          <button
            type="submit"
            disabled={busy || !canSubmit}
            style={{
              background: "var(--accent)",
              color: "white",
              border: "none",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: busy || !canSubmit ? "not-allowed" : "pointer",
              opacity: busy || !canSubmit ? 0.5 : 1,
              fontSize: 13,
            }}
          >
            {busy ? "提交中…" : "提交审核"}
          </button>
          <button
            type="button"
            onClick={() => navigate("/market/mcp")}
            style={{
              background: "transparent",
              border: "1px solid var(--border)",
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {result?.ok ? "回到列表" : "取消"}
          </button>
        </div>

        {result && (
          <div
            style={{
              padding: "var(--space-2) var(--space-3)",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-secondary)",
              color: result.ok ? "var(--status-ok)" : "var(--status-err)",
              fontSize: 13,
            }}
          >
            {result.ok ? "✓ " : "✗ "}{result.text}
          </div>
        )}
      </form>
    </Card>
  );
}
