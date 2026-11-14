/** 对话里的 A2UI 卡片 (11/15 试点, 鸿波「按你的建议来做」: 先拿集结号采集表试)。
 *
 * # 是什么
 *
 * A2UI (github.com/a2ui-project/a2ui) 是一套「智能体用数据描述界面、客户端用自己的组件画」的协议:
 * 小鲶在回复里写一个 ```a2ui 代码块 (A2UI v1.0 消息列表), Companion 用白名单组件画成一张能填的卡片。
 * 模型给的只是数据, 不是代码 —— 只能用下面 CARD_COMPONENTS 里的组件, 没有脚本、没有外链图片。
 *
 * # 一来一回
 *
 *   小鲶回复 ```a2ui [...]```  →  A2uiCard 画出来  →  员工填、点按钮
 *   →  按钮的 action (name + 解析好的 context) 拼成一句话, 走 catfish:approval-send 发回同一个会话
 *      (跟审批按钮、「继续」同一条路, 不加新接口)
 *   →  小鲶 (技能里写了怎么处理这个 action) 去干活
 *
 * 发回去的那句话里带 ```a2ui-action``` 代码块和这张卡的 key —— 会话重新打开时, 交过的卡片照样显示「已交」,
 * 不会让人以为没交又点一次。
 *
 * # 降级
 *
 * - 格式不对 (JSON 坏了 / 不符合协议 / 用了白名单外的组件): 不画, 说一句「卡片格式不对」, 原文折叠可看。
 * - 还在流式输出: 代码块没写完时 JSON 肯定是坏的, 显示「卡片生成中…」, 不报错。
 * - 微信: 显示不了卡片, 外发前由插件换成一句文字 (catfish-xcatfish-user/plugin_weixin_zh.py)。
 */

/** 卡片能用的组件 (A2UI 基础组件的子集)。不放 Image / Video / AudioPlayer (会去外网拉东西, 跟邮件里拦跟踪像素一个道理)、
 *  Modal (对话里弹窗太重); 函数去掉 openUrl (让模型的卡片能开任意网址不是试点需要的)。 */
export const CARD_COMPONENTS = [
  "Text", "Row", "Column", "List", "Card", "Divider", "Tabs", "Icon",
  "Button", "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput",
] as const;
export const CARD_BLOCKED_FUNCTIONS = ["openUrl"] as const;
export const CARD_CATALOG_ID = "catfish:cards/v1";

/** 一张卡最多这么多字 (集结号采集卡约 5KB)。卡片是模型写的, 超长多半是写坏了;
 *  也让 zod 3 那个「超大数组校验耗尽内存」(GHSA-mrhx-c7xc-m3w5, 见 vite.config.ts) 在这里够不着。 */
export const CARD_MAX_CHARS = 32_000;

export type ParsedCard = { ok: true; messages: unknown[] } | { ok: false; reason: string };

/** ```a2ui 代码块的内容 → 消息列表。认三种写法: JSON 数组、单个消息对象、一行一个消息 (JSONL)。 */
export function parseCardSource(src: string): ParsedCard {
  const text = src.trim();
  if (!text) return { ok: false, reason: "代码块是空的" };
  if (text.length > CARD_MAX_CHARS) return { ok: false, reason: `卡片太大 (${Math.round(text.length / 1000)}K 字, 上限 ${CARD_MAX_CHARS / 1000}K)` };
  try {
    const v = JSON.parse(text);
    if (Array.isArray(v)) return v.length ? { ok: true, messages: v } : { ok: false, reason: "消息列表是空的" };
    if (v && typeof v === "object") return { ok: true, messages: [v] };
    return { ok: false, reason: "不是 A2UI 消息" };
  } catch (e) {
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1) {
      try {
        return { ok: true, messages: lines.map((l) => JSON.parse(l.replace(/,$/, ""))) };
      } catch {
        /* 落到下面报原来的错 */
      }
    }
    return { ok: false, reason: `JSON 解析失败: ${e instanceof Error ? e.message : String(e)}` };
  }
}

/** 白名单外的组件名。
 *
 * 11/15 测出来: A2UI 的处理器碰到目录里没有的组件 (比如 Image) 不报错 —— 白名单要我们自己把。有就整张不画,
 * 不是悄悄跳过那一块: 模型写了外链图片之类的东西, 员工应该看到「这张卡不对」, 而不是一张缺了块的卡。 */
export function unknownComponents(messages: unknown[]): string[] {
  const allowed = new Set<string>(CARD_COMPONENTS);
  const bad = new Set<string>();
  for (const m of messages) {
    for (const c of (m as { updateComponents?: { components?: unknown[] } })?.updateComponents?.components ?? []) {
      const name = (c as { component?: unknown })?.component;
      if (typeof name !== "string" || !allowed.has(name)) bad.add(String(name));
    }
  }
  return [...bad];
}

/** 用到了被拿掉的函数 (openUrl): 跟组件一样, 处理器不一定拦, 自己查 —— 整张卡里任何一处 "@call" 都算 */
export function blockedFunctions(messages: unknown[]): string[] {
  const blocked = new Set<string>(CARD_BLOCKED_FUNCTIONS);
  const hit = new Set<string>();
  const walk = (v: unknown): void => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") {
      for (const [k, x] of Object.entries(v)) {
        if (k === "@call" && typeof x === "string" && blocked.has(x)) hit.add(x);
        walk(x);
      }
    }
  };
  walk(messages);
  return [...hit];
}

