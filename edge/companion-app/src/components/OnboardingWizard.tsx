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
import { invoke } from "@tauri-apps/api/core";

import { fetchCatalog, readServerConfig, writeServerConfig } from "../lib/tauri";
import { useUIStore } from "../store/ui";
import { useAgentStore } from "../store/agent";
import { PERSONALITY_LABELS, type Personality } from "../lib/agent";

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

// ── 5 个 step (5/3 晚 BL-E11 加"起名"在第 2 步) ─────────────────


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
        鲶鱼是你的 AI 副手 — 跟同事一样<strong style={{ color: "var(--catfish-text)" }}>记得你</strong>, 帮你写公文 / 看邮件 / 操作内网系统.
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
        几步简单设置, 大概 2 分钟.
      </p>
      <Buttons onNext={onNext} nextLabel="开始 →" onSkip={onSkip} />
    </>
  );
}

// 7/15 BL-ONBOARDING-SERVER-STEP — 服务器地址 (员工首次启动填, IP/域名任意)
//
// 沙箱铁证依赖:
//   - readServerConfig() / writeServerConfig() Tauri commands 已就绪
//   - 写入 ~/.catfish/companion.yaml 的 endpoints.gateway_url + oidc.issuer
//   - URL 校验只要求 http/https 开头 (跟 ServerConfigCard 一致)
function StepServerConfig({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  const [gatewayUrl, setGatewayUrl] = useState("http://127.0.0.1:8999");
  const [identityUrl, setIdentityUrl] = useState("http://127.0.0.1:8998");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 首次加载, 读当前配置作 default (若员工重开 onboarding 或已有 yaml)
  useEffect(() => {
    let alive = true;
    readServerConfig()
      .then((cfg) => {
        if (!alive) return;
        if (cfg.gateway_url) setGatewayUrl(cfg.gateway_url);
        if (cfg.identity_url) setIdentityUrl(cfg.identity_url);
      })
      .catch(() => {
        /* 首次启动无 yaml, 用 default 127.0.0.1 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const handleSaveAndNext = async () => {
    setSaving(true);
    setErr(null);
    try {
      const gw = gatewayUrl.trim().replace(/\/+$/, "");
      const id = identityUrl.trim().replace(/\/+$/, "");
      if (!/^https?:\/\//.test(gw)) {
        throw new Error("Gateway URL 必须 http:// 或 https:// 开头");
      }
      if (id && !/^https?:\/\//.test(id)) {
        throw new Error("Identity URL 必须 http:// 或 https:// 开头");
      }
      // gateway_token 保持空 ("") — 走 SSO 后自动填, 不需要员工手输
      await writeServerConfig(gw, "", id || undefined);
      onNext();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const inputStyle = {
    width: "100%",
    padding: "10px 12px",
    fontSize: 14,
    border: "1px solid var(--catfish-border)",
    borderRadius: "var(--radius-md)",
    background: "var(--catfish-bg)",
    color: "var(--catfish-text)",
    boxSizing: "border-box" as const,
    marginBottom: "var(--space-3)",
    fontFamily: "monospace",
  };

  const labelStyle = {
    display: "block",
    fontSize: 12,
    color: "var(--catfish-text-muted)",
    marginBottom: 4,
  };

  return (
    <>
      <h2 style={{ textAlign: "center", margin: "0 0 var(--space-3) 0" }}>
        连接你公司的服务器
      </h2>
      <p
        style={{
          fontSize: 13,
          lineHeight: 1.6,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-4)",
        }}
      >
        鲶鱼需要连接你公司部署的中央服务. 请找 IT 拿服务器地址填在下面.
        <br />
        <strong>本机试用</strong>可保持默认 <code>127.0.0.1</code>. <strong>远程部署</strong>
        填 IP (例: <code>http://192.168.10.20:8999</code>) 或域名
        (例: <code>https://catfish.company.com</code>).
      </p>

      <label style={labelStyle}>
        Gateway URL <span style={{ color: "var(--catfish-text-muted)" }}>(员工聊天走这, 通常端口 8999)</span>
      </label>
      <input
        type="text"
        value={gatewayUrl}
        onChange={(e) => setGatewayUrl(e.target.value)}
        placeholder="http://192.168.10.20:8999 或 https://catfish.company.com"
        style={inputStyle}
        spellCheck={false}
        autoCapitalize="off"
      />

      <label style={labelStyle}>
        Identity URL <span style={{ color: "var(--catfish-text-muted)" }}>(SSO 登录走这, 通常端口 8998)</span>
      </label>
      <input
        type="text"
        value={identityUrl}
        onChange={(e) => setIdentityUrl(e.target.value)}
        placeholder="http://192.168.10.20:8998 或 https://catfish.company.com"
        style={inputStyle}
        spellCheck={false}
        autoCapitalize="off"
      />

      {err && (
        <div
          style={{
            color: "var(--catfish-danger, #ef4444)",
            fontSize: 12,
            marginBottom: "var(--space-3)",
            padding: "8px 10px",
            background: "rgba(239, 68, 68, 0.08)",
            borderRadius: 6,
          }}
        >
          {err}
        </div>
      )}

      <div
        style={{
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 6,
          padding: "var(--space-3)",
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-3)",
        }}
      >
        ℹ️ 之后可在 <strong>Dashboard → 服务器配置</strong> 卡片里修改. 测试期先用 IP, 正式上线可切域名.
      </div>

      <Buttons
        onBack={onBack}
        onNext={handleSaveAndNext}
        nextLabel={saving ? "保存中..." : "保存并继续"}
        nextDisabled={saving}
        onSkip={onSkip}
      />
    </>
  );
}

// BL-E11 命名权 (五一 sprint 5/3 晚) — Onboarding Step 2 (新)
function StepName({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  const currentName = useAgentStore((s) => s.name);
  const currentPersonality = useAgentStore((s) => s.personality);
  const updateAgentPrefs = useAgentStore((s) => s.updateAgentPrefs);
  const [name, setName] = useState(currentName);
  const [personality, setPersonality] = useState<Personality>(currentPersonality);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleSaveAndNext = async () => {
    setSaving(true);
    setErr(null);
    try {
      await updateAgentPrefs(name.trim() || "小鲶", personality);
      onNext();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <h2 style={{ textAlign: "center", margin: "0 0 var(--space-3) 0" }}>
        给我起个名字
      </h2>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)", marginBottom: "var(--space-4)" }}>
        默认叫"小鲶". 你想叫"老李 / 阿强 / Penny / 数智小李 / 什么都行". 之后聊天 / 通知 / Dashboard 都用这名字 — 让我成为<strong>你的</strong>同事.
      </p>

      <label style={{ display: "block", fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
        名字
      </label>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={32}
        placeholder="小鲶"
        style={{
          width: "100%",
          padding: "10px 12px",
          fontSize: 14,
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-md)",
          background: "var(--catfish-bg)",
          color: "var(--catfish-text)",
          boxSizing: "border-box",
          marginBottom: "var(--space-4)",
        }}
      />

      <label style={{ display: "block", fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: 6 }}>
        说话风格
      </label>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(Object.keys(PERSONALITY_LABELS) as Personality[]).map((p) => {
          const meta = PERSONALITY_LABELS[p];
          const selected = personality === p;
          return (
            <button
              key={p}
              type="button"
              onClick={() => setPersonality(p)}
              style={{
                textAlign: "left",
                padding: "10px 12px",
                border: `1.5px solid ${selected ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
                borderRadius: "var(--radius-md)",
                background: selected ? "var(--catfish-bg-cream)" : "var(--catfish-bg)",
                cursor: "pointer",
                color: "var(--catfish-text)",
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>
                {meta.label}
              </div>
              <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", lineHeight: 1.5 }}>
                {meta.desc}
              </div>
            </button>
          );
        })}
      </div>

      {err && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginTop: 8 }}>
          保存失败: {err}
        </div>
      )}

      <Buttons
        onNext={handleSaveAndNext}
        onBack={onBack}
        onSkip={onSkip}
        nextLabel={saving ? "保存中…" : "保存 →"}
      />
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


// BL-CR Curator 集成 (5/7) — Onboarding step 5 (5 of 6)
//
// 给员工一个明确的 "让小鲶定期帮我整理脚本" 开关. 默认勾.
// 不勾 → 写 ~/.hermes/config.yaml 的 curator.enabled: false.
//
// 跟"自动归档"的恐慌情绪对冲: 重点强调
//   - 永不真删 (archive 是搬到不可见, 不是 rm)
//   - 鲶鱼自带的 skill 不在范围内
//   - 你随时能在仪表盘 → 脚本整理 卡片关掉
function StepCurator({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 进 step 时读现有配置 (员工可能之前已经设过)
  useEffect(() => {
    void (async () => {
      try {
        const cfg = await invoke<{ enabled: boolean }>("get_curator_config");
        setEnabled(cfg.enabled);
      } catch {
        // 没读到无所谓, 默认勾
      }
    })();
  }, []);

  const handleNext = async () => {
    setSaving(true);
    setError(null);
    try {
      // 把当前选择写进 ~/.hermes/config.yaml
      // (其他 4 个参数走我们保守默认, 跟 ensure_default 一致)
      await invoke("set_curator_config", {
        enabled,
        intervalHours: 168,
        minIdleHours: 4,
        staleAfterDays: 60,
        archiveAfterDays: 180,
      });
      onNext();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>3️⃣ 脚本整理</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        鲶鱼可以定期帮你整理工作脚本 — 长时间没用的归档, 长得像的合并.
        <br />
        <strong>永不真删, 都能恢复.</strong> 鲶鱼自带的 skill 不在范围内.
      </p>

      <label
        style={{
          display: "flex",
          alignItems: "flex-start",
          gap: 10,
          padding: "var(--space-3)",
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 6,
          marginTop: "var(--space-3)",
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          style={{ marginTop: 2 }}
        />
        <div>
          <div style={{ fontSize: 13, fontWeight: 500 }}>
            让小鲶定期帮我整理工作脚本 (推荐)
          </div>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4, lineHeight: 1.5 }}>
            · 60 天没用 → 标记"久未使用" (你能看到)
            <br />· 180 天没用 → 归档到不可见 (能恢复)
            <br />· 你 idle 4 小时才开始干, 一周最多跑一次
            <br />· 仪表盘 "脚本整理" 卡随时关
          </div>
        </div>
      </label>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginTop: 8 }}>
          保存失败: {error}
        </div>
      )}

      <Buttons
        onNext={handleNext}
        nextLabel={saving ? "保存中…" : "下一步 →"}
        onBack={onBack}
        onSkip={onSkip}
      />
    </>
  );
}

// ── BL-ONBOARDING-DOC-DIRS-STEP (鸿波 6/1 拍 C1) ─────────────────
// 默认目录命中率低 (中文用户用 ~/文稿/, 程序员 ~/projects/, 国企 ~/项目/
// 各种自定义). 不让员工显式指目录, 文书风格抽不到东西.
// 这步: 让员工手输 1 个目录. 跳过 → 后面在仪表盘 "📂 搜索范围" 卡随时加.
//
// BL-STYLE-FP-USE-INDEX (7/27): 从 style_fingerprint_scan_dirs_add 改成
// local_search_scope_add. 文书风格现在查 local_search 索引, 目录范围唯一
// 由 search-scope.yaml 决定; 写 companion.yaml 那条路已经删了.
// 顺带好处: 这一步同时把目录加进了本地搜索, 员工问 "上次那份合同在哪" 也能搜到.
interface ScopeResult {
  include: string[];
  exclude: string[];
  yamlPath: string;
}

function StepDocDirs({
  onNext,
  onBack,
  onSkip,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  const [path, setPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    const trimmed = path.trim();
    if (!trimmed) {
      setError("请输入目录路径, 或点跳过");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await invoke<ScopeResult>("local_search_scope_add", {
        path: trimmed,
      });
      onNext();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // 后端验证: 目录不存在 / 不是目录 / 已存在 等
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <h3 style={{ marginTop: 0 }}>4️⃣ 学你的文风</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        鲶鱼会给这个文件夹建本地索引, 一来你能直接搜"上次那份合同", 二来它会从中
        抽你的写作特征 (句长 / 用词 / 标点习惯), 帮你起草汇报时模仿你的风格 —
        不上传, 全在你本机.
        <br />
        <strong>你工作文档在哪个文件夹?</strong>
      </p>

      <div
        style={{
          padding: "var(--space-3)",
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 6,
          marginTop: "var(--space-3)",
        }}
      >
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="~/Documents · ~/work · ~/项目 · ~/桌面/资料 ..."
          style={{
            width: "100%",
            padding: "8px 10px",
            fontSize: 13,
            fontFamily: "var(--font-mono, monospace)",
            background: "var(--catfish-bg-elevated)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            color: "var(--catfish-text)",
            boxSizing: "border-box",
          }}
          autoFocus
        />
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 8, lineHeight: 1.5 }}>
          常见路径: <code>~/Documents</code> · <code>~/work</code> · <code>~/项目</code> · <code>~/文稿</code>
          <br />
          后面在仪表盘 "📂 搜索范围" 卡随时改 / 加多个.
        </div>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12, marginTop: 8 }}>
          {error}
        </div>
      )}

      <Buttons
        onNext={handleSave}
        nextLabel={saving ? "保存中…" : "保存 →"}
        onBack={onBack}
        onSkip={onSkip}
        skipLabel="跳过 (用默认)"
      />
    </>
  );
}

function StepTryChat({
  onFinish,
  onBack,
  onSkip,
}: {
  onFinish: (seedMessage: string) => void;
  onBack: () => void;
  onSkip: () => void;
}) {
  // BL-MEMORY-ONBOARDING-SEED (5/16): 改成"教小鲶记基础事实"引导, 让新员工
  // 第一次聊就 seed 几条 memory entries. 比"早, 第一句" 这种空话有用 10x.
  const [picked, setPicked] = useState<string>(
    "我在 [部门] 做 [角色], 项目主要是 [项目]. 记下来.",
  );
  const examples = [
    "我在 [部门] 做 [角色], 项目主要是 [项目]. 记下来.",
    "记下我的偏好: 公文我喜欢段落, 别给我列表.",
    "记下我儿子叫 [名字], 在 [学校 / 单位].",
    "早, 这是我的第一句. 你能记住么?",
  ];
  return (
    <>
      <h3 style={{ marginTop: 0 }}>5️⃣ 试聊 — 教小鲶记住你</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        小鲶有跨 session 长期记忆 (在你本机 <code>~/.hermes/memories/USER.md</code>, 永不上传).
        第一次聊建议**教它几条基础事实**, 今后它就懂你, 不用每次重复.
      </p>
      <p style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginTop: 8 }}>
        选一句, 把 [...] 部分填上你的真实情况, 按 Enter 发. 小鲶会调 memory 工具记下来.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: "var(--space-3)" }}>
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setPicked(ex)}
            style={{
              textAlign: "left",
              padding: "8px 10px",
              border: `1.5px solid ${picked === ex ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
              borderRadius: 6,
              background: picked === ex ? "var(--catfish-bg-cream)" : "var(--catfish-bg)",
              cursor: "pointer",
              color: "var(--catfish-text)",
              fontSize: 12,
              fontFamily: "var(--font-mono)",
            }}
          >
            💬 {ex}
          </button>
        ))}
      </div>
      <p style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: "var(--space-3)" }}>
        发完后仪表盘"鲶鱼对你的认识 → 我的记忆"卡能看到刚记的, 也能随时删.
      </p>
      <Buttons
        onNext={() => onFinish(picked)}
        nextLabel="开聊 →"
        onBack={onBack}
        onSkip={onSkip}
      />
    </>
  );
}


function Buttons({
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
