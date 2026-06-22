/** 模型下拉选择 —— 从 catalog 里读所有模型,显示为 dropdown */

import { useCatalog } from "../../hooks/useCatalog";
import { savePickerState } from "../../lib/picker_state";
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
      onChange={(e) => {
        const next = e.target.value;
        // P3.5.80 (6/23 鸿波 catch): UI 切 picker 立即 sync ~/.catfish/picker_state.json,
        // 不等 chat.ts send 时才 fire. 之前真因: savePickerState 只在 chat.ts:243
        // 发送时 fire, picker UI 切了但没发消息 → picker_state.json 永远是上次发送时
        // 的旧 model → 微信/cron (走 P21/P23 picker 联动) 拿到旧值, 跟 UI 显示不一致.
        // 这里 fire-and-forget, 不阻塞 onChange.
        savePickerState(next);
        onChange(next);
      }}
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
