/** BL-CATFISH-WIKI-MODE P3.3.8 (6/4) — + 新建 entity/concept form modal.
 *
 * 字段:
 *   - kind: entity | concept | system (radio)
 *   - title: 必填
 *   - subtype: entity person/org/system/cert/project, concept process/rule/principle/standard,
 *              system 强制 "system" (concept_type, P3.5.109 isSystemConcept 真判定字段)
 *   - tags: comma-separated
 *   - related: comma-separated (name only, 真不`[[]]` 真rendered 时加)
 *   - body: textarea (2-5 段, Markdown 支持真)
 *
 * P3.5.109 (6/25 鸿波 catch "+ 新建少了体系"): 加第 3 选项"体系 (system)" —
 * 后端**0 改动** (wiki_write.rs concept_type 无 enum 校验), UI 仍调 kind:"concept"
 * 但 concept_type="system", 自动归"🌟 顶级体系"组 + WikiGraph 子树显示.
 *
 * Submit → wikiCreateEntityOrConcept → reload files + close modal + jump select 新 file.
 */

import { useState } from "react";
import { wikiCreateEntityOrConcept } from "../../lib/tauri";
import { useWikiStore } from "../../store/wiki";

interface Props {
  onClose: () => void;
  // P3.5.110 (6/25): 鸿波点 dangling wikilink → 弹 modal prefill title + 默认 kind=system.
  prefillTitle?: string;
  prefillKind?: "entity" | "concept" | "system";
}

const ENTITY_SUBTYPES = ["person", "org", "system", "cert", "project"];
const CONCEPT_SUBTYPES = ["process", "rule", "principle", "standard"];
// P3.5.109: "体系" UI-only kind, 底层仍 concept, concept_type 强制 "system".
const SYSTEM_SUBTYPE = "system";

// UI kind 3 选项, 真但 wikiCreateEntityOrConcept 只接受 entity/concept.
// system 映射到 concept 真后端层 0 改动.
type UiKind = "entity" | "concept" | "system";

function uiKindToBackend(uk: UiKind): "entity" | "concept" {
  return uk === "entity" ? "entity" : "concept"; // system → concept (subtype=system)
}

function defaultSubtype(uk: UiKind): string {
  if (uk === "entity") return ENTITY_SUBTYPES[0];
  if (uk === "concept") return CONCEPT_SUBTYPES[0];
  return SYSTEM_SUBTYPE; // system → 强制 "system"
}

export default function WikiCreateModal({ onClose, prefillTitle, prefillKind }: Props) {
  // P3.5.110: prefill 初始化 — 鸿波点 dangling 带 title + kind 跳进来.
  const [kind, setKind] = useState<UiKind>(prefillKind ?? "entity");
  const [title, setTitle] = useState(prefillTitle ?? "");
  const [subtype, setSubtype] = useState(defaultSubtype(prefillKind ?? "entity"));
  const [tagsStr, setTagsStr] = useState("");
  const [relatedStr, setRelatedStr] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFiles = useWikiStore((s) => s.loadFiles);
  const selectFile = useWikiStore((s) => s.selectFile);

  // P3.5.109: subtype 3 路径 — entity / concept / system (system 单选 system)
  const subtypes =
    kind === "entity" ? ENTITY_SUBTYPES : kind === "concept" ? CONCEPT_SUBTYPES : [SYSTEM_SUBTYPE];

  function handleKindChange(k: UiKind) {
    setKind(k);
    setSubtype(defaultSubtype(k));
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
      // P3.5.109: UI kind "system" → backend "concept" 0 后端改动, concept_type=system
      // WikiTree P3.5.107 A 真自动归"🌟 顶级体系"组 (related 空 → 顶级).
      // P3.5.108 WikiGraph isSystemConcept 真升级看 subtype === "system" 优先于 title heuristic.
      const result = await wikiCreateEntityOrConcept({
        kind: uiKindToBackend(kind),
        title: title.trim(),
        subtype,
        tags,
        related,
        body:
          body.trim() ||
          (kind === "system"
            ? `# ${title.trim()}\n\n(顶级体系真定义范围 + 列子级类目 + 适用场景)`
            : `# ${title.trim()}\n\n(待补充)`),
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
          新建 {kind === "entity" ? "实体" : kind === "concept" ? "概念" : "体系"}
        </h3>

        <Field label="类型">
          <div style={{ display: "flex", gap: 8 }}>
            {(["entity", "concept", "system"] as const).map((k) => (
              <button
                key={k}
                onClick={() => handleKindChange(k)}
                style={{
                  flex: 1,
                  padding: "6px 12px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  background:
                    kind === k
                      ? k === "system"
                        ? "var(--catfish-orange, #F47B3D)" // P3.5.109: 体系橙色, 跟 WikiTree "🌟 顶级体系"组真视觉一致
                        : "var(--catfish-accent, #4a9eff)"
                      : "transparent",
                  color: kind === k ? "#fff" : "var(--catfish-text)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {k === "entity"
                  ? "实体 (entity)"
                  : k === "concept"
                  ? "概念 (concept)"
                  : "🌟 体系 (system)"}
              </button>
            ))}
          </div>
        </Field>

        <Field label="标题 *">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={
              kind === "entity"
                ? "例: 陈鸿波 / 中电福富 / ISO 27001"
                : kind === "concept"
                ? "例: 资质评估流程 / 月度通报模板"
                : "例: 政企客户体系 / 信通院评估体系 / 渠道合作体系"
            }
            style={inputStyle}
          />
        </Field>
        {kind === "system" && (
          <div
            style={{
              marginTop: -8,
              marginBottom: "var(--space-3)",
              padding: "6px 10px",
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              background: "rgba(244, 123, 61, 0.06)",
              borderRadius: 4,
              borderLeft: "2px solid var(--catfish-orange, #F47B3D)",
            }}
          >
            💡 <strong>顶级体系</strong> — 进 WikiTree "🌟 顶级体系"组 + 选它后 WikiGraph 显整体系子树.
            "相关" 字段建议留空 (顶级体系没有上位), 下属概念在 body 写 [[本体系名]] 即自动挂下来.
          </div>
        )}

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
