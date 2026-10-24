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
  ["/v1/hub/skills", "发布技能"],
  ["/v1/wiki/", "部门知识"],
  ["/api/proactive/", "主动提醒"],
];

/** 页面上看到的「做什么」: catfish_source 标签优先, 其次按路径 */
export function egressPurpose(url: string): string {
  try {
    const u = new URL(url);
    const src = u.searchParams.get("catfish_source");
    if (src) return SOURCE_PURPOSE[src] ?? src;
    for (const [prefix, label] of PATH_PURPOSE) if (u.pathname.includes(prefix)) return label;
    return u.pathname || u.hostname;
  } catch {
    return url.slice(0, 60);
  }
}

/** 前端几处单发模型调用打的 catfish_source → 用途 */
const SOURCE_PURPOSE: Record<string, string> = {
  "companion-chat": "对话",
  "companion-profile": "员工画像",
  "companion-advisor": "早安建议",
  "companion-advisor-transform": "早安建议",
  "companion-briefing-card": "早安卡片",
  "companion-email-draft": "邮件草稿",
  "companion-wiki-relation-suggest": "关系类型建议",
  "companion-wiki-suggest": "知识链接建议",
};

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
