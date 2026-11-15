/** 对话里的 A2UI 卡片 (11/15): 画一张已经读出来的卡片。协议和一来一回见 lib/a2uiCard.ts。
 *
 * 这个文件连带 @a2ui/react + @a2ui/web_core (+ Lit) 体积不小, 只经 ToolCards.tsx 的 React.lazy 按需加载 ——
 * 对话里真出现卡片才拉, 平时不进首屏。
 */
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { A2uiSurface, MarkdownComponentContext, type ReactCatalogComponent } from "@a2ui/react";
import { Catalog, MessageProcessor, type ActionPayload, type SurfaceModel } from "@a2ui/web_core";
import { basicCatalog } from "@a2ui/web_core/catalogs/basic/v1";

import { buildSubmission, CARD_BLOCKED_FUNCTIONS, CARD_CATALOG_ID, CARD_COMPONENTS, cardProblem, cardTitle } from "../../lib/a2uiCard";

/** 白名单目录: 上游 v1.0 基础目录里挑 CARD_COMPONENTS, 去掉 openUrl; id 不变 (tool-bridge 用官方 SDK 编出来的就写这个 id) */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let catalog: Catalog<any, any> | null = null;
function cardCatalog() {
  if (!catalog) {
    if (basicCatalog.id !== CARD_CATALOG_ID) {
      // 升级 @a2ui/web_core 后基础目录换了 id: tool-bridge 那边的 catalog JSON 也要一起换 (data/a2ui/NOTICE.md)
      console.warn(`[a2ui] 基础目录 id 变了: ${basicCatalog.id}`);
    }
    const keep = new Set<string>(CARD_COMPONENTS);
    const blocked = new Set<string>(CARD_BLOCKED_FUNCTIONS);
    catalog = new Catalog(
      CARD_CATALOG_ID, "1.0",
      [...basicCatalog.components.values()].filter((c) => keep.has(c.name)),
      [...basicCatalog.functions.values()].filter((f) => !blocked.has(f.name)),
    );
  }
  return catalog;
}

/** A2UI 的样式变量 → 鲶鱼的配色 (深浅色跟着 --catfish-* 走) */
const THEME: CSSProperties = {
  "--a2ui-color-primary": "var(--catfish-cyan)",
  "--a2ui-color-primary-hover": "var(--catfish-cyan)",
  "--a2ui-color-primary-light": "var(--catfish-cyan)",
  "--a2ui-color-primary-dark": "var(--catfish-cyan)",
  "--a2ui-color-on-primary": "#fff",
  "--a2ui-color-secondary": "var(--catfish-bg-elevated)",
  "--a2ui-color-secondary-hover": "var(--catfish-bg-elevated)",
  "--a2ui-color-on-secondary": "var(--catfish-text)",
  "--a2ui-color-background": "var(--catfish-bg)",
  "--a2ui-color-on-background": "var(--catfish-text)",
  "--a2ui-color-surface": "var(--catfish-bg-elevated)",
  "--a2ui-color-on-surface": "var(--catfish-text)",
  "--a2ui-color-input": "var(--catfish-bg)",
  "--a2ui-color-on-input": "var(--catfish-text)",
  "--a2ui-color-border": "var(--catfish-border)",
  "--a2ui-font-family": "inherit",
  "--a2ui-font-family-title": "inherit",
  "--a2ui-font-size": "13px",
  "--a2ui-border-radius": "6px",
} as CSSProperties;

/** Text 组件里的简单 Markdown: 只认 **粗体**, 其余按纯文字, 不引 markdown-it、不渲染 HTML */
function PlainMarkdown({ markdown }: { markdown: string }) {
  const parts = markdown.split(/(\*\*[^*]+\*\*)/g);
  return <span>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p))}</span>;
}

export const cardNoteStyle: CSSProperties = {
  margin: "var(--space-2) 0", padding: "10px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
  background: "var(--catfish-bg-elevated)", fontSize: 12, lineHeight: 1.6, color: "var(--catfish-text-muted)",
};

type Built =
  | { error: string }
  | { processor: MessageProcessor<ReactCatalogComponent>; surfaces: SurfaceModel<ReactCatalogComponent>[]; title: string;
      handlers: { onAction?: (a: ActionPayload) => void } };

