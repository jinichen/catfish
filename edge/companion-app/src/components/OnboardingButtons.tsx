/** Onboarding 每一步底部的按钮条 —— 下一步 / 上一步 / 稍后。
 *
 * 8/15 从 OnboardingWizard.tsx 搬出来 (979 行, 过了 CLAUDE.md §1 的 800 红线)。
 *
 * 单独一个文件是因为**八个 Step 都用它**, 放在任何一个 step 文件里都会让另一个
 * step 文件去 import 一个名字叫 "XxxSteps" 的东西拿按钮, 读起来别扭。
 *
 * # 「稍后」不是装饰
 *
 * 每一步都能跳过, 跳过就写 onboarded=true 不再显。这是有意的 ——
 * 员工第一次打开一个装在公司电脑上的 AI, 最不该有的体验是"必须答完八个问题
 * 才让用"。所以 onSkip 在每一步都在, 别为了"引导完成率"把它去掉。
 */

export function Buttons({
  onNext,
  onBack,
  onSkip,
  nextLabel = "下一步 →",
  skipLabel = "稍后再说",
  nextDisabled = false,
}: {
  onNext: () => void;
  onBack?: () => void;
  onSkip: () => void;
  nextLabel?: string;
  /** BL-ONBOARDING-DOC-DIRS-STEP (6/1): 默认"稍后再说", 加 skipLabel 让 StepDocDirs
   *  显"跳过 (用默认)" 更准确表达员工选择. */
  skipLabel?: string;
  /** 7/15 BL-ONBOARDING-SERVER-STEP: 加 nextDisabled 让 StepServerConfig 保存中
   *  时禁用 "下一步" 按钮, 防重复点击. */
  nextDisabled?: boolean;
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
        {skipLabel}
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
          disabled={nextDisabled}
          style={{
            padding: "8px 20px",
            background: nextDisabled ? "var(--catfish-border)" : "var(--catfish-cyan)",
            color: "white",
            border: "none",
            borderRadius: 4,
            cursor: nextDisabled ? "not-allowed" : "pointer",
            fontSize: 13,
            fontWeight: 500,
            opacity: nextDisabled ? 0.6 : 1,
          }}
        >
          {nextLabel}
        </button>
      </div>
    </div>
  );
}
