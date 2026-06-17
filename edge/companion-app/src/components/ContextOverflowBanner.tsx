/** P3.5.17 (6/17 鸿波) — context 上下文占用顶部横条 (信息流, 不是操作流).
 *
 * # 为啥是信息流
 *
 * P3.5.17.b 修了 hermes 自带 ContextCompressor auth (catfish-gateway auth fallback
 * 让 hermes-cli auxiliary 缺 X-Catfish-User 不报 400) — 现在 hermes 下次发送时
 * 会**自动 preflight 压缩** (config.yaml threshold=0.5, prompt > 50% × ctx_window
 * 触发). 员工**不需要手动** Cmd+N / 切大模型, 等 hermes 自己压就行.
 *
 * 老 banner v1 (6/17 早上版本) 提示 "立刻 Cmd+N / 切 catfish-public-gemini-pro"
 * 是 5/13 时代话术 — 那时候 hermes 自动压缩 broken, 只能员工手动 escape.
 * 鸿波看 banner 反馈"自动压缩, 提示这个奇怪", 对的, **跟新世界观矛盾**. 改.
 *
 * # 显示规则 v2 (信息流)
 *
 *   pct < 80%   → 不显 (常态, 不打扰)
 *   pct ≥ 80%   → 黄色 ℹ "下次发送时 hermes 会自动压缩, 不用动"
 *                  (信息预告, 让员工知道压缩在即, 不慌不动手)
 *   pct ≥ 100%  → 红色 🚨 "已超上限 — 自动压缩本应已触发. 没生效则检查
 *                  ~/.hermes/logs/agent.log 看 'context compression' 行,
 *                  或手动 /compress"
 *                  (诊断方向, 不再喊员工 Cmd+N — 那是放弃自动压缩)
 *
 * 95% 那档跟 80% 合并 — 既然交给 hermes, 80% / 95% / 100% 之前都是 hermes
 * 自己负责, 员工不用做 anything. 信号噪声合并.
 *
 * # 跟 ContextCounter 共存
 *
 * 不删 ContextCounter — 状态栏小标签**常态可见**显具体数字. Banner 只在 80%+
 * 时 pop 顶部告诉员工 "hermes 在准备/已经在压了". 两个互补, 一个看数据一个看
 * 状态.
 *
 * # 数据来源
 *
 * useChatStore.lastPromptTokens (useChat.onDone 写) + useCatalog 当前 model
 * context_window. 切会话 lastPromptTokens=null, banner 自动消失.
 *
 * # 红线没变
 *
 * UI 不**自动切模型**, 不提示员工切 (5/15 BL-FALLBACK-CAP-SCOPE — private 不
 * 静默飞 public). 压缩在 private 之内做 (hermes 自带 compressor 不出端).
 */

import { useChatStore } from "../store/chat";
import { useCatalog } from "../hooks/useCatalog";

const BANNER_BASE: React.CSSProperties = {
  borderBottom: "1px solid",
  fontSize: 12,
  padding: "6px 16px",
  textAlign: "center" as const,
};

export default function ContextOverflowBanner() {
  const lastPromptTokens = useChatStore((s) => s.lastPromptTokens);
  const model = useChatStore((s) => s.model);
  const { catalog } = useCatalog();

  if (lastPromptTokens == null) return null;

  const modelInfo = catalog?.models.find((m) => m.id === model);
  const contextWindow = modelInfo?.context_window ?? 0;
  if (contextWindow <= 0) return null;

  const pct = (lastPromptTokens / contextWindow) * 100;

  // < 80% → 不显, 不打扰员工 (ContextCounter 状态栏角落仍显)
  if (pct < 80) return null;

  // ≥ 100% → 红色: 已超上限, 自动压缩本应已触发, 没生效给诊断方向
  if (pct >= 100) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(220, 38, 38, 0.18)",
          borderBottomColor: "rgba(220, 38, 38, 0.45)",
          color: "#dc2626",
          fontWeight: 600,
        }}
      >
        🚨 当前 prompt {Math.round(lastPromptTokens / 1000)}K / {Math.round(contextWindow / 1000)}K
        ({pct.toFixed(0)}%) 已超上限 — hermes 自动压缩本应已触发. 没生效则查
        {" "}<code style={{ background: "rgba(0,0,0,0.06)", padding: "0 4px", borderRadius: 3 }}>
          ~/.hermes/logs/agent.log
        </code> 看 'context compression' 行, 或手动 <strong>/compress</strong>.
      </div>
    );
  }

  // 80-100% → 黄色: 信息预告 hermes 自动压缩在即
  return (
    <div
      style={{
        ...BANNER_BASE,
        background: "rgba(217, 119, 6, 0.12)",
        borderBottomColor: "rgba(217, 119, 6, 0.35)",
        color: "#d97706",
      }}
    >
      ℹ prompt 已用 {pct.toFixed(0)}% ({Math.round(lastPromptTokens / 1000)}K /
      {" "}{Math.round(contextWindow / 1000)}K) — 下次发消息时 hermes 会自动压缩, 不用动.
    </div>
  );
}
