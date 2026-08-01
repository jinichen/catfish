/** 模型下拉选择 —— 从 catalog 里读所有模型,显示为 dropdown */

import { useCatalog } from "../../hooks/useCatalog";
import { savePickerState } from "../../lib/picker_state";
import { codexBackendSelectModel } from "../../lib/tauri";
import type { CatalogModel } from "../../types/catalog";
import { useState } from "react";

interface Props {
  current: string;
  onChange: (modelId: string, pickedByUser?: boolean, runtimeAlreadySynced?: boolean) => void;
}

function modelLabel(m: CatalogModel): string {
  // 用 display_name 但截短: "Qwen3.5 122B (A10B MoE) · 主力" → "Qwen3.5 122B"
  const base = m.display_name.split(" · ")[0] || m.display_name;
  return base.length > 30 ? `${base.slice(0, 28)}…` : base;
}

export default function ChatModelPicker({ current, onChange }: Props) {
  const { catalog } = useCatalog();
  const models = catalog?.models ?? [];
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const gatewayModels = models.filter((model) => model.source !== "codex");
  const codexModels = models.filter((model) => model.source === "codex");

  const options = (items: CatalogModel[]) =>
    items.map((m) => (
      <option key={`${m.source ?? "gateway"}:${m.id}`} value={m.id} disabled={m.selectable === false}>
        {m.selectable === false ? "不可用 · " : ""}{modelLabel(m)}
      </option>
    ));

  return (
    <div className="chat-model-picker">
      <select
        value={current}
        disabled={switching}
        aria-label="选择聊天模型"
        onChange={async (e) => {
          const next = e.target.value;
          setSwitching(true);
          setError(null);
          try {
            // 先完成 runtime 对齐再更新 store，避免员工刚选 Codex 就立即
            // 发送时，请求抢在 config 写入前到达 Hermes。
            await codexBackendSelectModel(next);
            savePickerState(next);
            onChange(next, true, true);
            window.dispatchEvent(new CustomEvent("catfish:catalog-refresh"));
          } catch (value) {
            setError(String(value));
          } finally {
            setSwitching(false);
          }
        }}
        className="chat-model-picker__select"
        data-error={!!error || undefined}
        data-switching={switching || undefined}
      >
        {models.length === 0 && <option value={current}>{current}</option>}
        {current && !models.some((model) => model.id === current) && (
          <option value={current}>{current}</option>
        )}
        {gatewayModels.length > 0 && <optgroup label="公司 / 通用模型">{options(gatewayModels)}</optgroup>}
        {codexModels.length > 0 && <optgroup label="Codex · ChatGPT">{options(codexModels)}</optgroup>}
      </select>
      {error && (
        <span title={error} className="chat-model-picker__error">
          切换失败，请检查 Codex 登录
        </span>
      )}
    </div>
  );
}
