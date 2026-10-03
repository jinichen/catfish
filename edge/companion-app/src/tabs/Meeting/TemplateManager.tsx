/**
 * 会议纪要模版管理 (10/3)。两种模版:
 *
 * - 文字模版: 一份 Markdown, 标题 / 表格照写, 括号里写这一节要写什么, 小鲶照着写。
 * - Word / Excel 模版 (下午加): 单位定死的 .docx / .xlsx 表单, 原样上传。上传时先认出
 *   要填的空 (标签格旁边的空格、表头下面的空行、「参会人员：」、或员工写的 {{占位符}}),
 *   列出来让员工核对; 生成纪要时把这张表填好另存, 字体边框合并格都不动。
 *
 * 缺省模版 (原来那份"摘要 / 决议 / 待定问题 / 待办") 不在这里, 不能改也不能删。
 * 都存在本机 ~/.catfish/meeting-templates/ (Windows: %USERPROFILE%\.catfish\meeting-templates)。
 */
import { useRef, useState } from "react";

import {
  meetingTemplateDelete, meetingTemplateSave, meetingTemplateUpload, type MinutesTemplate, type TemplateSlot,
} from "../../lib/tauri_meeting";
import { Btn, cardStyle, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
import { EXAMPLE_TEMPLATE, FILE_TEMPLATE_HELP, isFileTemplate, PLACEHOLDER_HELP, readFileBase64 } from "./minutesTemplates";

interface Props {
  templates: MinutesTemplate[];
  onChanged: (selectId?: string) => void;
  onClose: () => void;
}

/** closed: 只列模版; text: 新建 / 改文字模版; upload: 上传新的 Word / Excel; file: 看一个文件模版 */
type Mode = "closed" | "text" | "upload" | "file";

function SlotList({ slots }: { slots: TemplateSlot[] }) {
  const texts = slots.filter((s) => s.kind === "text").map((s) => s.label);
  const tables = slots.filter((s) => s.kind === "table");
  return (
    <div style={{ fontSize: 12, lineHeight: 1.7 }}>
      <div>认出 {slots.length} 处要填:</div>
      {texts.length > 0 && <div>· {texts.join("、")}</div>}
      {tables.map((t) => (
        <div key={t.id}>· 表格「{t.label}」: {(t.columns ?? []).join(" / ")} (每个待办一行, 行不够自动加)</div>
      ))}
    </div>
  );
}

export default function TemplateManager({ templates, onChanged, onClose }: Props) {
  const [mode, setMode] = useState<Mode>("closed");
  const [editing, setEditing] = useState<MinutesTemplate | null>(null);
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 删除点两下: Tauri WebView 里 window.confirm() 静默返回 null (CronJobsCard 7/6 记过), 不能用
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function open(t: MinutesTemplate | null, next: Mode) {
    setEditing(t);
    setName(t?.name ?? "");
    setBody(t && !isFileTemplate(t) ? t.body : EXAMPLE_TEMPLATE);
    setError(null);
    setConfirmDelete(false);
    setMode(next);
  }

  async function saveText() {
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

  /** 上传新的, 或给 editing 那个文件模版换文件 */
  async function upload(file: File) {
    const finalName = name.trim() || file.name.replace(/\.(docx|xlsx)$/i, "");
    setBusy(true);
    setError(null);
    try {
      const saved = await meetingTemplateUpload(editing?.id ?? null, finalName, file.name, await readFileBase64(file));
      onChanged(saved.id);
      open(saved, "file");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
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
      setMode("closed");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const deleteBtn = editing && (
    <Btn kind="ghost" disabled={busy} onClick={() => void remove()}>
      {confirmDelete ? "再点一次确认删除 (已生成的纪要不受影响)" : "删除"}
    </Btn>
  );

  return (
    <div style={{ ...cardStyle, gap: "var(--space-2)", fontSize: 13 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
        <div style={{ fontWeight: 600, flex: 1 }}>纪要模版</div>
        <Btn kind="ghost" onClick={onClose}>关闭</Btn>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {templates.map((t) => (
          <Btn key={t.id} kind={editing?.id === t.id ? "primary" : "ghost"}
            onClick={() => open(t, isFileTemplate(t) ? "file" : "text")}>
            {isFileTemplate(t) ? `${t.kind === "xlsx" ? "Excel" : "Word"} · ` : ""}{t.name}
          </Btn>
        ))}
        <Btn kind="ghost" onClick={() => open(null, "text")}>+ 新建文字模版</Btn>
        <Btn kind="ghost" onClick={() => open(null, "upload")}>+ 上传 Word / Excel 模版</Btn>
      </div>
      <input ref={fileInput} type="file" accept=".docx,.xlsx" style={{ display: "none" }}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />

      {mode === "text" && (
        <>
          <input style={inputStyle} placeholder="模版名字, 比如「党委会纪要」「周例会」" value={name} maxLength={40}
            onChange={(e) => setName(e.target.value)} />
          <textarea
            style={{ ...inputStyle, minHeight: 280, fontFamily: "var(--font-mono, monospace)", fontSize: 12, resize: "vertical" }}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", whiteSpace: "pre-wrap" }}>{PLACEHOLDER_HELP}</div>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <Btn kind="primary" disabled={busy || !name.trim() || !body.trim()} onClick={() => void saveText()}>
              {editing ? "保存修改" : "保存模版"}
            </Btn>
            {deleteBtn}
          </div>
        </>
      )}

      {mode === "upload" && (
        <>
          <input style={inputStyle} placeholder="模版名字 (不填就用文件名)" value={name} maxLength={40}
            onChange={(e) => setName(e.target.value)} />
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", whiteSpace: "pre-wrap" }}>{FILE_TEMPLATE_HELP}</div>
          <div>
            <Btn kind="primary" disabled={busy} onClick={() => fileInput.current?.click()}>
              {busy ? "正在读模版…" : "选择 .docx / .xlsx 文件"}
            </Btn>
          </div>
        </>
      )}

      {mode === "file" && editing && (
        <>
          <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
            {editing.kind === "xlsx" ? "Excel" : "Word"} 模版 · 原文件「{editing.original_name}」 · 生成时把这张表填好另存, 格式不动
          </div>
          {editing.slots && <SlotList slots={editing.slots} />}
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            认的不对? 在模版里要填的地方写上占位符 (见「上传」里的说明), 再「替换文件」。
          </div>
          <div style={{ display: "flex", gap: "var(--space-2)" }}>
            <Btn kind="ghost" disabled={busy} onClick={() => fileInput.current?.click()}>{busy ? "正在读模版…" : "替换文件"}</Btn>
            {deleteBtn}
          </div>
        </>
      )}

      {mode === "closed" && templates.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
          还没有自定义模版。单位有固定的 Word / Excel 纪要表就直接上传; 没有就新建文字模版 (先给一份示例)。
        </div>
      )}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
