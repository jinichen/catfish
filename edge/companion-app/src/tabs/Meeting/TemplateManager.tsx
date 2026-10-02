/**
 * 会议纪要模版管理 (10/3): 新建 / 改 / 删员工自己的模版。
 *
 * 模版是一份 Markdown: 标题层级、表格照写, 括号里写这一节要写什么, 小鲶照着填。
 * 缺省模版 (原来那份"摘要 / 决议 / 待定问题 / 待办") 不在这里, 不能改也不能删。
 * 存在本机 ~/.catfish/meeting-templates/ (Windows: %USERPROFILE%\.catfish\meeting-templates)。
 */
import { useState } from "react";

import { meetingTemplateDelete, meetingTemplateSave, type MinutesTemplate } from "../../lib/tauri_meeting";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { EXAMPLE_TEMPLATE, PLACEHOLDER_HELP } from "./minutesTemplates";

interface Props {
  templates: MinutesTemplate[];
  onChanged: (selectId?: string) => void;
  onClose: () => void;
}

export default function TemplateManager({ templates, onChanged, onClose }: Props) {
  const [editing, setEditing] = useState<MinutesTemplate | null>(null);
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  // 删除点两下: Tauri WebView 里 window.confirm() 静默返回 null (CronJobsCard 7/6 记过), 不能用
  const [confirmDelete, setConfirmDelete] = useState(false);

  function open(t: MinutesTemplate | null) {
    setEditing(t);
    setName(t?.name ?? "");
    setBody(t?.body ?? EXAMPLE_TEMPLATE);
    setError(null);
    setIsOpen(true);
    setConfirmDelete(false);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await meetingTemplateSave(editing?.id ?? null, name, body);
      onChanged(saved.id);
      setEditing(saved);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!editing) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 3000);
      return;
    }
    setConfirmDelete(false);
    setBusy(true);
    try {
      await meetingTemplateDelete(editing.id);
      onChanged();
      setEditing(null);
      setIsOpen(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ ...cardStyle, gap: "var(--space-2)", fontSize: 13 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <div style={{ fontWeight: 600, flex: 1 }}>纪要模版</div>
        <Btn kind="ghost" onClick={onClose}>关闭</Btn>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {templates.map((t) => (
          <Btn key={t.id} kind={editing?.id === t.id ? "primary" : "ghost"} onClick={() => open(t)}>{t.name}</Btn>
        ))}
        <Btn kind="ghost" onClick={() => open(null)}>+ 新建模版</Btn>
      </div>
      {isOpen && (
        <>
          <input
            style={inputStyle}
            placeholder="模版名字, 比如「党委会纪要」「周例会」"
            value={name}
            maxLength={40}
            onChange={(e) => setName(e.target.value)}
          />
          <textarea
            style={{ ...inputStyle, minHeight: 280, fontFamily: "var(--font-mono, monospace)", fontSize: 12, resize: "vertical" }}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", whiteSpace: "pre-wrap" }}>{PLACEHOLDER_HELP}</div>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <Btn kind="primary" disabled={busy || !name.trim() || !body.trim()} onClick={() => void save()}>
              {editing ? "保存修改" : "保存模版"}
            </Btn>
            {editing && (
              <Btn kind="ghost" disabled={busy} onClick={() => void remove()}>
                {confirmDelete ? "再点一次确认删除 (已生成的纪要不受影响)" : "删除"}
              </Btn>
            )}
          </div>
        </>
      )}
      {!isOpen && templates.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
          还没有自定义模版。点「新建模版」, 会先给一份示例, 照着改成你们单位的格式。
        </div>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
