/** 数据外发记录的标准 (10/24, 鸿波定): 看带没带内容出去、是不是给第三方, 不看走哪条路。
 *
 *   · 发往本机 (localhost / 127.0.0.1, 中央服务的端口除外) —— 不记, 没出本机
 *     (原来记的 42 万条几乎全是界面每 3 秒问本机 hermes 的轮询)
 *   · 发往中央 —— 只记带内容的 (POST/PUT/PATCH 且有 body), 登录 / 续 token / 健康检查不记;
 *     GET 拉配额、目录这类不带员工的内容, 不记
 *   · 发往第三方 —— 都记
 *
 * hermes 出口那边同一套规则在 edge/hermes-plugins/catfish-xcatfish-user/egress_log.py。
 */

export type EgressKind = "content" | "third_party";

const LOOPBACK = new Set(["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"]);
const CONTENT_METHODS = new Set(["POST", "PUT", "PATCH"]);
const SKIP_PATHS = ["/token", "/oauth", "/authorize", "/userinfo", "/healthz", "/health", "/.well-known/"];
/** 中央服务默认端口: 网关 / 身份 / 技能 / 部门知识 (同一台机器) */
const CENTRAL_PORTS = [8999, 8998, 8997, 8994];
const HERMES_PORT = 8642;

function hostPort(url: string): { host: string; port: number; path: string } | null {
  try {
    const u = new URL(url);
    const port = u.port ? Number(u.port) : u.protocol === "https:" ? 443 : 80;
    return { host: u.hostname.toLowerCase(), port, path: u.pathname };
  } catch {
    return null;
  }
}

/** 中央服务的 host:port 集合 (配置里的网关 / 门户地址 + 同主机的默认端口; 本机 hermes 除外) */
export function centralSet(configured: string[]): Set<string> {
  const out = new Set<string>();
  const hosts = new Set<string>();
  for (const u of configured) {
    const hp = u ? hostPort(u) : null;
    if (!hp) continue;
    out.add(`${hp.host}:${hp.port}`);
    hosts.add(hp.host);
  }
  if (hosts.size === 0) hosts.add("127.0.0.1");
  for (const h of hosts) for (const p of CENTRAL_PORTS) out.add(`${h}:${p}`);
  for (const h of LOOPBACK) out.delete(`${h}:${HERMES_PORT}`);
  return out;
}

export function classifyEgress(method: string, url: string, bodyBytes: number, central: Set<string>): EgressKind | null {
  const hp = hostPort(url);
  if (!hp) return null;
  if (central.has(`${hp.host}:${hp.port}`)) {
    const m = method.toUpperCase();
    if (CONTENT_METHODS.has(m) && bodyBytes > 0 && !SKIP_PATHS.some((s) => hp.path.includes(s))) return "content";
    return null;
  }
  if (LOOPBACK.has(hp.host) || hp.host.endsWith(".localhost") || hp.host.endsWith(".local")) return null;
  return "third_party";
}

const PATH_PURPOSE: [string, string][] = [
  ["/v1/chat/completions", "模型调用"],
  ["/v1/embeddings", "向量计算"],
  // 11/17: 小鲶出图 / 出视频 (改图 / 图生视频带原图)
  ["/v1/images/", "出图 / 改图"],
  ["/v1/videos", "出视频"],
  ["/v1/hub/skills", "发布技能"],
  ["/v1/wiki/", "部门知识"],
  ["/api/proactive/", "主动提醒"],
  ["/api/egress/anchor", "报指纹 (防篡改)"],
  ["/api/show", "查询模型信息"],
  ["/api/room-link/mailbox", "同事协作 · 邮筒"],
  // 11/07: 登记 / 查同事的加密公钥 (只有公钥), 原来显示成「网页请求」
  ["/api/room-link/keys", "同事协作 · 加密公钥"],
];

/** 页面上看到的「做什么」: catfish_source 标签优先, 其次按路径 */
export function egressPurpose(url: string): string {
  try {
    const u = new URL(url);
    const src = u.searchParams.get("catfish_source");
    if (src) return sourceLabel(src);
    for (const [prefix, label] of PATH_PURPOSE) if (u.pathname.includes(prefix)) return label;
    return u.pathname || u.hostname;
  } catch {
    return url.slice(0, 60);
  }
}

