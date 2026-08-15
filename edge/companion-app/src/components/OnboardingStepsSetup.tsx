/** Onboarding 第 5–8 步 —— 选模型 / 脚本整理开关 / 文书目录 / 试聊一句。
 *
 * 8/15 从 OnboardingWizard.tsx 搬出来。这四步的共同点是**都在配置"鲶鱼替你
 * 干什么"**, 而且每一步都能跳过 —— 跳过只是少一项能力, 不影响能不能用。
 *
 * # ModelInfo 定义在这里
 *
 * OnboardingWizard 也用它 (fetchCatalog 回来的列表), 但类型定义放在**用它最深
 * 的地方**, 让依赖方向保持单向: wizard → steps, 没有回边。
 */
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { Buttons } from "./OnboardingButtons";

/** /v1/models catalog 里的一项。wizard 拉列表, StepModel 渲染。 */
export interface ModelInfo {
  id: string;
  name?: string;
  tier?: string;
}

export function StepModel({
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
export function StepCurator({
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

export function StepDocDirs({
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

export function StepTryChat({
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