/** 引用了却没定义的组件 id。
 *
 * A2UI 允许组件分几次发 (流式), 所以它的校验器不把「children 里的 id 找不到」当错 —— 但对话里一张卡是一个代码块
 * 一次给全的, 少了就是模型漏写了。11/15 实测 10 次里有 2 次漏了一整行 (来源 / 采集人), 照画会悄悄少两个字段。
 * 有缺的就不画, 让小鲶重出。也认 root: 一张卡没有 root 什么都画不出来。 */
export function missingComponents(messages: unknown[]): string[] {
  const perSurface = new Map<string, { ids: Set<string>; refs: Set<string> }>();
  for (const m of messages) {
    const uc = (m as { updateComponents?: { surfaceId?: string; components?: unknown[] } })?.updateComponents;
    if (!uc) continue;
    const sid = String(uc.surfaceId ?? "");
    const e = perSurface.get(sid) ?? { ids: new Set<string>(), refs: new Set<string>(["root"]) };
    for (const c of uc.components ?? []) {
      const comp = c as { id?: unknown; child?: unknown; children?: unknown };
      if (typeof comp.id === "string") e.ids.add(comp.id);
      if (typeof comp.child === "string") e.refs.add(comp.child);
      if (Array.isArray(comp.children)) for (const ch of comp.children) if (typeof ch === "string") e.refs.add(ch);
    }
    perSurface.set(sid, e);
  }
  const missing: string[] = [];
  for (const { ids, refs } of perSurface.values()) for (const r of refs) if (!ids.has(r)) missing.push(r);
  return missing;
}

/** 卡片的 key: 代码块内容的短哈希 (FNV-1a)。同一张卡重新打开会话时 key 不变, 用来认「这张交过了」。 */
export function cardKey(src: string): string {
  let h = 0x811c9dc5;
  const s = src.trim();
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

/** 卡片标题: 第一个 Text 组件的字 (去掉 Markdown 星号和「· 后半句」), 没有就用 surfaceId */
export function cardTitle(messages: unknown[], fallback: string): string {
  for (const m of messages) {
    const comps = (m as { updateComponents?: { components?: unknown[] } })?.updateComponents?.components;
    for (const c of comps ?? []) {
      const t = (c as { component?: string; text?: unknown })?.text;
      if ((c as { component?: string }).component === "Text" && typeof t === "string" && t.trim()) {
        return t.replace(/\*\*/g, "").split(/\s+·\s+/)[0].trim().slice(0, 40);
      }
    }
  }
  return fallback;
}

export interface CardSubmission {
  title: string;
  key: string;
  name: string;
  surfaceId: string;
  context: Record<string, unknown>;
}

const SUBMIT_HEAD = "（填好了卡片「";
const ACTION_FENCE = /```a2ui-action\s*\n([\s\S]*?)\n```/;

/** 点了卡片上的按钮 → 发回会话的那句话。第一行给人看, 代码块给小鲶 (技能里按 name 处理)。 */
export function buildSubmission(s: CardSubmission): string {
  const payload = { name: s.name, surfaceId: s.surfaceId, card: s.key, context: s.context };
  return `${SUBMIT_HEAD}${s.title}」）\n\`\`\`a2ui-action\n${JSON.stringify(payload, null, 1)}\n\`\`\``;
}

/** 反过来: 用户消息是不是一次卡片提交 (气泡要显示成「交了什么」, 不显示一坨 JSON) */
export function parseSubmission(text: string): CardSubmission | null {
  if (!text.startsWith(SUBMIT_HEAD)) return null;
  const m = ACTION_FENCE.exec(text);
  if (!m) return null;
  try {
    const p = JSON.parse(m[1]) as { name?: unknown; surfaceId?: unknown; card?: unknown; context?: unknown };
    if (typeof p.name !== "string") return null;
    const title = text.slice(SUBMIT_HEAD.length, text.indexOf("」）"));
    return {
      title, name: p.name, surfaceId: String(p.surfaceId ?? ""), key: String(p.card ?? ""),
      context: p.context && typeof p.context === "object" ? (p.context as Record<string, unknown>) : {},
    };
  } catch {
    return null;
  }
}

/** 会话里已经交过的卡片 key */
export function submittedCardKeys(messages: { role: string; content: string }[]): Set<string> {
  const keys = new Set<string>();
  for (const m of messages) {
    if (m.role !== "user") continue;
    const s = parseSubmission(m.content ?? "");
    if (s?.key) keys.add(s.key);
  }
  return keys;
}

/** 交上去的内容压成几行「字段: 值」给气泡显示 (嵌一层的对象展开, 空值不显示) */
export function submissionLines(context: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = [];
  const show = (v: unknown): string =>
    Array.isArray(v) ? v.map(String).filter(Boolean).join("、") : v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  for (const [k, v] of Object.entries(context)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) {
        const s = show(v2).trim();
        if (s) out.push([k2, s]);
      }
    } else {
      const s = show(v).trim();
      if (s) out.push([k, s]);
    }
  }
  return out;
}
