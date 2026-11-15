/** 对话里的 A2UI 卡片 (11/15)。
 *
 * # 照上游怎么做的
 *
 * A2UI (github.com/a2ui-project/a2ui) 是「智能体用数据描述界面、客户端用自己的组件画」的协议。鲶鱼照上游 eval 的
 * `subagent_tool` 策略 (eval/a2ui_eval/strategies/subagent_tool.py):
 *
 *   员工要选 / 填 / 勾 / 定时间 → 主小鲶调 catfish_form 的 card 动作 (自己决定要不要)
 *   → tool-bridge 的专员用官方 SDK 生成的 Express 提示词写卡片、SDK 校验、不过带着报错重写 (tool-bridge/a2ui_card.py)
 *   → 卡片存本机, 只把编号还给小鲶 (上游 saved out-of-band)
 *   → 这里按工具调用结果里的编号读出来 (Rust a2ui_card_read) 画在那次工具调用下面 (tabs/Chat/ToolCards.tsx)
 *   → 员工点按钮: 上游标准的 `action` 消息 (renderer_to_agent) 拼进一句话, 走 catfish:approval-send 发回同一个会话
 *   → 小鲶按出卡时的目的接着办 (发送 / 删除照常走审批)
 *
 * 卡片出了以后小鲶还能改里面的数据 (进度、状态): catfish_form 的 card_update 动作往卡片文件追加上游标准的
 * updateDataModel 消息, 这里看到那次调用就把新消息打到原来那张卡上, 卡片原地变 (上游说的「画布」用法)。
 *
 * 模型给的只是数据, 不是代码 —— 只能用下面 CARD_COMPONENTS 里的组件, 没有脚本、没有外链图片。
 * 卡片在 tool-bridge 已经过官方 SDK 校验; 这里再按白名单 / 引用 / 大小查一遍, 是防文件被改过的兜底。
 */

/** 卡片能用的组件 = 鲶鱼目录 (tool-bridge/a2ui_catalog.py, 顺序也一致 —— 那边测试对着这里查):
 *  A2UI 基础组件 12 个 (不放 Image / Video / AudioPlayer: 会去外网拉东西, 跟邮件里拦跟踪像素一个道理;
 *  Modal / Tabs / Icon 对话里用不上) + 鲶鱼自定义 4 个 (邮件卡 / 日程卡 / 待办项 / 文件卡, 见 tabs/Chat/catfishCardComponents.tsx)。
 *  函数去掉 openUrl (卡片不该能打开任意网址)。 */
export const CARD_BASIC_COMPONENTS = [
  "Text", "Row", "Column", "List", "Card", "Divider",
  "Button", "TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput",
] as const;
export const CARD_CUSTOM_COMPONENTS = ["EmailCard", "EventCard", "TodoItem", "FileCard"] as const;
export const CARD_COMPONENTS = [...CARD_BASIC_COMPONENTS, ...CARD_CUSTOM_COMPONENTS] as const;
export const CARD_BLOCKED_FUNCTIONS = ["openUrl"] as const;
/** 鲶鱼目录的 id (tool-bridge a2ui_catalog.CATALOG_ID; SDK 编出来的 createSurface 写这个) */
export const CARD_CATALOG_ID = "https://catfish.local/a2ui/catalogs/catfish/v1.json";
/** 主对话随请求声明的渲染能力 (上游 v1.0 renderer_capabilities.json 的形状): hermes 插件据此决定这个会话能不能出卡 */
export const CARD_RENDERER_CAPABILITIES = { "1.0": { supportedCatalogIds: [CARD_CATALOG_ID] } } as const;
/** 一张卡最多这么多字 (十几个输入框的采集卡约 5KB); 也让 zod 3 那个「超大数组校验耗尽内存」(见 vite.config.ts) 够不着 */
export const CARD_MAX_CHARS = 32_000;

/** 卡片编号 (tool-bridge a2ui_card.save_card 生成): c_YYYYMMDD_HHMMSS_6位十六进制 */
export const CARD_ID = /^c_\d{8}_\d{6}_[0-9a-f]{6}$/;

type Comp = { id?: unknown; component?: unknown; child?: unknown; children?: unknown; text?: unknown };