/** 建好的卡片按编号存在组件外面: 对话列表重渲染时卡片可能卸了又装, 员工填了一半的值 (在 processor 的数据模型里) 不能丢 */
const BUILT = new Map<string, Built>();
const BUILT_MAX = 40;

function buildCard(cardId: string, messages: unknown[]): Built {
  const hit = BUILT.get(cardId);
  if (hit) return hit;
  const problem = cardProblem(messages);
  if (problem) return { error: problem };
  const handlers: { onAction?: (a: ActionPayload) => void } = {};
  let processor: MessageProcessor<ReactCatalogComponent>;
  try {
    processor = new MessageProcessor([cardCatalog()], (a: ActionPayload) => handlers.onAction?.(a), { reportErrorsOnly: true });
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  const errors = processor.processMessagesWithErrors(messages);
  if (errors.length) {
    const first = errors[0] as { error?: { message?: string; code?: string } };
    processor.dispose();
    return { error: first.error?.message || first.error?.code || "不符合 A2UI 协议" };
  }
  const surfaces = [...processor.model.surfacesMap.values()] as SurfaceModel<ReactCatalogComponent>[];
  if (!surfaces.length) {
    processor.dispose();
    return { error: "没有要显示的内容" };
  }
  const ok: Built = { processor, surfaces, title: cardTitle(messages, "卡片"), handlers };
  BUILT.set(cardId, ok);
  if (BUILT.size > BUILT_MAX) {
    const oldest = BUILT.keys().next().value as string;
    const old = BUILT.get(oldest);
    if (old && "processor" in old) old.processor.dispose();
    BUILT.delete(oldest);
  }
  return ok;
}

/** 测试用 */
export function buildCardForTest(cardId: string, messages: unknown[]): Built {
  return buildCard(cardId, messages);
}
export function _resetCardCacheForTest() {
  BUILT.clear();
}

const sendToChat = (text: string) => window.dispatchEvent(new CustomEvent("catfish:approval-send", { detail: { text } }));

export interface A2uiCardProps {
  cardId: string;
  messages: unknown[];
  interactive: boolean;
  submitted: boolean;
  /** 点了按钮: 默认发回当前会话 (catfish:approval-send); 测试里换掉 */
  onSubmit?: (text: string) => void;
}

export default function A2uiCard({ cardId, messages, interactive, submitted, onSubmit = sendToChat }: A2uiCardProps) {
  const [sentNow, setSentNow] = useState(false);
  const done = submitted || sentNow;
  const built = useMemo(() => buildCard(cardId, messages), [cardId, messages]);

  useEffect(() => {
    if (!("processor" in built)) return;
    built.handlers.onAction = (a) => {
      if (!interactive || done) return;
      setSentNow(true);
      onSubmit(buildSubmission({
        title: built.title, key: cardId,
        action: { name: a.name, surfaceId: a.surfaceId, sourceComponentId: a.sourceComponentId, timestamp: a.timestamp, context: a.context },
      }));
    };
    return () => { built.handlers.onAction = undefined; };
  }, [built, interactive, done, cardId, onSubmit]);

  if ("error" in built) {
    return <div style={cardNoteStyle} role="note">这张卡片画不出来 ({built.error})。可以让小鲶改用文字问。</div>;
  }

  return (
    <div className="a2ui-card" data-card={cardId} style={{ ...THEME, margin: "var(--space-2) 0", maxWidth: 640 }}>
      <div aria-disabled={done || !interactive} style={done ? { opacity: 0.6, pointerEvents: "none" } : undefined}>
        <MarkdownComponentContext.Provider value={PlainMarkdown}>
          {built.surfaces.map((s) => <A2uiSurface key={s.id} surface={s} />)}
        </MarkdownComponentContext.Provider>
      </div>
      {done && <div style={{ ...cardNoteStyle, margin: "6px 0 0", padding: "4px 10px" }}>已交给小鲶 ✓</div>}
      {!done && !interactive && (
        <div style={{ ...cardNoteStyle, margin: "6px 0 0", padding: "4px 10px" }}>这张卡片在「对话」里打开才能填写。</div>
      )}
    </div>
  );
}
