/** Onboarding 引导 — 员工首次启动 Companion 走这个 (五一 sprint 5/2 收尾 BL-F3 MVP).
 *
 * 4 步:
 *   1. Welcome (鲶鱼是啥, 一句话)
 *   2. 鉴权 (SSO 登录 / dev_token 兜底)
 *   3. 选默认模型
 *   4. 试聊一句 + 完成
 *
 * 触发: localStorage["catfish:onboarded"] !== "true" → 首次显示
 * 跳过: 任何步骤都能 "稍后" 跳过, 写 onboarded=true 不再显
 *
 * 完整 BL-F3 (动画 / 多语言 / 真试聊 / 跟 SSO flow 集成) 留下次, 这是 MVP.
 */

import { useEffect, useState } from "react";

import { fetchCatalog } from "../lib/tauri";
import { useUIStore } from "../store/ui";

const _STORAGE_KEY = "catfish:onboarded";

function isOnboarded(): boolean {
  try {
    return localStorage.getItem(_STORAGE_KEY) === "true";
  } catch {
    return true;
  }
}

function markOnboarded(): void {
  try {
    localStorage.setItem(_STORAGE_KEY, "true");
  } catch {
    /* ignore */
  }
}

interface ModelInfo {
  id: string;
  name?: string;
  tier?: string;
}

export default function OnboardingWizard() {
  const [step, setStep] = useState(0);
  const [visible, setVisible] = useState(!isOnboarded());
  const [models, setModels] = useState<ModelInfo[]>([]);
  const startProactiveChat = useUIStore((s) => s.startProactiveChat);

  // 拉模型列表给 step 3 选默认模型
  useEffect(() => {
    if (!visible) return;
    void (async () => {
      try {
        const catalog = await fetchCatalog();
        const modelsArr = (catalog as { data?: ModelInfo[]; models?: ModelInfo[] }).data
          || (catalog as { models?: ModelInfo[] }).models
          || [];
        setModels(Array.isArray(modelsArr) ? modelsArr : []);
      } catch {
        setModels([]);
      }
    })();
  }, [visible]);

  if (!visible) return null;

  const close = () => {
    markOnboarded();
    setVisible(false);
  };

  const next = () => setStep((s) => s + 1);
  const back = () => setStep((s) => Math.max(0, s - 1));

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.7)",
        zIndex: 10_000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      role="dialog"
      aria-modal="true"
    >
      <div
        style={{
          background: "var(--catfish-bg-elevated)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 12,
          padding: "var(--space-6)",
          width: 540,
          maxWidth: "90vw",
          maxHeight: "85vh",
          overflow: "auto",
          color: "var(--catfish-text)",
        }}
      >
        {/* 步骤指示 */}
        <div
          style={{
            display: "flex",
            gap: 6,
            marginBottom: "var(--space-4)",
            justifyContent: "center",
          }}
        >
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              style={{
                width: 32,
                height: 4,
                borderRadius: 2,
                background:
                  i <= step
                    ? "var(--catfish-cyan)"
                    : "var(--catfish-border)",
              }}
            />
          ))}
        </div>

        {step === 0 && <StepWelcome onNext={next} onSkip={close} />}
        {step === 1 && <StepAuth onNext={next} onBack={back} onSkip={close} />}
        {step === 2 && (
          <StepModel models={models} onNext={next} onBack={back} onSkip={close} />
        )}
        {step === 3 && (
          <StepTryChat
            onFinish={() => {
              close();
              startProactiveChat("早, 这是我的第一句. 你能记住么?");
            }}
            onSkip={close}
            onBack={back}
          />
        )}
      </div>
    </div>
  );
}

// ── 4 个 step ──────────────────────────────────────────────────


