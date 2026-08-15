/** Onboarding 第 1–4 步 —— 认识鲶鱼 / 服务器地址 / 起名 / 登录。
 *
 * 8/15 从 OnboardingWizard.tsx 搬出来。这四步的共同点是**都在解决"连不连得上"**,
 * 连不上后面四步一步都走不了。
 *
 * # Step 2「服务器地址」为什么在这么靠前的位置 (7/15 BL-ONBOARDING-SERVER-STEP)
 *
 * 客户端首次启动时默认 URL 是 http://127.0.0.1:8999。达华这类把中央部署到远程
 * 服务器 (IP 或域名) 的客户, 员工一上来登录就挂 —— 而挂的样子是"登录失败",
 * 看不出是地址不对。
 *
 * 所以这一步排在登录**之前**: 先让员工自己把地址填对, 不必等 IT 去预配
 * ~/.catfish/companion.yaml。URL 校验只要求 http:// 或 https:// 开头,
 * IP / 域名 / 带端口都放行 (跟 Dashboard 的 ServerConfigCard 一致 —— 两处
 * 校验规则要是飘了, 就会出现"仪表盘填得进、引导填不进"这种莫名其妙的差异)。
 *
 * # Step 3「起名」是产品主张不是彩蛋 (BL-E11)
 *
 * 员工给鲶鱼起名 + 选人设。命名权在员工手里, 这是"这是我的助手"而不是
 * "公司装的监控"的第一个信号。别改成默认名直接跳过。
 */
import { useEffect, useState } from "react";

import { readServerConfig, writeServerConfig } from "../lib/tauri";
import { useAgentStore } from "../store/agent";
import { PERSONALITY_LABELS, type Personality } from "../lib/agent";

import { Buttons } from "./OnboardingButtons";

// ── 5 个 step (5/3 晚 BL-E11 加"起名"在第 2 步) ─────────────────


export function StepWelcome({ onNext, onSkip }: { onNext: () => void; onSkip: () => void }) {
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
export function StepServerConfig({
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
export function StepName({
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


export function StepAuth({
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
