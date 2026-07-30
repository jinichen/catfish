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

// ── 运行时覆盖层 (7/30) ──────────────────────────────────────────────
//
// ## 为什么加这一层
//
// 上面那两张硬编码表 (_MAP / _PRICE_RMB_PER_1K_TOKEN) 的注释写着
// "添加新 model: 在 _MAP 加一行"。模型改成能在 /admin/models 界面上增删改
// 之后, 这句话就跟产品行为矛盾了: 客户加模型是**运行时操作**, 而补显示名和
// 单价却要改前端代码 + 重新构建 + 重新部署。
//
// 不补的后果不是"缺点样式", 是**数字是错的**:
//   · 审计页显示"未知模型 ⚪"
//   · 成本按兜底价 0.001 算 —— 而真实单价从 0.00005 到 0.0218 差 400 倍
//
// 这正是 7/30 排查 catfish-public-qwen-flash 改名事故时看到的表现 (那次
// 成本被高估 2.22 倍)。区别在于: 那次要改名才触发, 而可编辑模型会让它
// **每次新增模型都必然发生**。
//
// ## 硬编码表为什么不删
//
// 审计数据是**历史**的。模型被删掉之后, 审计页仍要展示它历史上的用量和成本,
// 而那时配置里已经查不到它了。所以保留硬编码表, 但用途从"唯一来源"降级成
// "配置里没有时的兜底" —— 那种情况是真的需要兜底, 不是懒。
//
// 优先级: 运行时配置 > 硬编码表 > 通用兜底。

/** 运行时能拿到的模型元信息. 只写这一层真正会用的字段 ——
 *  用 `[k: string]: unknown` 那种宽松签名的话, CatalogModel 这类具体接口
 *  反而传不进来 (缺索引签名), 而且也失去了字段名写错时的检查。 */
export interface RuntimeModelMeta {
  id: string;
  display_name?: string;
  tier?: string;
  color?: string | null;
  dot_emoji?: string | null;
  price_per_1k_tokens?: number | null;
}

let _runtime: Record<string, RuntimeModelMeta> = {};

/** 由 catalog 加载方调用一次, 把运行时模型元信息灌进来.
 *
 * 没调用也不会坏 —— 只是退回硬编码表, 也就是 7/30 之前的行为。
 */
export function setRuntimeModelMeta(models: RuntimeModelMeta[]): void {
  const next: Record<string, RuntimeModelMeta> = {};
  for (const m of models) {
    if (m.id) next[m.id] = m;
  }
  _runtime = next;
}

export function getModelDisplay(catalogId: string): ModelDisplay {
  const rt = _runtime[catalogId];
  const base = _MAP[catalogId] ?? _FALLBACK;
  if (!rt) return base;
  return {
    // display_name 是模型配置里本来就有的字段, 优先用它 —— 客户改了显示名
    // 应该在审计页立刻体现, 而不是等我们改前端。
    friendly: rt.display_name || base.friendly,
    tier: rt.tier === "private" ? "私有" : rt.tier === "public" ? "公网" : base.tier,
    color: rt.color || base.color,
    dotEmoji: rt.dot_emoji || base.dotEmoji,
  };
}

/** 这个模型的成本是不是估的 (配置和硬编码表里都没有单价).
 *
 * 给界面用: 成本栏旁边该标一下"估算", 而不是把一个兜底数字当准确值展示。
 */
export function isCostEstimated(catalogId: string): boolean {
  return (
    _runtime[catalogId]?.price_per_1k_tokens == null &&
    _PRICE_RMB_PER_1K_TOKEN[catalogId] == null
  );
}

// ── BL-AUDIT-UX-P1 (5/17 鸿波): 精算 RMB 成本 ───────────────────
//
// 老 audit 页 P0 用了"统一 0.02¥/1K tokens" 粗估, 不分模型. 这里按各 model 厂商
// 公开价目表精算 (input + output 平均):
//
//   - DeepSeek Flash:   ¥0.001 / 1K (in) · ¥0.002 / 1K (out)  → 均 0.0015
//   - Qwen Flash:        ¥0.0003 / 1K (in) · ¥0.0006 / 1K (out) → 均 0.00045
//   - NVIDIA Nemotron:   $0.20 / 1M (in)   · $0.60 / 1M (out)   → 均 ¥0.0028 (汇率 7)
//   - NVIDIA Llama:      $0.18 / 1M        · $0.20 / 1M         → 均 ¥0.0013
//   - Gemini Flash:      $0.075 / 1M       · $0.30 / 1M         → 均 ¥0.00067
//   - Gemini Pro:        $1.25 / 1M        · $5 / 1M            → 均 ¥0.0218
//   - 私有 (自建 GPU 摊算): ¥0.0001 / 1K (粗估, 电费 + 硬件折旧)
//
// 注: in/out 比例假设 5:1 (典型 chat workload). 真要按 actual usage_in/out 拆,
// backend 要返 model × tokens_in × tokens_out 分别给前端, P2 再做.

const _PRICE_RMB_PER_1K_TOKEN: Record<string, number> = {
  "catfish-public-deepseek-flash": 0.0015,
  "catfish-public-qwen-flash": 0.00045,
  "catfish-public-nvidia-nemotron": 0.0028,
  "catfish-public-nvidia-llama": 0.0013,
  "catfish-public-gemini-flash": 0.00067,
  "catfish-public-gemini-pro": 0.0218,
  "catfish-private-main": 0.0001,
  "catfish-private-vision": 0.0001,
  "catfish-private-embed": 0.00005,
};

/** 兜底单价. 只在配置和硬编码表都没有时用 —— 配合 isCostEstimated() 提示。
 *
 * 注意它跟真实单价的差距很大 (0.00005 ~ 0.0218, 差 400 倍), 所以走到兜底
 * 时算出来的数**不该当准确值展示**。 */
const _FALLBACK_PRICE = 0.001;

/** 按模型精算: 输入 model → token 数 → RMB.
 *
 * 优先级: 运行时配置 (可在 /admin/models 界面上改) > 硬编码表 (历史模型
 * 兜底) > _FALLBACK_PRICE。见上面 setRuntimeModelMeta 的说明。
 */
export function costRMB(catalogId: string, tokens: number): number {
  const price =
    _runtime[catalogId]?.price_per_1k_tokens ??
    _PRICE_RMB_PER_1K_TOKEN[catalogId] ??
    _FALLBACK_PRICE;
  return (tokens / 1000) * price;
}

/** 多 model 加权总 RMB. 输入 by_model 列表, 返总成本. */
export function totalCostRMB(
  byModel: { model: string; total_tokens: number }[],
): number {
  return byModel.reduce(
    (sum, m) => sum + costRMB(m.model, m.total_tokens),
    0,
  );
}

/** 格式化 RMB: < ¥1 → 2 位小数, < ¥100 → 1 位, >= ¥100 → 整数 + 千分位 */
export function fmtRMB(rmb: number): string {
  if (rmb < 0.01) return "≈ ¥0.00";
  if (rmb < 1) return `≈ ¥${rmb.toFixed(2)}`;
  if (rmb < 100) return `≈ ¥${rmb.toFixed(1)}`;
  return `≈ ¥${Math.round(rmb).toLocaleString()}`;
}