function StepWelcome({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
  return (
    <>
      {/* 五一 sprint 5/3 BL-D11: 60px 🐟 → 96px 正式吉祥物, brand 一致 */}
      <div style={{ textAlign: "center", marginBottom: 12 }}>
        <img src="/catfish-mascot.svg" alt="小鲶" width={96} height={96} style={{ display: "inline-block" }} />
      </div>
      <h2 style={{ textAlign: "center", margin: "0 0 var(--space-3) 0" }}>
        欢迎使用 鲶鱼 Companion
      </h2>
      <p style={{ fontSize: 14, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        鲶鱼是你的 AI 副手 — 跟同事一样**记得你**, 帮你写公文 / 看邮件 / 操作内网系统.
      </p>
      <ul
        style={{
          fontSize: 13,
          lineHeight: 1.8,
          color: "var(--catfish-text-muted)",
          paddingLeft: 20,
        }}
      >
        <li>📝 写汇报 / 周报 / 立项材料 — 一句话生成 .docx</li>
        <li>💾 跨 session 记忆 — 它记得你最近做了什么</li>
        <li>🔐 数据全在你本机, 中央只看用量不看内容</li>
        {/* 🐠 是观赏鱼 emoji, 跟"鲶鱼"品牌容易混淆 → 换中性 ✨ */}
        <li>✨ 越用越懂你, journal 自动总结你的工作</li>
      </ul>
      <p style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginTop: "var(--space-3)" }}>
        4 步设置, 大概 1 分钟.
      </p>
      <Buttons onNext={onNext} nextLabel="开始 →" onSkip={onSkip} />
    </>
  );
}


function StepAuth({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  return (
    <>
      <h3 style={{ marginTop: 0 }}>1️⃣ 登录</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        鲶鱼支持你公司的 SSO (飞书 / 钉钉 / 企微 / 自建 OIDC).
      </p>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        如果没配 SSO, 默认用 dev token 模式 (顶部黄条会显示, 提醒 IT 改). 测试 / 个人本机够用.
      </p>
      <div
        style={{
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 6,
          padding: "var(--space-3)",
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginTop: "var(--space-3)",
        }}
      >
        ℹ️ 后续可在 设置 → 登录 切换. 现在直接走默认就行, gateway 自动用 .env dev_token.
      </div>
      <Buttons onNext={onNext} onBack={onBack} onSkip={onSkip} />
    </>
  );
}


function StepModel({
  models,
  onNext,
  onBack,
  onSkip,
}: {
  models: ModelInfo[];
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  return (
    <>
      <h3 style={{ marginTop: 0 }}>2️⃣ 选默认模型</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        鲶鱼支持多模型, 每次对话都能切. 我们建议默认走**内网模型** (数据不出公司).
      </p>
      {models.length === 0 ? (
        <div
          style={{
            background: "var(--catfish-bg)",
            border: "1px dashed var(--catfish-border)",
            padding: "var(--space-3)",
            borderRadius: 6,
            fontSize: 12,
            color: "var(--catfish-text-muted)",
          }}
        >
          模型列表加载中... 如果一直空, 说明 gateway 还没起或没配 catalog. 后续再来设置.
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 6,
            marginTop: "var(--space-2)",
            maxHeight: 200,
            overflow: "auto",
          }}
        >
          {models.map((m) => (
            <div
              key={m.id}
              style={{
                padding: "var(--space-2) var(--space-3)",
                background: "var(--catfish-bg)",
                border: "1px solid var(--catfish-border)",
                borderRadius: 4,
                fontSize: 12,
                fontFamily: "var(--font-mono)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{m.id}</span>
              {m.tier === "private" && (
                <span
                  style={{
                    fontSize: 10,
                    color: "var(--catfish-cyan)",
                    border: "1px solid var(--catfish-cyan)",
                    padding: "1px 6px",
                    borderRadius: 3,
                  }}
                >
                  内网
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: "var(--space-3)" }}>
        默认模型在 仪表盘 → 身份卡 / 对话 tab 顶部下拉切.
      </p>
      <Buttons onNext={onNext} onBack={onBack} onSkip={onSkip} />
    </>
  );
}


function StepTryChat({
  onFinish,
  onBack,
  onSkip,
}: {
  onFinish: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  return (
    <>
      <h3 style={{ marginTop: 0 }}>3️⃣ 试聊一句</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        最后一步: 跟鲶鱼说一句, 看看效果.
      </p>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        点 "开聊" 我会帮你打开对话 tab 并预填一句话, 你按 Enter 发就行.
      </p>
      <div
        style={{
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 6,
          padding: "var(--space-3)",
          fontSize: 13,
          fontFamily: "var(--font-mono)",
          color: "var(--catfish-cyan)",
          marginTop: "var(--space-3)",
        }}
      >
        💬 早, 这是我的第一句. 你能记住么?
      </div>
      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: "var(--space-3)" }}>
        以后你每次开 Companion, 仪表盘第一卡 "📝 今日话题" 会主动给你起话题, 不用想说啥.
      </p>
      <Buttons onNext={onFinish} nextLabel="开聊 →" onBack={onBack} onSkip={onSkip} />
    </>
  );
}


function Buttons({
  onNext,
  onBack,
  onSkip,
  nextLabel = "下一步 →",
}: {
  onNext: () => void;
  onBack?: () => void;
  onSkip: () => void;
  nextLabel?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginTop: "var(--space-5)",
        gap: "var(--space-3)",
      }}
    >
      <button
        onClick={onSkip}
        style={{
          background: "transparent",
          border: "none",
          color: "var(--catfish-text-muted)",
          fontSize: 12,
          cursor: "pointer",
          padding: 4,
        }}
      >
        稍后再说
      </button>
      <div style={{ display: "flex", gap: "var(--space-2)" }}>
        {onBack && (
          <button
            onClick={onBack}
            style={{
              padding: "8px 16px",
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              cursor: "pointer",
              color: "var(--catfish-text)",
              fontSize: 13,
            }}
          >
            ← 返回
          </button>
        )}
        <button
          onClick={onNext}
          style={{
            padding: "8px 20px",
            background: "var(--catfish-cyan)",
            color: "white",
            border: "none",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          {nextLabel}
        </button>
      </div>
    </div>
  );
}
