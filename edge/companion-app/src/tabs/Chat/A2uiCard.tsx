/** 对话里的 A2UI 卡片 (11/15 试点), 协议和一来一回见 lib/a2uiCard.ts。
 *
 * 这个文件连带 @a2ui/react + @a2ui/web_core (+ Lit) 体积不小, 只经 lib/markdown 的 React.lazy 按需加载 ——
 * 对话里真出现卡片才拉, 平时不进首屏。
 */
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { A2uiSurface, MarkdownComponentContext, type ReactCatalogComponent } from "@a2ui/react";
import { Catalog, MessageProcessor, type ActionPayload, type SurfaceModel } from "@a2ui/web_core";
import { basicCatalog } from "@a2ui/web_core/catalogs/basic/v1";

import {
  blockedFunctions, buildSubmission, CARD_BLOCKED_FUNCTIONS, CARD_CATALOG_ID, CARD_COMPONENTS, cardKey, cardTitle, missingComponents, parseCardSource, unknownComponents,
} from "../../lib/a2uiCard";

/** 白名单目录: 基础目录里挑 CARD_COMPONENTS, 去掉 openUrl。模型的 createSurface 要写 catalogId = CARD_CATALOG_ID。 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let catalog: Catalog<any, any> | null = null;
function cardCatalog() {
  if (!catalog) {
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

/** Text 组件里的简单 Markdown (粗体之类): 只认 **粗体**, 其余按纯文字, 不引 markdown-it、不渲染 HTML */
function PlainMarkdown({ markdown }: { markdown: string }) {
  const parts = markdown.split(/(\*\*[^*]+\*\*)/g);
  return <span>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p))}</span>;
}

const box: CSSProperties = {
  margin: "var(--space-2) 0", padding: "10px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
  background: "var(--catfish-bg-elevated)", fontSize: 12, lineHeight: 1.6, color: "var(--catfish-text-muted)",
};

export interface A2uiCardProps {
  source: string;
  interactive: boolean;
  streaming: boolean;
  submitted: boolean;
  /** 点了按钮: 默认发回当前会话 (catfish:approval-send); 测试里换掉 */
  onSubmit?: (text: string) => void;
}


type Built =
  | { error: string }
  | { processor: MessageProcessor<ReactCatalogComponent>; surfaces: SurfaceModel<ReactCatalogComponent>[]; title: string;
      handlers: { onAction?: (a: ActionPayload) => void } };

/** 建好的卡片按 key 存在组件外面。
 *
 * react-markdown 每次重新渲染都会换一个新的 `pre` 组件, 卡片跟着卸了又装 —— 状态放组件里, 员工填了一半的内容
 * 一刷新 (后面的消息在流式输出、提交后会话多了一条) 就没了 (11/15 浏览器里实测)。processor 里存着填的值,
 * 所以整个缓存起来; 只缓存画得出来的, 格式坏的每次重新判断 (流式输出时代码块在变)。 */
const BUILT = new Map<string, Built>();
const BUILT_MAX = 40;

function buildCard(source: string, key: string): Built {
  const hit = BUILT.get(key);
  if (hit) return hit;
  const parsed = parseCardSource(source);
  if (!parsed.ok) return { error: parsed.reason };
  const unknown = unknownComponents(parsed.messages);
  if (unknown.length) return { error: `用了卡片里不允许的组件 (${unknown.slice(0, 3).join("、")})` };
  const fns = blockedFunctions(parsed.messages);
  if (fns.length) return { error: `用了卡片里不允许的功能 (${fns.join("、")})` };
  const missing = missingComponents(parsed.messages);
  if (missing.length) return { error: `卡片缺了一部分 (${missing.slice(0, 3).join("、")})` };
  const title = cardTitle(parsed.messages, "卡片");
  const handlers: { onAction?: (a: ActionPayload) => void } = {};
  let processor: MessageProcessor<ReactCatalogComponent>;
  try {
    processor = new MessageProcessor([cardCatalog()], (a: ActionPayload) => handlers.onAction?.(a), { reportErrorsOnly: true });
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  const errors = processor.processMessagesWithErrors(parsed.messages);
  if (errors.length) {
    const first = errors[0] as { error?: { message?: string; code?: string } };
    processor.dispose();
    return { error: first.error?.message || first.error?.code || "不符合 A2UI 协议" };
  }
  const surfaces = [...processor.model.surfacesMap.values()] as SurfaceModel<ReactCatalogComponent>[];
  if (!surfaces.length) {
    processor.dispose();
    return { error: "没有要显示的内容 (少了 createSurface)" };
  }
  const ok: Built = { processor, surfaces, title, handlers };
  BUILT.set(key, ok);
  if (BUILT.size > BUILT_MAX) {
    const oldest = BUILT.keys().next().value as string;
    const old = BUILT.get(oldest);
    if (old && "processor" in old) old.processor.dispose();
    BUILT.delete(oldest);
  }
  return ok;
}

/** 测试用: 拿缓存里的那份 (key 跟组件里一样算) */
export function buildCardForTest(source: string): Built {
  return buildCard(source, cardKey(source));
}

/** 测试用: 清掉缓存 */
export function _resetCardCacheForTest() {
  BUILT.clear();
}

const sendToChat = (text: string) => window.dispatchEvent(new CustomEvent("catfish:approval-send", { detail: { text } }));

export default function A2uiCard({ source, interactive, streaming, submitted, onSubmit = sendToChat }: A2uiCardProps) {
  const key = useMemo(() => cardKey(source), [source]);
  const [sentNow, setSentNow] = useState(false);
  const done = submitted || sentNow;

  const built = useMemo(() => buildCard(source, key), [source, key]);

  useEffect(() => {
    if (!("processor" in built)) return;
    built.handlers.onAction = (a) => {
      if (!interactive || done) return;
      setSentNow(true);
      onSubmit(buildSubmission({ title: built.title, key, name: a.name, surfaceId: a.surfaceId, context: a.context }));
    };
    return () => { built.handlers.onAction = undefined; };
  }, [built, interactive, done, key, onSubmit]);

  if ("error" in built) {
    if (streaming) return <div style={box}>卡片生成中…</div>;
    return (
      <div style={box} role="note">
        这张卡片的格式不对, 没法显示 ({built.error})。可以让小鲶重新出一张。
        <details style={{ marginTop: 4 }}>
          <summary style={{ cursor: "pointer" }}>看原文</summary>
          <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{source}</pre>
        </details>
      </div>
    );
  }

  return (
    <div className="a2ui-card" data-card={key} style={{ ...THEME, margin: "var(--space-2) 0", maxWidth: 640 }}>
      <div
        aria-disabled={done || !interactive}
        style={done ? { opacity: 0.6, pointerEvents: "none" } : undefined}
      >
        <MarkdownComponentContext.Provider value={PlainMarkdown}>
          {built.surfaces.map((s) => <A2uiSurface key={s.id} surface={s} />)}
        </MarkdownComponentContext.Provider>
      </div>
      {done && <div style={{ ...box, margin: "6px 0 0", padding: "4px 10px" }}>已交给小鲶 ✓</div>}
      {!done && !interactive && (
        <div style={{ ...box, margin: "6px 0 0", padding: "4px 10px" }}>这张卡片在「对话」里打开才能填写。</div>
      )}
    </div>
  );
}
