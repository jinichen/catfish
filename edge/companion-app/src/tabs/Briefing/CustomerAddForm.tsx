/** 11/3: 早安「+ 加客户」小表单 —— 原来只能在对话里说或去知识体系建实体, 早安页没有入口。
 *
 * 员工自己填的就是确认, 直接建 (tool-bridge customers.add_customer): 同名客户已有时报错不改,
 * 只是从文档里认出来的那家就认领那一页。阶段取销售模板, 默认第一个 (线索)。
 */
import { useEffect, useState } from "react";

import { salesCustomerAdd, salesTemplateGet } from "../../lib/tauri_sales";

const field = {
  fontSize: 12, padding: "4px 8px", background: "var(--catfish-bg)", color: "var(--catfish-text)",
  border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)", fontFamily: "inherit", minWidth: 0,
} as const;

export default function CustomerAddForm({ onDone, onCancel }: { onDone: (name: string) => void; onCancel: () => void }) {
  const [stages, setStages] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [stage, setStage] = useState("");
  const [contact, setContact] = useState("");
  const [email, setEmail] = useState("");
  const [wechat, setWechat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void salesTemplateGet()
      .then((t) => {
        const names = t.stages.map((s) => s.name);
        setStages(names);
        setStage((cur) => cur || names[0] || "");
      })
      .catch(() => setStages([]));
  }, []);

  async function save() {
    if (!name.trim()) {
      setError("先填客户公司名");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const person = { name: contact.trim(), email: email.trim(), wechat: wechat.trim() };
      const contacts = person.name || person.email || person.wechat ? [person] : [];
      await salesCustomerAdd(name.trim(), stage || null, contacts);
      onDone(name.trim());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-label="加客户"
      style={{
        display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", padding: "8px 10px", fontSize: 12,
        background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-sm)",
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
        if (e.key === "Enter") void save();
      }}
    >
      <input autoFocus aria-label="客户公司名" placeholder="客户公司名 (必填)" value={name} onChange={(e) => setName(e.target.value)}
        style={{ ...field, width: 180 }} />
      {stages.length > 0 && (
        <select aria-label="阶段" value={stage} onChange={(e) => setStage(e.target.value)} style={field}>
          {stages.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      )}
      <input aria-label="联系人" placeholder="联系人 (选填)" value={contact} onChange={(e) => setContact(e.target.value)}
        style={{ ...field, width: 110 }} />
      <input aria-label="邮箱" placeholder="邮箱 (选填)" value={email} onChange={(e) => setEmail(e.target.value)}
        style={{ ...field, width: 160 }} />
      <input aria-label="微信" placeholder="微信备注名 (选填)" value={wechat} onChange={(e) => setWechat(e.target.value)}
        style={{ ...field, width: 130 }} />
      <button type="button" className="chat-composer__primary-button" disabled={busy} onClick={() => void save()}>
        {busy ? "保存中…" : "保存"}
      </button>
      <button type="button" className="chat-composer__secondary-button" onClick={onCancel}>取消</button>
      {error && <span style={{ color: "var(--catfish-orange)", flexBasis: "100%" }}>{error}</span>}
    </div>
  );
}
