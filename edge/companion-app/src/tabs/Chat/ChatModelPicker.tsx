/** 模型下拉选择 —— 从 catalog 里读所有模型,显示为 dropdown */

import { useCatalog } from "../../hooks/useCatalog";
import type { CatalogModel } from "../../types/catalog";

interface Props {
  current: string;
  onChange: (modelId: string) => void;
}

function modelLabel(m: CatalogModel): string {
  // 用 display_name 但截短: "Qwen3.5 122B (A10B MoE) · 主力" → "Qwen3.5 122B"
  const base = m.display_name.split(" · ")[0] || m.display_name;
  return base.length > 30 ? `${base.slice(0, 28)}…` : base;
}

function modelStatusEmoji(m: CatalogModel): string {
  if (!m.api_key_configured) return "○";
  if (m.is_reachable === true) return "●";
  if (m.is_reachable === false) return "◐";
  return "○";
}

export default function ChatModelPicker({ current, onChange }: Props) {
  const { catalog } = useCatalog();
  const models = catalog?.models ?? [];

  return (
    <select
      value={current}
      onChange={(e) => onChange(e.target.value)}
      style={{
        padding: "4px 8px",
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        background: "var(--catfish-bg-elevated)",
        color: "var(--catfish-text)",
        fontFamily: "var(--font-mono)",
        cursor: "pointer",
        minWidth: 180,
      }}
    >
      {models.length === 0 && <option value={current}>{current}</option>}
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {modelStatusEmoji(m)} {modelLabel(m)}
        </option>
      ))}
    </select>
  );
}