/** 前端几处单发模型调用打的 catfish_source → 用途 */
export const SOURCE_PURPOSE: Record<string, string> = {
  "companion-chat": "对话",
  "companion-profile": "员工画像",
  "companion-advisor": "早安建议",
  "companion-advisor-transform": "早安建议",
  "companion-briefing-card": "早安卡片",
  "companion-email-draft": "邮件草稿",
  "companion-wiki-relation-suggest": "关系类型建议",
  "companion-wiki-suggest": "知识链接建议",
  // 11/5: 同事群聊 (tool-bridge 打的 X-Catfish-Source, 跟 egress_log.py 同一套叫法)
  "room-auto-post": "群聊 · 定时任务结果自动转发",
  "plugin:toolbridge-room-assist": "群聊 · 小鲶总结 / 起草",
  "plugin:toolbridge-room-revise": "一起改文档 · 小鲶给合并建议",
  "plugin:toolbridge-style-samples": "文书风格 · 总结范文写法",
  "plugin:toolbridge-task-check": "派任务 · 小鲶核对结果",
  "room-file": "群聊 · 发文件",
  // 11/06: tool-bridge 直接调网关的几处 (原来不带来源)
  "plugin:toolbridge-memory-compact": "整理记忆",
  "plugin:toolbridge-memory-dedupe": "记忆去重判断",
  "plugin:toolbridge-expertise": "专长提取",
  "plugin:toolbridge-browser-locate": "浏览器 · 看图找位置",
  "plugin:toolbridge-captcha": "浏览器 · 识别验证码",
  "plugin:toolbridge-workflow": "工作流 · 小鲶判断",
  "plugin:toolbridge-expert-builder": "专家 · 一句话搭专家",
  "plugin:toolbridge-wiki-props": "知识库 · 整理属性",
  // 11/18 学习 (learning*.py, 按功能分开标) / 会中问小鲶 / 对话卡片 —— 原来没登记, 中央配额日志显示一串 plugin:toolbridge-…
  "plugin:toolbridge-learning": "学习 · 摸底 / 讲解",
  "plugin:toolbridge-learning-practice": "学习 · 练习",
  "plugin:toolbridge-learning-cards": "学习 · 做复习卡",
  "plugin:toolbridge-learning-graph": "学习 · 沉淀要点",
  "plugin:toolbridge-learning-drill": "学习 · 答辩陪练",
  "plugin:toolbridge-learning-lang": "学习 · 外语",
  "plugin:toolbridge-meeting": "会议 · 纪要 / 让小鲶改",
  "plugin:toolbridge-meeting-live": "会议 · 会中问小鲶 / 提示",
  "plugin:toolbridge-card": "对话 · 卡片",
  // 11/17: 小鲶出图 / 出视频 (hermes 插件 media_gen.py, 跟 egress_log.py 同一套叫法)
  "plugin:media-image": "出图 / 改图",
  "plugin:media-video": "出视频",
  // 11/19 跟 egress_log.py 的 _SOURCE_PURPOSE 补齐 (测试钉住两边同一套): 外发记录里存的是原始标签时页面照样翻成中文
  "plugin:memory-wiki-ground": "知识卡核对",
  "plugin:memory-wiki-analysis": "资料蒸馏",
  "plugin:memory-wiki-generation": "资料蒸馏",
  "plugin:memory-wiki-merge": "资料蒸馏",
  "plugin:memory-wiki": "知识卡整理",
  "plugin:memory-distill": "长期记忆整理",
  "plugin:memory-summarize": "长期记忆整理",
  "plugin:memory-reconcile": "长期记忆整理",
  "plugin:memory-classify": "记忆归类",
  "plugin:memory-recall-embed": "记忆检索",
  "plugin:memory": "长期记忆",
  "companion-email-scheduler": "邮件整理",
  "companion-phishing-scan": "钓鱼邮件检查",
  "hermes:aux:compression": "对话 · 上下文压缩",
  "hermes:aux:vision": "对话 · 看图",
  "hermes:aux:title_generation": "对话 · 起标题",
  "hermes:aux:approval": "对话 · 审批判断",
  "hermes:aux:background_review": "后台 · 技能 / 记忆复盘",
  "hermes:aux:mcp": "MCP 工具",
  "hermes:aux": "hermes 辅助",
  "hermes:api_server": "对话",
  "hermes:webui": "对话",
  "hermes:weixin": "微信对话",
  "hermes:cron": "定时任务",
  "hermes:curator": "后台 · 技能整理",
  "hermes:subagent": "子任务",
  "companion": "Companion",
};

/** 10/28: 老记录里存的用途名换成现在的叫法再显示。
 *  「做什么」是指纹链的一部分 (_CHAIN_FIELDS 里有 purpose), 老记录不能改库, 只能显示时换。 */
/** catfish_source 标签 → 用途。跟 egress_log.py purpose() 一样按前缀、长的优先;
 *  一个都对不上时跟中央配额日志 (auditSource.ts) 同样的兜底叫法, 不把 plugin:toolbridge-xxx 原样露出来。 */
