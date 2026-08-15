/** Onboarding 引导 — 员工首次启动 Companion 走这个 (五一 sprint 5/2 收尾 BL-F3 MVP).
 *
 * 8 步 (7/15 BL-ONBOARDING-SERVER-STEP 加"服务器地址"在第 2 步, IP/域名任意填):
 *   1. Welcome (鲶鱼是啥, 一句话)
 *   2. ★ 服务器地址 (Gateway + Identity URL, 支持 IP/域名, BL-ONBOARDING-SERVER-STEP)
 *   3. ★ 命名权 + 选人设 (员工给鲶鱼起名 + 3 档人设, BL-E11)
 *   4. 鉴权 (SSO 登录 / dev_token 兜底)
 *   5. 选默认模型
 *   6. Curator consent toggle
 *   7. 文书目录 (BL-ONBOARDING-DOC-DIRS-STEP)
 *   8. 试聊一句 + 完成
 *
 * 触发: localStorage["catfish:onboarded"] !== "true" → 首次显示
 * 跳过: 任何步骤都能 "稍后" 跳过, 写 onboarded=true 不再显
 *
 * BL-ONBOARDING-SERVER-STEP (7/15) 加 Step 2 服务器地址:
 *   - 原因: 客户端首次启动前默认 URL=http://127.0.0.1:8999, 达华等中央部署到远程
 *     服务器 (IP 或域名) 时员工登录挂. 加此步让员工先设 URL 再登录, 无需 IT
 *     预配 ~/.catfish/companion.yaml.
 *   - URL 校验: 只要求 http:// 或 https:// 开头, IP/域名/带端口都支持 (跟
 *     Dashboard ServerConfigCard 逻辑一致).
 *   - 写入: 调 writeServerConfig() Tauri command → ~/.catfish/companion.yaml
 *     的 endpoints.gateway_url + oidc.issuer.
 */

import { useEffect, useState } from "react";

import { fetchCatalog } from "../lib/tauri";
import { useUIStore } from "../store/ui";
// 8/15: OnboardingWizard.tsx 原本 979 行, 过了 CLAUDE.md §1 的 800 红线。
// 下面三块是从本文件搬出去的**同一批组件**, 不是新东西:
//   · StepsConnect  1–4 步, 解决"连不连得上" (欢迎 / 服务器地址 / 起名 / 登录)
//   · StepsSetup    5–8 步, 配置"鲶鱼替你干什么" (模型 / 脚本整理 / 文书目录 / 试聊)
//   · Buttons       每一步底部的按钮条, 八步共用
//
// 依赖方向单向: wizard → steps → buttons, 没有回边。ModelInfo 的类型定义也
// 跟着 StepModel 走了, 这里 import 回来 —— 定义放在用它最深的地方。
import {
  StepAuth,
  StepName,
  StepServerConfig,
  StepWelcome,
} from "./OnboardingStepsConnect";
import {
  type ModelInfo,
  StepCurator,
  StepDocDirs,
  StepModel,
  StepTryChat,
} from "./OnboardingStepsSetup";

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
        {/* 步骤指示 (5/3 晚 BL-E11: 4→5, 6/1 BL-ONBOARDING-DOC-DIRS-STEP: 6→7,
            7/15 BL-ONBOARDING-SERVER-STEP: 7→8 加服务器地址) */}
        <div
          style={{
            display: "flex",
            gap: 6,
            marginBottom: "var(--space-4)",
            justifyContent: "center",
          }}
        >
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <div
              key={i}
              style={{
                width: 28,
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
        {/* 7/15 BL-ONBOARDING-SERVER-STEP: 加服务器地址 step, 员工首次启动就设
            gateway + identity URL, 无需 IT 预配 companion.yaml. 支持 IP/域名. */}
        {step === 1 && <StepServerConfig onNext={next} onBack={back} onSkip={close} />}
        {step === 2 && <StepName onNext={next} onBack={back} onSkip={close} />}
        {step === 3 && <StepAuth onNext={next} onBack={back} onSkip={close} />}
        {step === 4 && (
          <StepModel models={models} onNext={next} onBack={back} onSkip={close} />
        )}
        {/* 5/7 BL-CR: Curator consent toggle */}
        {step === 5 && <StepCurator onNext={next} onBack={back} onSkip={close} />}
        {/* 6/1 BL-ONBOARDING-DOC-DIRS-STEP: 加文书目录, 解决默认 ~/Documents/work
            命中率低问题 — 50 员工 mac 上各种自定义目录 (~/项目/ / ~/文稿/ / ~/work/),
            不让员工指目录则文书风格永远 0 文档. */}
        {step === 6 && <StepDocDirs onNext={next} onBack={back} onSkip={close} />}
        {step === 7 && (
          <StepTryChat
            onFinish={(seedMessage) => {
              // BL-MEMORY-ONBOARDING-SEED (5/16): seedMessage 来自 StepTryChat 4 选 1
              // (3 个教 memory 的引导句 + 1 个空话). 让新员工第一聊就 seed 基础 entries.
              close();
              startProactiveChat(seedMessage);
            }}
            onSkip={close}
            onBack={back}
          />
        )}
      </div>
    </div>
  );
}

