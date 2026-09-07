/** WikiLinkSuggestModal — 🔗 扫描关联建议 modal (P3.5.172 Phase C 7/3 鸿波).
 *
 * 严格背景 (Phase A/B/C.1 audit):
 *   员工新加 concept (e.g. "组织架构") 后严格是 orphan 孤立节点, WikiGraph 只
 *   显 1 节点. Phase C 决策 = AI 增强, 员工点"🔗 扫描关联"→ LLM 扫 body + 现有
 *   wiki title list → 弹本 modal 显匹配建议 → 员工 checkbox 确认 → body 末尾加
 *   "## 关联概念" 段落 (严格员工主权, 不动老 body).
 *
 * UX 一致性: 复用 RecMode ModalShell + T theme tokens (跟 SetupModal / LearnModal
 * 同 macOS Sonoma 风). 严格员工 muscle memory 不破坏.
 *
 * 状态机 (自管, 无独立 zustand store):
 *   - idle       : 加载前 (刚 open)
 *   - loading    : LLM 调用中 (显 spinner)
 *   - loaded     : 拿到 suggestions[] (员工 checkbox 选)
 *   - applying   : wikiUpdateFile 中 (显 button spin)
 *   - error      : LLM / write 失败 (显错 + 重试)
 *   - done       : 应用成功 (显 toast 后 auto close)
 */

import { useState, useEffect } from "react";
import { ModalShell, T } from "../Chat/RecMode/shared";
import { wikiUpdateFile } from "../../lib/tauri";
import {
  suggestWikilinks,
  applyWikilinkSuggestions,
  buildWikiContentWithBody,
  type WikiLinkSuggestion,
} from "../../lib/wikiLinkSuggest";
import { useWikiStore } from "../../store/wiki";

interface Props {
  /** 当前 concept file (从 WikiPreview 传). */
  currentTitle: string;
  currentRelPath: string;
  currentBody: string;
  currentFrontmatter: string;
  /** 员工在聊天页 picker 上选的 model。
   *  7/30: 改成由 WikiPreview 直接读 useChatStore().model 传进来 ——
   *  原来走 picker_state.json (chat 发送时才写的滞后副本) + 写死兜底,
   *  会出现"切了模型但 wiki 还用旧的"。调用方保证非空。 */
  model: string;
  onClose: () => void;
  /** 应用成功后, WikiPreview 侧 reload files + reload body. */
  onApplied: () => void;
}

type ViewState = "idle" | "loading" | "loaded" | "applying" | "error" | "done";

