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
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import {
  meetingTemplateDelete, meetingTemplateSave, meetingTemplateUpload, type MinutesTemplate, type TemplateSlot,
} from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle } from "../Collab/roomLinkUi";
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

  // Esc 关弹窗
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const deleteBtn = editing && (
    <Btn kind="ghost" disabled={busy} onClick={() => void remove()}>
      {confirmDelete ? "再点一次确认删除" : "删除"}
    </Btn>
  );
  const kindLabel = (t: MinutesTemplate) => (t.kind === "xlsx" ? "Excel" : t.kind === "docx" ? "Word" : "文字");

  const listItem = (key: string, active: boolean, title: ReactNode, sub: string, onClick?: () => void) => (
    <button key={key} type="button" onClick={onClick} disabled={!onClick}
      style={{ ...itemBtn, background: active ? "var(--catfish-bg-cream)" : "transparent", cursor: onClick ? "pointer" : "default" }}>
      <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>{sub}</div>
    </button>
  );

  return (
    <div style={backdrop} role="dialog" aria-modal="true" aria-label="纪要模版" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div style={modal}>
        <div style={{ display: "flex", alignItems: "center", padding: "14px 18px", borderBottom: "1px solid var(--catfish-border)" }}>
          <div style={{ fontWeight: 600, fontSize: 15, flex: 1 }}>纪要模版</div>
          <button type="button" aria-label="关闭" onClick={onClose} style={closeBtn}>×</button>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "220px minmax(0, 1fr)", minHeight: 0, flex: 1 }}>
          {/* 左: 模版列表 */}
          <div style={{ borderRight: "1px solid var(--catfish-border)", padding: 10, display: "flex", flexDirection: "column", gap: 4, overflowY: "auto" }}>
            {listItem("default", false, "标准纪要", "内置 · 摘要 / 决议 / 待办 / 待定问题")}
            {templates.map((t) => listItem(t.id, editing?.id === t.id, t.name, kindLabel(t),
              () => open(t, isFileTemplate(t) ? "file" : "text")))}
            <div style={{ flex: 1 }} />
            <Btn kind="ghost" onClick={() => open(null, "upload")}>+ 上传 Word / Excel</Btn>
            <Btn kind="ghost" onClick={() => open(null, "text")}>+ 新建文字模版</Btn>
          </div>
          {/* 右: 选中的那个 */}
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12, overflowY: "auto", fontSize: 13 }}>
            {mode === "closed" && (
              <div style={{ color: "var(--catfish-text-muted)", lineHeight: 1.8 }}>
                单位有固定的 Word / Excel 纪要表, 点左下「上传 Word / Excel」, 生成时原样填好;<br />
                没有的话「新建文字模版」, 按自己的格式写一份 (先给示例)。<br />
                生成纪要时在纪要卡片右上角选模版。
              </div>
            )}

            {mode === "text" && (
              <>
                <input style={inputStyle} placeholder="模版名字, 比如「党委会纪要」「周例会」" value={name} maxLength={40}
                  onChange={(e) => setName(e.target.value)} />
                <textarea
                  style={{ ...inputStyle, minHeight: 300, fontFamily: "var(--font-mono, monospace)", fontSize: 12, resize: "vertical", lineHeight: 1.6 }}
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
                <Help title="怎么写?">{PLACEHOLDER_HELP}</Help>
                <div style={{ display: "flex", gap: 8 }}>
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
                <div style={{ color: "var(--catfish-text-muted)", lineHeight: 1.7 }}>
                  把单位现成的纪要表原样传上来, 小鲶认出要填的地方, 生成时只填空, 格式不动。
                </div>
                <div>
                  <Btn kind="primary" disabled={busy} onClick={() => fileInput.current?.click()}>
                    {busy ? "正在读模版…" : "选择 .docx / .xlsx 文件"}
                  </Btn>
                </div>
                <Help title="能认出哪些地方? 认不准怎么办?">{FILE_TEMPLATE_HELP}</Help>
              </>
            )}

            {mode === "file" && editing && (
              <>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{editing.name}</div>
                  <div style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
                    {kindLabel(editing)} 模版 · {editing.original_name}
                  </div>
                </div>
                {editing.slots && <SlotList slots={editing.slots} />}
                <Help title="认的不对?">{FILE_TEMPLATE_HELP}</Help>
                <div style={{ display: "flex", gap: 8 }}>
                  <Btn kind="ghost" disabled={busy} onClick={() => fileInput.current?.click()}>{busy ? "正在读模版…" : "替换文件"}</Btn>
                  {deleteBtn}
                </div>
              </>
            )}
            {error && <ErrorLine>{error}</ErrorLine>}
          </div>
        </div>
        <input ref={fileInput} type="file" accept=".docx,.xlsx" style={{ display: "none" }}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
      </div>
    </div>
  );
}

/** 说明文字默认收起, 要看再点开 —— 原来一大段常驻, 把按钮挤到下面去了 */
function Help({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details style={{ fontSize: 12, color: "var(--catfish-text-muted)" }}>
      <summary style={{ cursor: "pointer", userSelect: "none" }}>{title}</summary>
      <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.7, marginTop: 6 }}>{children}</div>
    </details>
  );
}

const backdrop: CSSProperties = {
  position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", background: "rgba(0,0,0,.28)",
};
const modal: CSSProperties = {
  width: "min(860px, calc(100vw - 32px))", height: "min(620px, calc(100vh - 48px))", display: "flex",
  flexDirection: "column", borderRadius: 12, background: "var(--catfish-bg)", boxShadow: "0 12px 40px rgba(0,0,0,.25)",
  color: "var(--catfish-text)", overflow: "hidden",
};
const itemBtn: CSSProperties = {
  textAlign: "left", padding: "8px 10px", border: "none", borderRadius: "var(--radius-sm)",
  color: "var(--catfish-text)", fontFamily: "inherit",
};
const closeBtn: CSSProperties = {
  border: "none", background: "transparent", fontSize: 20, lineHeight: 1, cursor: "pointer",
  color: "var(--catfish-text-muted)", padding: "2px 6px",
};