/** 消息里所有组件 (按 surface): createSurface 里内联的 (Express 编出来的就是这种) + updateComponents 的 */
function componentsBySurface(messages: unknown[]): Map<string, Comp[]> {
  const out = new Map<string, Comp[]>();
  for (const m of messages) {
    const msg = m as { createSurface?: { surfaceId?: unknown; components?: unknown[] }; updateComponents?: { surfaceId?: unknown; components?: unknown[] } };
    for (const block of [msg?.createSurface, msg?.updateComponents]) {
      if (!block || !Array.isArray(block.components)) continue;
      const sid = String(block.surfaceId ?? "");
      out.set(sid, [...(out.get(sid) ?? []), ...(block.components as Comp[])]);
    }
  }
  return out;
}

/** 白名单外的组件名 —— A2UI 的处理器碰到目录里没有的组件不报错 (11/15 测出来的), 白名单要自己把 */
export function unknownComponents(messages: unknown[]): string[] {
  const allowed = new Set<string>(CARD_COMPONENTS);
  const bad = new Set<string>();
  for (const comps of componentsBySurface(messages).values()) {
    for (const c of comps) {
      if (typeof c?.component !== "string" || !allowed.has(c.component)) bad.add(String(c?.component));
    }
  }
  return [...bad];
}

/** 用到了被拿掉的函数 (openUrl): 跟组件一样, 处理器不一定拦, 自己查 */
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

/** 引用了却没定义的组件 id (协议允许分次发, 对话里一张卡是一次给全的); 没有 root 也算。
 *  List 的 children 可以是模板 {path, componentId}: 模板组件也得在 */
export function missingComponents(messages: unknown[]): string[] {
  const missing: string[] = [];
  for (const comps of componentsBySurface(messages).values()) {
    const ids = new Set<string>();
    const refs = new Set<string>(["root"]);
    for (const c of comps) {
      if (typeof c?.id === "string") ids.add(c.id);
      if (typeof c?.child === "string") refs.add(c.child);
      if (Array.isArray(c?.children)) for (const ch of c.children) if (typeof ch === "string") refs.add(ch);
      const tpl = c?.children as { componentId?: unknown } | undefined;
      if (tpl && !Array.isArray(tpl) && typeof tpl === "object" && typeof tpl.componentId === "string") refs.add(tpl.componentId);
    }
    for (const r of refs) if (!ids.has(r)) missing.push(r);
  }
  return missing;
}

/** 画之前的兜底检查; 有问题返回原因 */
export function cardProblem(messages: unknown[]): string | null {
  if (!Array.isArray(messages) || messages.length === 0) return "卡片是空的";
  if (JSON.stringify(messages).length > CARD_MAX_CHARS) return "卡片太大";
  const unknown = unknownComponents(messages);
  if (unknown.length) return `用了卡片里不允许的组件 (${unknown.slice(0, 3).join("、")})`;
  const fns = blockedFunctions(messages);
  if (fns.length) return `用了卡片里不允许的功能 (${fns.join("、")})`;
  const missing = missingComponents(messages);
  if (missing.length) return `卡片缺了一部分 (${missing.slice(0, 3).join("、")})`;
  return null;
}

/** 卡片标题: 第一个 Text 组件的字 (去掉 Markdown 星号和「· 后半句」), 没有就用 fallback */
export function cardTitle(messages: unknown[], fallback: string): string {
  for (const comps of componentsBySurface(messages).values()) {
    for (const c of comps) {
      if (c?.component === "Text" && typeof c.text === "string" && c.text.trim()) {
        return c.text.replace(/\*\*/g, "").split(/\s+·\s+/)[0].trim().slice(0, 40);
      }
    }
  }
  return fallback;
}

/** 渲染端回调给的 action (web_core ActionPayload) */
export interface CardAction {
  name: string;
  surfaceId: string;
  sourceComponentId: string;
  timestamp: string;
  context: Record<string, unknown>;
}

export interface CardSubmission {
  title: string;
  /** 卡片编号 */
  key: string;
  action: CardAction;
}

const SUBMIT_HEAD = "（填好了卡片「";
const ACTION_FENCE = /```a2ui-action\s*\n([\s\S]*?)\n```/;
const CARD_MARK = /<!-- card:(c_\d{8}_\d{6}_[0-9a-f]{6}) -->/;

