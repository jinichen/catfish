/** RecMode PreviewBanner — 抽自 RecModeButton.tsx (5/20 拆分).
 *
 * 分析完成后显 SKILL.md + main.py 内容 + 3 按钮: 跑试 / 保存 / 重录.
 */

import { useEffect, useState } from "react";

import { useRecModeStore } from "../../../store/recmode";
import {
  getSkillContent,
  saveSkill,
  testSkill,
  type SkillContentResponse,
  type TestSkillResponse,
} from "../../../lib/recmode";

import { ModalShell, T, TabButton } from "./shared";


function PreviewBanner() {
  // Day 3 完整 PreviewModal — 显 SKILL.md + main.py + 三按钮 (跑试 / 保存 / 重录)
  const preview = useRecModeStore((s) => s.preview);
  const reset = useRecModeStore((s) => s.reset);
  const openSetup = useRecModeStore((s) => s.openSetup);
  const setError = useRecModeStore((s) => s.setError);
  const [content, setContent] = useState<SkillContentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"skill" | "code">("skill");
  const [testResult, setTestResult] = useState<TestSkillResponse | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedTo, setSavedTo] = useState<string | null>(null);

  // 拉 SKILL.md + main.py 内容
  useEffect(() => {
    if (!preview) return;
    let cancelled = false;
    setLoading(true);
    getSkillContent(preview.skill_dir)
      .then((c) => { if (!cancelled) setContent(c); })
      .catch((e) => { if (!cancelled) setError(`读 skill 文件失败: ${e.message || e}`); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [preview, setError]);

  if (!preview) return null;

  async function onTest() {
    if (!preview || testing) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testSkill(preview.skill_dir);
      setTestResult(r);
    } catch (e) {
      setTestResult({
        ok: false,
        duration_s: 0,
        skill_path: preview.skill_dir,
        error: (e as Error).message || String(e),
      });
    } finally {
      setTesting(false);
    }
  }

  async function onSave() {
    if (!preview || saving) return;
    setSaving(true);
    try {
      const r = await saveSkill(preview.skill_dir);
      setSavedTo(r.final_dir);
    } catch (e) {
      setError(`保存失败: ${(e as Error).message || e}`);
    } finally {
      setSaving(false);
    }
  }

  function onRerecord() {
    // 重录 — 关 preview, 开 setup (保留之前 setup 数据让用户改名)
    openSetup();
  }

  const confidenceColor =
    preview.confidence >= 0.7 ? "#34c759"
    : preview.confidence >= 0.4 ? "#ff9500"
    : "#ff3b30";

  return (
    <ModalShell onClose={reset}>
      {/* 关闭 X */}
      <button
        onClick={reset}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>

      {/* header — skill 名 + confidence + step count */}
      <div style={{ paddingTop: 4 }}>
        <div style={{
          display: "inline-block",
          padding: "2px 8px",
          fontSize: 11,
          fontWeight: 500,
          color: "white",
          background: "#34c759",
          borderRadius: 6,
          marginBottom: 8,
          letterSpacing: 0.5,
        }}>
          ✓ DRAFT
        </div>
        <h3 style={{
          margin: 0,
          fontSize: 19,
          fontWeight: 600,
          color: T.text,
          letterSpacing: "-0.015em",
          fontFamily: T.systemFont,
        }}>
          {preview.skill_name}
        </h3>
        <div style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          marginTop: 6,
          fontSize: 12,
          color: T.textSecondary,
          fontFamily: T.systemFont,
        }}>
          <span>{preview.namespace}</span>
          <span>·</span>
          <span>{preview.steps_count} 步</span>
          <span>·</span>
          <span>
            自评{" "}
            <span style={{ color: confidenceColor, fontWeight: 600 }}>
              {(preview.confidence * 100).toFixed(0)}%
            </span>
          </span>
        </div>
      </div>

      {/* 待 confirm 问题 (LLM 不确定的点) */}
      {preview.questions_for_user.length > 0 && (
        <div style={{
          marginTop: 14,
          padding: "10px 12px",
          background: "#fff8e1",
          border: "1px solid #ffe082",
          borderRadius: 8,
          fontSize: 12,
          color: "#7c5500",
          fontFamily: T.systemFont,
        }}>
          <strong style={{ display: "block", marginBottom: 4 }}>⚠ 鲶鱼有些不确定的点:</strong>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {preview.questions_for_user.map((q, i) => (
              <li key={i} style={{ marginTop: 2 }}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {/* tabs: SKILL.md / main.py */}
      <div style={{
        marginTop: 18,
        borderBottom: `1px solid ${T.border}`,
        display: "flex",
        gap: 0,
      }}>
        <TabButton active={activeTab === "skill"} onClick={() => setActiveTab("skill")}>
          📄 说明
        </TabButton>
        <TabButton active={activeTab === "code"} onClick={() => setActiveTab("code")}>
          {"</>"} 代码
        </TabButton>
      </div>

      {/* 内容区 — SKILL.md 渲染 / main.py 代码块 */}
      <div style={{
        marginTop: 0,
        height: 240,
        overflowY: "auto",
        padding: "14px 16px",
        background: T.bgWhite,
        border: `1px solid ${T.border}`,
        borderTop: "none",
        borderRadius: "0 0 8px 8px",
        fontSize: 12,
        fontFamily: activeTab === "code" ? "ui-monospace, 'SF Mono', Menlo, monospace" : T.systemFont,
        lineHeight: 1.55,
        color: T.text,
        whiteSpace: "pre-wrap",
      }}>
        {loading ? (
          <span style={{ color: T.textTertiary }}>加载中…</span>
        ) : !content ? (
          <span style={{ color: T.textTertiary }}>读不到 skill 文件</span>
        ) : activeTab === "skill" ? (
          content.skill_md || "(SKILL.md 空)"
        ) : (
          content.main_py || "(main.py 空)"
        )}
      </div>

      {/* 测试结果 (跑一次试 后显) */}
      {testResult && (
        <div style={{
          marginTop: 12,
          padding: "10px 12px",
          background: testResult.ok ? "#e8f7ed" : "#fef0f0",
          border: `1px solid ${testResult.ok ? "#34c759" : T.errorRed}`,
          borderRadius: 8,
          fontSize: 12,
          fontFamily: T.systemFont,
          color: testResult.ok ? "#1e6e2e" : "#a8201a",
        }}>
          <strong>{testResult.ok ? "✅ 跑通" : "❌ 跑挂"}</strong>
          {testResult.duration_s > 0 && <> · {testResult.duration_s.toFixed(1)}s</>}
          {testResult.error && (
            <div style={{ marginTop: 4, fontFamily: "monospace", fontSize: 11 }}>
              {testResult.error.slice(0, 200)}
            </div>
          )}
          {testResult.stdout && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer" }}>stdout</summary>
              <pre style={{ marginTop: 4, fontSize: 11, maxHeight: 100, overflow: "auto" }}>
                {testResult.stdout.slice(0, 1500)}
              </pre>
            </details>
          )}
        </div>
      )}

      {/* 已保存提示 */}
      {savedTo && (
        <div style={{
          marginTop: 12,
          padding: "10px 12px",
          background: "#e8f7ed",
          border: `1px solid #34c759`,
          borderRadius: 8,
          fontSize: 12,
          fontFamily: T.systemFont,
          color: "#1e6e2e",
        }}>
          ✅ 已保存到 <code style={{ fontSize: 11 }}>{savedTo}</code>
        </div>
      )}

      {/* 按钮 bar — 三按钮 + 关 */}
      <div style={{
        display: "flex",
        gap: 8,
        marginTop: 20,
        paddingTop: 16,
        borderTop: `1px solid ${T.border}`,
        alignItems: "center",
      }}>
        <button
          onClick={onRerecord}
          style={{
            padding: "9px 14px",
            border: "none",
            borderRadius: 8,
            background: "transparent",
            color: T.textSecondary,
            fontSize: 13,
            fontFamily: T.systemFont,
            cursor: "pointer",
          }}
        >
          🔄 重录
        </button>
        <div style={{ flex: 1 }} />
        <button
          onClick={onTest}
          disabled={testing}
          style={{
            padding: "9px 14px",
            border: `1px solid ${T.cyan}`,
            borderRadius: 8,
            background: "transparent",
            color: T.cyan,
            fontSize: 13,
            fontWeight: 500,
            fontFamily: T.systemFont,
            cursor: testing ? "default" : "pointer",
          }}
        >
          {testing ? "跑中…" : "🚀 跑一次试"}
        </button>
        <button
          onClick={onSave}
          disabled={saving || !!savedTo}
          style={{
            padding: "9px 18px",
            border: "none",
            borderRadius: 8,
            background: savedTo ? T.textTertiary : T.cyan,
            color: "white",
            fontSize: 13,
            fontWeight: 600,
            fontFamily: T.systemFont,
            cursor: saving || savedTo ? "default" : "pointer",
            boxShadow: savedTo ? "none" : `0 2px 6px ${T.cyan}40`,
          }}
        >
          {saving ? "保存中…" : savedTo ? "✓ 已保存" : "💾 保存到 skills"}
        </button>
      </div>
    </ModalShell>
  );
}

export default PreviewBanner;
