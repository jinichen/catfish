/** Vision model 自动选择 — 抽自 useChat.ts (5/20 拆分).
 *
 * 员工带图发送时, 如果当前模型不支持视觉, 透明切到支持的 model:
 *   1. 拉 catalog 找 supports_vision=true 的模型
 *   2. P3.5.139 (6/29 鸿波"都要去除硬编码"): 优先按 role `vision` 拉 roles.yaml,
 *      命中且 api_key_configured 真**直接用**. 没拉到 / 不可用 → visionPool 第一个.
 *   3. 改当前 store 的 model, 聊天里追加一条 system 消息 "已切到 X"
 *
 * 失败兜底: catalog 拉不到 / 没视觉模型 → 用原模型硬发, 上游报错员工自己决策.
 */

import { fetchCatalog, fetchRole } from "../../lib/tauri";
import type { CatalogModel } from "../../types/catalog";

export interface VisionSwitchResult {
  switched: boolean;
  /** 改后的 model id (没切就是原值) */
  newModel: string;
  /** 给员工看的提示 (没切就是 null) */
  notice: string | null;
}

export async function maybeSwitchToVision(
  currentModel: string,
): Promise<VisionSwitchResult> {
  let models: CatalogModel[];
  try {
    const cat = await fetchCatalog();
    models = cat.models ?? [];
  } catch (e) {
    console.warn("[catfish chat] 拉 catalog 失败, 不切视觉模型:", e);
    return { switched: false, newModel: currentModel, notice: null };
  }

  const cur = models.find((m) => m.id === currentModel);
  if (cur && cur.supports_vision) {
    return { switched: false, newModel: currentModel, notice: null };
  }

  const visionPool = models.filter(
    (m) => m.supports_vision && m.api_key_configured,
  );
  if (visionPool.length === 0) {
    return {
      switched: false,
      newModel: currentModel,
      notice:
        "⚠ 没有可用的视觉模型 (supports_vision=true 且配了 API key 的为空)。" +
        "检查内网 vision 模型配置, 或在 .env 配 GEMINI_API_KEY / DASHSCOPE_API_KEY 启用公共视觉模型。",
    };
  }

  // P3.5.139 (6/29 鸿波"都要去除硬编码"): 优先按 roles.yaml `vision` role 选,
  // 不再写死 ["catfish-private-vision", "catfish-public-vision"] 数组.
  // 客户改 roles.yaml `vision: customer-vision-model` 这里跟着走.
  const visionRole = await fetchRole("vision");
  if (visionRole) {
    const m = visionPool.find((x) => x.id === visionRole);
    if (m) {
      return {
        switched: true,
        newModel: m.id,
        notice: `🔁 检测到图片附件, 已切到「${m.display_name.split(" · ")[0] || m.id}」(原 ${cur?.display_name || currentModel} 不支持视觉)`,
      };
    }
    // role 配的 model 不在 catalog (没起 / 没 api key) — log + fallback visionPool 第一个
    console.warn(
      `[catfish chat] roles.yaml vision=${visionRole} 但 catalog 找不到 / api_key 没配, fallback visionPool 第一个`,
    );
  }

  // roles.yaml vision 没拉到 / role 配的 model 不可用 → visionPool 第一个
  const first = visionPool[0];
  return {
    switched: true,
    newModel: first.id,
    notice: `🔁 检测到图片附件, 已切到「${first.display_name}」(原模型不支持视觉)`,
  };
}