/** 点了卡片上的按钮 → 发回会话的那句话。第一行给人看; 代码块是上游标准的 v1.0 `action` 消息 (renderer_to_agent),
 *  原样给小鲶; 末尾带卡片编号 (会话重开时认得这张交过了)。 */
export function buildSubmission(s: CardSubmission): string {
  const message = { version: "1.0", action: s.action };
  return `${SUBMIT_HEAD}${s.title}」）\n\`\`\`a2ui-action\n${JSON.stringify(message, null, 1)}\n\`\`\`\n<!-- card:${s.key} -->`;
}

/** 反过来: 用户消息是不是一次卡片提交 (气泡要显示成「交了什么」, 不显示一坨 JSON) */
export function parseSubmission(text: string): CardSubmission | null {
  if (!text.startsWith(SUBMIT_HEAD)) return null;
  const m = ACTION_FENCE.exec(text);
  if (!m) return null;
  try {
    const p = JSON.parse(m[1]) as { action?: Partial<CardAction> };
    const a = p.action;
    if (!a || typeof a.name !== "string") return null;
    return {
      title: text.slice(SUBMIT_HEAD.length, text.indexOf("」）")),
      key: CARD_MARK.exec(text)?.[1] ?? "",
      action: {
        name: a.name, surfaceId: String(a.surfaceId ?? ""), sourceComponentId: String(a.sourceComponentId ?? ""),
        timestamp: String(a.timestamp ?? ""),
        context: a.context && typeof a.context === "object" ? (a.context as Record<string, unknown>) : {},
      },
    };
  } catch {
    return null;
  }
}

/** 会话里已经交过的卡片编号 */
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

/** 工具结果里找卡片编号。hermes 存进库 / 推给前端的结果外面包着一层说明文字
 *  (`<untrusted_tool_result …>…</untrusted_tool_result>`), 里面是 `{"result": "<JSON 字符串>"}`,
 *  tool-bridge 再包一层 `{ok, tool, result: {card_id…}}` —— 一层层剥到有 card_id 为止 (11/15 实机: 只认纯 JSON 就一张都画不出来)。 */
function findCardResult(raw: unknown): { card_id: string; revision?: unknown } | null {
  let cur: unknown = raw;
  for (let i = 0; i < 6 && cur != null; i++) {
    if (typeof cur === "string") {
      const start = cur.indexOf("{");
      const end = cur.lastIndexOf("}");
      if (start < 0 || end <= start) return null;
      try {
        cur = JSON.parse(cur.slice(start, end + 1));
      } catch {
        return null;
      }
      continue;
    }
    if (typeof cur !== "object") return null;
    const id = (cur as { card_id?: unknown }).card_id;
    if (typeof id === "string") return CARD_ID.test(id) ? (cur as { card_id: string; revision?: unknown }) : null;
    cur = (cur as { result?: unknown }).result;
  }
  return null;
}

type CardCall = { name: string; args?: Record<string, unknown>; result?: unknown };

/** 这次工具调用是不是出了一张卡片 (catfish_form 的 card 动作, 结果里带编号); 是就返回编号 */
export function cardIdFromToolCall(call: CardCall): string | null {
  if (!/(^|__)catfish_form$/.test(call.name)) return null;
  // args 在流式过程中可能还是 {} —— 结果里认得出卡片编号就算; card_update 的结果带 revision, 不是新卡
  if (call.args && call.args.action !== undefined && call.args.action !== "card") return null;
  const r = findCardResult(call.result);
  return r && r.revision === undefined ? r.card_id : null;
}

/** 这次工具调用是不是改了一张已有的卡片 (card_update, 结果带 revision); 是就返回编号和版本 */
export function cardUpdateFromToolCall(call: CardCall): { cardId: string; revision: number } | null {
  if (!/(^|__)catfish_form$/.test(call.name)) return null;
  if (call.args && call.args.action !== undefined && call.args.action !== "card_update") return null;
  const r = findCardResult(call.result);
  return r && typeof r.revision === "number" ? { cardId: r.card_id, revision: r.revision } : null;
}

/** card_update 成功后通知对话里那张卡重读 (ToolCards 发, 同编号的卡收) */
export const CARD_UPDATED_EVENT = "catfish:a2ui-card-updated";
