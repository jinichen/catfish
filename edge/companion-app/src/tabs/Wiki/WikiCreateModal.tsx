/** BL-CATFISH-WIKI-MODE P3.3.8 (6/4) — + 新建 entity/concept form modal.
 *
 * 字段:
 *   - kind: entity | concept (radio)
 *   - title: 必填
 *   - subtype: entity 真**person/org/system/cert/project**, concept 真**process/rule/principle/standard**
 *   - tags: comma-separated
 *   - related: comma-separated (name only, 真**真**真**不**真**`[[]]` 真**真**rendered 时加**)
 *   - body: textarea (2-5 段, 真**Markdown** 真**支持**真)
 *
 * Submit → wikiCreateEntityOrConcept → reload files + close modal + jump select 真**新 file**.
 */

import { useState } from "react";
import { wikiCreateEntityOrConcept } from "../../lib/tauri";
import { useWikiStore } from "../../store/wiki";

interface Props {
  onClose: () => void;
}

const ENTITY_SUBTYPES = ["person", "org", "system", "cert", "project"];
const CONCEPT_SUBTYPES = ["process", "rule", "principle", "standard"];

export default function WikiCreateModal({ onClose }: Props) {
  const [kind, setKind] = useState<"entity" | "concept">("entity");
  const [title, setTitle] = useState("");
  const [subtype, setSubtype] = useState(ENTITY_SUBTYPES[0]);
  const [tagsStr, setTagsStr] = useState("");
  const [relatedStr, setRelatedStr] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFiles = useWikiStore((s) => s.loadFiles);
  const selectFile = useWikiStore((s) => s.selectFile);

  const subtypes = kind === "entity" ? ENTITY_SUBTYPES : CONCEPT_SUBTYPES;

  function handleKindChange(k: "entity" | "concept") {
    setKind(k);
    setSubtype(k === "entity" ? ENTITY_SUBTYPES[0] : CONCEPT_SUBTYPES[0]);
  }

  async function handleSubmit() {
    if (!title.trim()) {
      setError("title 必填");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const tags = tagsStr
        .split(/[,，]/)
        .map((t) => t.trim())
        .filter((t) => t.length > 0);
      const related = relatedStr
        .split(/[,，]/)
        .map((r) => r.trim().replace(/^\[\[|\]\]$/g, ""))
        .filter((r) => r.length > 0);
      const result = await wikiCreateEntityOrConcept({
        kind,
        title: title.trim(),
        subtype,
        tags,
        related,
        body: body.trim() || `# ${title.trim()}\n\n(待补充)`,
      });
      await loadFiles();
      await selectFile(result.rel_path);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: 540,
          maxWidth: "90vw",
          maxHeight: "90vh",
          background: "var(--catfish-bg)",
          borderRadius: 12,
          padding: "var(--space-4)",
          overflowY: "auto",
          boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
        }}
      >
        <h3 style={{ margin: "0 0 var(--space-3) 0", fontSize: 16 }}>
          新建 {kind === "entity" ? "实体" : "概念"}
        </h3>

        <Field label="类型">
          <div style={{ display: "flex", gap: 8 }}>
            {(["entity", "concept"] as const).map((k) => (
              <button
                key={k}
                onClick={() => handleKindChange(k)}
                style={{
                  flex: 1,
                  padding: "6px 12px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  background: kind === k ? "var(--catfish-accent, #4a9eff)" : "transparent",
                  color: kind === k ? "#fff" : "var(--catfish-text)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {k === "entity" ? "实体 (entity)" : "概念 (concept)"}
              </button>
            ))}
          </div>
        </Field>

        <Field label="标题 *">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={kind === "entity" ? "例: 陈鸿波 / 中电福富 / ISO 27001" : "例: 资质评估流程 / 月度通报模板"}
            style={inputStyle}
          />
        </Field>

        <Field label="子类型">
          <select
            value={subtype}
            onChange={(e) => setSubtype(e.target.value)}
            style={inputStyle}
          >
            {subtypes.map((st) => (
              <option key={st} value={st}>
                {st}
              </option>
            ))}
          </select>
        </Field>

        <Field label="标签 (逗号分隔)">
          <input
            type="text"
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="资质, 合规, 申报"
            style={inputStyle}
          />
        </Field>

        <Field label="相关 (逗号分隔, 不带 [[]] )">
          <input
            type="text"
            value={relatedStr}
            onChange={(e) => setRelatedStr(e.target.value)}
            placeholder="陈鸿波, FFCS数字鲶鱼, 资质申报流程"
            style={inputStyle}
          />
        </Field>

        <Field label="正文 (Markdown 支持)">
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="2-4 段正文. 概述 / 关键关系 / 案例..."
            rows={6}
            style={{ ...inputStyle, fontFamily: "ui-monospace, monospace", resize: "vertical" }}
          />
        </Field>

        {error && (
          <div style={{ color: "var(--status-err, #d33)", fontSize: 12, marginBottom: 8 }}>
            ✗ {error}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: "var(--space-3)" }}>
          <button
            onClick={onClose}
            style={{
              padding: "6px 14px",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              background: "transparent",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !title.trim()}
            style={{
              padding: "6px 14px",
              border: "none",
              borderRadius: 4,
              background: saving || !title.trim() ? "#aaa" : "var(--catfish-accent, #4a9eff)",
              color: "#fff",
              cursor: saving || !title.trim() ? "not-allowed" : "pointer",
              fontSize: 13,
            }}
          >
            {saving ? "保存中..." : "✓ 创建"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <label style={{ display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4, color: "var(--catfish-text)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  fontSize: 13,
  border: "1px solid var(--catfish-border)",
  borderRadius: 4,
  background: "var(--catfish-bg-elevated)",
  color: "var(--catfish-text)",
  boxSizing: "border-box",
};
