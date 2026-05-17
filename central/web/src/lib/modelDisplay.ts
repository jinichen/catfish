/** BL-AUDIT-UX-P0 (5/17 鸿波): catalog 内部 ID → 客户能读的产品名 + 颜色.
 *
 * 旧 audit 页直接显示 `catfish-public-nvidia-nemotron` 这种 catalog 路由名 — 工程师
 * 看得懂, 客户老板看不懂. 这里映射:
 *   - friendly: 产品名 ("NVIDIA Nemotron")
 *   - tier: 公网 / 私有 (右下角标签)
 *   - color: 数据可视化里区分 model 用 (横向 bar / donut chart 等)
 *   - dotEmoji: 列表行首小圆点, 一眼分 provider
 *
 * 添加新 model: 在 _MAP 加一行. 未知 ID 走 _fallback (蓝色 / "未知").
 */

export interface ModelDisplay {
  /** 友好产品名, 给客户老板看的 */
  friendly: string;
  /** "公网" / "私有" — 客户最关心的口径 */
  tier: "公网" | "私有";
  /** brand 色 (CSS color), 用于 horizontal bar / donut / line chart */
  color: string;
  /** 行首 emoji 圆点 */
  dotEmoji: string;
}

const _MAP: Record<string, ModelDisplay> = {
  // NVIDIA 系
  "catfish-public-nvidia-nemotron": {
    friendly: "NVIDIA Nemotron",
    tier: "公网",
    color: "#76b900",  // NVIDIA brand green
    dotEmoji: "🟢",
  },
  "catfish-public-nvidia-llama": {
    friendly: "NVIDIA Llama",
    tier: "公网",
    color: "#5fa8d3",
    dotEmoji: "🔵",
  },

  // DeepSeek
  "catfish-public-deepseek-flash": {
    friendly: "DeepSeek Flash",
    tier: "公网",
    color: "#7c3aed",
    dotEmoji: "🟣",
  },

  // Qwen / DashScope
  "catfish-public-qwen-flash": {
    friendly: "Qwen Flash",
    tier: "公网",
    color: "#f59e0b",
    dotEmoji: "🟠",
  },

  // Gemini
  "catfish-public-gemini-flash": {
    friendly: "Gemini Flash",
    tier: "公网",
    color: "#ef4444",
    dotEmoji: "🔴",
  },
  "catfish-public-gemini-pro": {
    friendly: "Gemini Pro",
    tier: "公网",
    color: "#dc2626",
    dotEmoji: "🔴",
  },

  // 私有 (客户内网)
  "catfish-private-main": {
    friendly: "私有主力",
    tier: "私有",
    color: "#6b7280",
    dotEmoji: "⚫",
  },
  "catfish-private-vision": {
    friendly: "私有视觉",
    tier: "私有",
    color: "#4b5563",
    dotEmoji: "⚫",
  },
  "catfish-private-embed": {
    friendly: "私有 Embed",
    tier: "私有",
    color: "#9ca3af",
    dotEmoji: "⚪",
  },
};

const _FALLBACK: ModelDisplay = {
  friendly: "未知模型",
  tier: "公网",
  color: "#94a3b8",
  dotEmoji: "⚪",
};

export function getModelDisplay(catalogId: string): ModelDisplay {
  return _MAP[catalogId] ?? _FALLBACK;
}