export default function WikiLinkSuggestModal({
  currentTitle,
  currentRelPath,
  currentBody,
  currentFrontmatter,
  model,
  onClose,
  onApplied,
}: Props) {
  const files = useWikiStore((s) => s.files);

  const [view, setView] = useState<ViewState>("idle");
  const [suggestions, setSuggestions] = useState<WikiLinkSuggestion[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [closeHover, setCloseHover] = useState(false);
  const [applyHover, setApplyHover] = useState(false);

  // 严格 auto-load 一次 (open 时立即调 LLM)
  useEffect(() => {
    if (view !== "idle") return;
    setView("loading");
    setError(null);
    void (async () => {
      const result = await suggestWikilinks(
        currentTitle,
        currentBody,
        files,
        model,
      );
      if (!result.ok) {
        setError(result.error || "LLM 调用失败");
        setView("error");
        return;
      }
      setSuggestions(result.suggestions);
      // 严格默认全选 confidence >= 0.7 的 (强匹配)
      const defaultSelected = new Set(
        result.suggestions
          .filter((s) => s.confidence >= 0.7)
          .map((s) => s.title),
      );
      setSelected(defaultSelected);
      setView("loaded");
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSelected(title: string) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });
  }

  async function handleApply() {
    if (selected.size === 0) {
      onClose();
      return;
    }
    setView("applying");
    setError(null);
    try {
      const newBody = applyWikilinkSuggestions(
        currentBody,
        Array.from(selected),
      );
      // 严格 body 无变化 → skip write
      if (newBody === currentBody) {
        setView("done");
        setTimeout(onClose, 800);
        return;
      }
      // wikiUpdateFile 接收完整 Markdown；扫描只改正文，不能丢掉 YAML frontmatter。
      await wikiUpdateFile(
        currentRelPath,
        buildWikiContentWithBody(currentFrontmatter, newBody),
      );
      setView("done");
      onApplied(); // 严格 WikiPreview 侧 reload files (拿新 related edges → 图更新)
      setTimeout(onClose, 800);
    } catch (e) {
      setError((e as Error).message || String(e));
      setView("error");
    }
  }

  function handleRetry() {
    setView("idle"); // 严格重触发 useEffect
  }

  // 严格 confidence 视觉分级
  function confidenceBadge(c: number): { label: string; color: string } {
    if (c >= 0.85) return { label: "强", color: T.cyan };
    if (c >= 0.65) return { label: "中", color: "#F47B3D" }; // 暖橙
    return { label: "弱", color: T.textTertiary };
  }

  return (
    <ModalShell onClose={onClose}>
      {/* 关闭按钮 (类 SetupModal / LearnModal) */}
      <button
        onClick={onClose}
        onMouseEnter={() => setCloseHover(true)}
        onMouseLeave={() => setCloseHover(false)}
        title="关闭"
        style={{
          position: "absolute",
          top: 16,
          right: 16,
          width: 22,
          height: 22,
          borderRadius: "50%",
          border: "none",
          background: closeHover ? T.closeBgHover : T.closeBg,
          color: T.textSecondary,
          fontSize: 11,
          cursor: "pointer",
          lineHeight: 1,
          padding: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          transition: "background 120ms ease",
        }}
        aria-label="关闭"
      >
        <svg width="9" height="9" viewBox="0 0 9 9" fill="none">
          <path
            d="M1 1 L8 8 M8 1 L1 8"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </svg>
      </button>

      {/* 视觉锚 — cyan 圆 + 白链条图标 */}
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 14,
          background: `linear-gradient(135deg, ${T.cyan}, ${T.cyanHover})`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 18,
          boxShadow: `0 6px 16px ${T.cyan}40`,
        }}
      >
        <svg
          width="28"
          height="28"
          viewBox="0 0 24 24"
          fill="none"
          stroke="white"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
          <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
        </svg>
      </div>

      <h3
        style={{
          margin: 0,
          fontSize: 19,
          fontWeight: 600,
          color: T.text,
          letterSpacing: "-0.015em",
          fontFamily: T.systemFont,
        }}
      >
        扫描关联建议
      </h3>
      <p
        style={{
          margin: "6px 0 0",
          fontSize: 13,
          color: T.textSecondary,
          lineHeight: 1.5,
          fontFamily: T.systemFont,
        }}
      >
        AI 扫描「{currentTitle}」的内容, 找出可能对应现有 wiki 节点的名字. 你确认后加入{" "}
        <strong style={{ color: T.text, fontWeight: 600 }}>关联概念</strong>{" "}
        段落, 图会自动拉这些节点进来.
      </p>

      {/* 内容区 — state-based render */}
      <div style={{ marginTop: 22, minHeight: 200 }}>
        {view === "loading" && (
          <div
            style={{
              padding: "40px 20px",
              textAlign: "center",
              color: T.textSecondary,
              fontSize: 13,
              fontFamily: T.systemFont,
            }}
          >
            <div style={{ fontSize: 28, marginBottom: 8 }}>⏳</div>
            AI 分析中… (通常 3-8 秒)
          </div>
        )}

        {view === "error" && (
          <div
            style={{
              padding: "20px",
              background: `${T.errorRed}15`,
              border: `1px solid ${T.errorRed}40`,
              borderRadius: 8,
              color: T.errorRed,
              fontSize: 13,
              fontFamily: T.systemFont,
              lineHeight: 1.5,
            }}
          >
            <div style={{ fontWeight: 600, marginBottom: 6 }}>❌ 出错</div>
            <div style={{ marginBottom: 12 }}>{error}</div>
            <button
              onClick={handleRetry}
              style={{
                padding: "6px 14px",
                border: `1px solid ${T.errorRed}`,
                borderRadius: 6,
                background: "transparent",
                color: T.errorRed,
                fontSize: 12,
                cursor: "pointer",
                fontFamily: T.systemFont,
              }}
            >
              重试
            </button>
          </div>
        )}

        {view === "loaded" && suggestions.length === 0 && (
          <div
            style={{
              padding: "40px 20px",
              textAlign: "center",
              color: T.textSecondary,
              fontSize: 13,
              fontFamily: T.systemFont,
              lineHeight: 1.6,
            }}
          >
            <div style={{ fontSize: 28, marginBottom: 8 }}>🔍</div>
            <div style={{ marginBottom: 4 }}>没找到明确匹配</div>
            <div style={{ fontSize: 11, color: T.textTertiary }}>
              AI 严格审阅了 body 里的内容, 没找到跟现有 wiki 节点明确对应的名字.
              <br />
              可以到左侧「+ 新建」加子概念, 或手动在 body 里加 `[[名字]]`.
            </div>
          </div>
        )}

        {view === "loaded" && suggestions.length > 0 && (
          <>
            <div
              style={{
                fontSize: 12,
                color: T.textSecondary,
                marginBottom: 10,
                fontFamily: T.systemFont,
              }}
            >
              找到 {suggestions.length} 个候选. 默认勾选强匹配 (置信度 ≥
              0.7). 你可以调整:
            </div>
            <div
              style={{
                maxHeight: 320,
                overflowY: "auto",
                border: `1px solid ${T.border}`,
                borderRadius: 8,
                padding: 4,
              }}
            >
              {suggestions.map((s) => {
                const badge = confidenceBadge(s.confidence);
                const isSelected = selected.has(s.title);
                return (
                  <label
                    key={s.title}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 10,
                      padding: "10px 12px",
                      cursor: "pointer",
                      borderRadius: 6,
                      background: isSelected
                        ? `${T.cyan}10`
                        : "transparent",
                      transition: "background 120ms ease",
                    }}
                    onMouseEnter={(e) => {
                      if (!isSelected)
                        (e.currentTarget as HTMLElement).style.background =
                          "rgba(0,0,0,0.03)";
                    }}
                    onMouseLeave={(e) => {
                      if (!isSelected)
                        (e.currentTarget as HTMLElement).style.background =
                          "transparent";
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelected(s.title)}
                      style={{
                        marginTop: 3,
                        accentColor: T.cyan,
                        cursor: "pointer",
                      }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                          marginBottom: 3,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: T.text,
                            fontFamily: T.systemFont,
                          }}
                        >
                          [[{s.title}]]
                        </span>
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 4,
                            background: `${badge.color}20`,
                            color: badge.color,
                            fontFamily: T.systemFont,
                            fontWeight: 500,
                          }}
                        >
                          {badge.label} · {(s.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: T.textSecondary,
                          lineHeight: 1.5,
                          fontFamily: T.systemFont,
                          marginBottom: 2,
                        }}
                      >
                        <span style={{ color: T.textTertiary }}>片段:</span>{" "}
                        {s.snippet}
                      </div>
                      {s.reason && (
                        <div
                          style={{
                            fontSize: 11,
                            color: T.textTertiary,
                            lineHeight: 1.5,
                            fontFamily: T.systemFont,
                          }}
                        >
                          <span style={{ color: T.textTertiary }}>依据:</span>{" "}
                          {s.reason}
                        </div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          </>
        )}

        {view === "applying" && (
          <div
            style={{
              padding: "40px 20px",
              textAlign: "center",
              color: T.textSecondary,
              fontSize: 13,
              fontFamily: T.systemFont,
            }}
          >
            <div style={{ fontSize: 28, marginBottom: 8 }}>💾</div>
            写入中…
          </div>
        )}

        {view === "done" && (
          <div
            style={{
              padding: "40px 20px",
              textAlign: "center",
              color: T.cyan,
              fontSize: 14,
              fontWeight: 600,
              fontFamily: T.systemFont,
            }}
          >
            <div style={{ fontSize: 32, marginBottom: 8 }}>✓</div>
            已加入 {selected.size} 个关联概念
          </div>
        )}
      </div>

      {/* 按钮 bar */}
      {(view === "loaded" || view === "applying") && (
        <div
          style={{
            display: "flex",
            gap: 8,
            marginTop: 20,
            paddingTop: 16,
            borderTop: `1px solid ${T.border}`,
            alignItems: "center",
          }}
        >
          <button
            onClick={onClose}
            disabled={view === "applying"}
            style={{
              padding: "9px 18px",
              border: "none",
              borderRadius: 8,
              background: "transparent",
              color: T.textSecondary,
              fontSize: 14,
              fontFamily: T.systemFont,
              cursor: view === "applying" ? "default" : "pointer",
            }}
          >
            取消
          </button>
          <div style={{ flex: 1 }} />
          <div
            style={{
              fontSize: 12,
              color: T.textSecondary,
              fontFamily: T.systemFont,
              marginRight: 8,
            }}
          >
            已选 {selected.size} / {suggestions.length}
          </div>
          <button
            onClick={handleApply}
            onMouseEnter={() => setApplyHover(true)}
            onMouseLeave={() => setApplyHover(false)}
            disabled={view === "applying" || selected.size === 0}
            style={{
              padding: "9px 22px",
              border: "none",
              borderRadius: 8,
              background:
                view === "applying"
                  ? T.textTertiary
                  : selected.size === 0
                    ? T.textTertiary
                    : applyHover
                      ? T.cyanHover
                      : T.cyan,
              color: "white",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: T.systemFont,
              cursor:
                view === "applying" || selected.size === 0
                  ? "default"
                  : "pointer",
              opacity: selected.size === 0 ? 0.6 : 1,
              transition: "background 120ms ease",
              boxShadow: selected.size > 0 ? `0 2px 6px ${T.cyan}40` : "none",
            }}
          >
            {view === "applying"
              ? "写入中…"
              : `应用 ${selected.size} 个 →`}
          </button>
        </div>
      )}
    </ModalShell>
  );
}