export function sourceLabel(src: string): string {
  const s = src.trim();
  const hit = Object.keys(SOURCE_PURPOSE).filter((k) => s.startsWith(k)).sort((a, b) => b.length - a.length)[0];
  if (hit) return SOURCE_PURPOSE[hit];
  if (s.startsWith("plugin:toolbridge-")) return `小鲶后台 · ${s.slice("plugin:toolbridge-".length)}`;
  if (s.startsWith("plugin:memory-")) return `后台 · 记忆 (${s.slice("plugin:memory-".length)})`;
  if (s.startsWith("companion-")) return `Companion · ${s.slice("companion-".length)}`;
  return s;
}

const RAW_SOURCE = /^(plugin:|hermes:|companion(-|$)|room-)/;

export function displayPurpose(purpose: string, destHost: string, url = ""): string {
  // 11/19 鸿波「companion 也应该和中央端一样的表述」: 早先 (标签还没登记 / 后台没重启) 记下的是原始标签, 显示时再翻一次
  if (RAW_SOURCE.test(purpose)) return sourceLabel(purpose);
  if (purpose === "/api/egress/anchor") return "报指纹 (防篡改)";
  if (purpose === "/api/show") return "查询模型信息";
  if (purpose === "/api/room-link/mailbox") return "同事协作 · 邮筒";
  if (purpose === "/api/room-link/keys") return "同事协作 · 加密公钥";
  if (purpose === "微信") return destHost.includes("cdn.") ? "微信 · 发图片 / 文件" : "微信 · 收发消息";
  // 11/02: 没标用途的第三方请求, purpose 里落的是网址路径 (/captcha_v4/… /static_resources/…) ——
  // 每条路径各占一行, 又长又看不懂。按去向合成一行「网页请求」, 具体网址在下面明细里看。
  if (purpose.startsWith("/")) return "网页请求";
  if (purpose === "长期记忆") {
    if (url.includes("memory-classify")) return "记忆归类";
    if (url.includes("memory-recall-embed")) return "记忆检索";
  }
  return purpose;
}

/** 汇总表: 换完叫法后同名同去向的老 / 新行并成一行 */
export function mergeSummaryRows<T extends { purpose: string; destHost: string; destKind: string; count: number;
  bytesUp: number; bytesDown: number; lastTs: string }>(rows: T[]): T[] {
  const out = new Map<string, T>();
  for (const r of rows) {
    const purpose = displayPurpose(r.purpose, r.destHost);
    const key = `${r.destKind}|${purpose}|${r.destHost}`;
    const prev = out.get(key);
    out.set(key, prev
      ? { ...prev, count: prev.count + r.count, bytesUp: prev.bytesUp + r.bytesUp, bytesDown: prev.bytesDown + r.bytesDown,
          lastTs: prev.lastTs > r.lastTs ? prev.lastTs : r.lastTs }
      : { ...r, purpose });
  }
  return [...out.values()].sort((a, b) => b.count - a.count);
}

export function egressHost(url: string): string {
  return hostPort(url)?.host ?? "";
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function describeBinary(buf: ArrayBuffer, name?: string): Promise<string> {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf); // 其实是文本
  } catch {
    const label = name ? `文件 ${name}` : "二进制内容";
    return `[${label} ${buf.byteLength} 字节, sha256=${await sha256Hex(buf)}; 未保存原件]`;
  }
}

/** 10/25: 要存进外发记录的请求内容。文本原样; 文件 / 二进制只记名字、大小、sha256 (拿着原件能对上);
 *  表单逐项列出。去密钥 / 大消息去重在 Rust 那边 (transparent_log_store.rs) 做。 */
export async function describeBody(body: BodyInit | null | undefined): Promise<string | undefined> {
  if (body == null) return undefined;
  if (typeof body === "string") return body;
  if (body instanceof URLSearchParams) return body.toString();
  if (body instanceof Blob) {
    return describeBinary(await body.arrayBuffer(), body instanceof File ? body.name : undefined);
  }
  if (body instanceof ArrayBuffer) return describeBinary(body);
  if (ArrayBuffer.isView(body)) {
    return describeBinary(body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength) as ArrayBuffer);
  }
  if (body instanceof FormData) {
    const lines: string[] = ["[表单]"];
    for (const [k, v] of body.entries()) {
      if (typeof v === "string") lines.push(`${k}=${v}`);
      else lines.push(`${k}=${await describeBinary(await v.arrayBuffer(), v.name)}`);
    }
    return lines.join("\n");
  }
  return "[流式请求体, 读不到内容]";
}
