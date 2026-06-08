/** 6/8 BL-PRIVACY-SECTION-TABS (鸿波 6/8): section 内 sub-tabs.
 *
 * macOS Settings App 风格 — section header 已经在 CollapsibleSection 给左
 * sidebar, 这里在右侧顶部给 horizontal tabs, 切换不同 content. 比纵向堆 N 卡:
 *   - 空间利用率 +25% (扫一眼只显当前 tab content, 不挤)
 *   - 信息分组更清晰
 *   - 各 tab 独立, 互不打架
 *
 * # localStorage 记忆
 *
 * tab 选中保存到 `section_tab_<storageKey>`. 重启 Companion 还原 user 上次
 * 选的 tab. 第一次访问 / localStorage 禁 → 落到第一个 tab.
 *
 * # 跟 manifesto 一致
 *
 * 不主动 fetch 其它 tab content (lazy). user 切 tab 才 render, 性能友好.
 */
import * as React from "react";

export interface Tab {
  /** localStorage 用 key, e.g. "privacy" / "outbound" / "wechat" */
  key: string;
  /** tab bar 显示文字 (含 emoji), e.g. "🟢 隐私状态" */
  label: string;
  /** tab 内容, lazy render — 未选中不调用 */
  render: () => React.ReactNode;
}

interface Props {
  /** localStorage key prefix, e.g. "privacy_section" → 存 "section_tab_privacy_section" */
  storageKey: string;
  tabs: Tab[];
  /** 默认 tab key, 未指 = tabs[0].key */
  defaultKey?: string;
}

function readSelectedTab(storageKey: string, fallback: string): string {
  try {
    const v = localStorage.getItem(`section_tab_${storageKey}`);
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

function writeSelectedTab(storageKey: string, key: string): void {
  try {
    localStorage.setItem(`section_tab_${storageKey}`, key);
  } catch {
    /* silent */
  }
}

export default function SectionTabs({ storageKey, tabs, defaultKey }: Props) {
  const initialKey = defaultKey ?? tabs[0]?.key ?? "";
  const [selectedKey, setSelectedKey] = React.useState(() =>
    readSelectedTab(storageKey, initialKey),
  );

  // localStorage 持久化 (避免 selectedKey 已不存在 tabs 中时 fallback)
  const validKey = tabs.some((t) => t.key === selectedKey) ? selectedKey : initialKey;
  const activeTab = tabs.find((t) => t.key === validKey);

  const onSelect = (key: string) => {
    setSelectedKey(key);
    writeSelectedTab(storageKey, key);
  };

  return (
    <div style={{ minWidth: 0 }}>
      {/* tab bar */}
      <div
        role="tablist"
        style={{
          display: "flex",
          gap: 0,
          borderBottom: "1px solid var(--catfish-border)",
          marginBottom: "var(--space-3)",
        }}
      >
        {tabs.map((t) => {
          const active = t.key === validKey;
          return (
            <button
              key={t.key}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => onSelect(t.key)}
              style={{
                background: "transparent",
                border: "none",
                padding: "8px 14px",
                fontSize: 13,
                fontWeight: active ? 600 : 400,
                color: active ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
                cursor: "pointer",
                borderBottom: active
                  ? "2px solid var(--catfish-cyan)"
                  : "2px solid transparent",
                marginBottom: -1, // 跟 wrapper border-bottom 重叠 (active 视觉接续)
                transition: "color 0.15s ease, border-color 0.15s ease",
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {/* 当前 tab content (lazy) */}
      <div role="tabpanel" aria-labelledby={validKey}>
        {activeTab?.render()}
      </div>
    </div>
  );
}
