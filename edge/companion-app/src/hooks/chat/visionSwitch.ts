/** Vision model 检查 — 抽自 useChat.ts (5/20 拆分).
 *
 * P3.5.140 (6/29 鸿波"不要再主动切 visionSwitch, 如果需要视觉选择的模型不支持, 直接报错"):
 * 之前自动切换被砍. 现在严格执行: 当前 picker 选的 model 不支持视觉 → 直接抛错,
 * 让员工自己 picker 切到视觉 model. 严格 picker 军规一致 — 系统不再"替员工做决定".
 *
 * 行为:
 *   1. 拉 catalog 查 current model supports_vision
 *   2. 支持: OK 继续 send (无 notice)
 *   3. 不支持 (或 catalog 没拉到 / current model 不在 catalog): 抛错 notice
 *      让 caller block send + 显错 提示员工手动切 picker
 *
 * 调用方 (useChat.ts:543) 改造: switched=false + notice!=null → 显错 block send,
 * 不再透明 setModelInStore.
 */

import { fetchCatalog } from "../../lib/tauri";
import type { CatalogModel } from "../../types/catalog";

export interface VisionCheckResult {
  /** picker 当前 model 支持视觉 → 继续 send */
  ok: boolean;
  /** 错误提示 (ok=true 时 null). 让 caller toast/banner 显, block send. */
  error: string | null;
}

export async function checkVisionSupport(
  currentModel: string,
): Promise<VisionCheckResult> {
  let models: CatalogModel[];
  try {
    const cat = await fetchCatalog();
    models = cat.models ?? [];
  } catch (e) {
    console.warn("[catfish chat] 拉 catalog 失败, vision 检查不通过:", e);
    return {
      ok: false,
      error: "⚠ 无法拉 catalog (gateway 可能没起). 检查 gateway 状态后重试.",
    };
  }

  const cur = models.find((m) => m.id === currentModel);
  if (cur && cur.supports_vision) {
    return { ok: true, error: null };
  }

  if (!cur) {
    return {
      ok: false,
      error: `⚠ 当前 model 「${currentModel}」不在 catalog (可能 api_key 没配 / yaml 拼写错). 请 picker 切到可用 model.`,
    };
  }

  // 当前 model 不支持视觉 — 列出 catalog 视觉可用 model 让员工自己挑.
  const visionPool = models.filter(
    (m) => m.supports_vision && m.api_key_configured,
  );
  if (visionPool.length === 0) {
    return {
      ok: false,
      error:
        `⚠ 当前 model 「${cur.display_name || currentModel}」不支持视觉, ` +
        "catalog 也没可用的视觉模型 (supports_vision=true 且配了 api_key 的为空). " +
        "检查内网 vision 模型配置, 或在 .env 配 GEMINI_API_KEY / DASHSCOPE_API_KEY 启用公共视觉模型.",
    };
  }
  const visionNames = visionPool
    .map((m) => m.display_name.split(" · ")[0] || m.id)
    .join(" / ");
  return {
    ok: false,
    error:
      `⚠ 当前 model 「${cur.display_name || currentModel}」不支持视觉. ` +
      `picker 切到视觉模型再发 (可用: ${visionNames}).`,
  };
}

/** @deprecated P3.5.140 (6/29 鸿波"不要再主动切 visionSwitch, 直接报错") —
 *  老自动切砍了. caller 改调 checkVisionSupport 仅检查不切. 留 stub
 *  防 git revert 容易, 下个 sprint 砍.
 */
export interface VisionSwitchResult {
  switched: boolean;
  newModel: string;
  notice: string | null;
}
