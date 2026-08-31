/**
 * 知识库 · chat 存 wiki / 读 / 写
 *
 * 2026-08-15 从 lib/tauri.ts 切出来 (1309 行超限)。纯搬迁, 逻辑一行未改。
 * 边界照抄原文件里作者早就画好的 `// ── xxx ──` 分节, 不是我另起的划分。
 *
 * lib/tauri.ts 现在是 barrel, 只做 re-export —— 62 个调用方一行没动。
 */

import { invoke as rawInvoke } from "@tauri-apps/api/core";

// ── BL-CATFISH-WIKI-MODE P1.2.2: chat 真 💾 button → wiki/queries/ 写盘 ──
export interface WikiQueryWriteResult {
  path: string;
  bytes: number;
}

export const wikiSaveChatMessage = (args: {
  date: string; // YYYY-MM-DD
  time: string; // HH:MM
  sessionId: string;
  messageId: string;
  userMessage: string;
  assistantResponse: string;
}) =>
  rawInvoke<WikiQueryWriteResult>("wiki_save_chat_message", {
    date: args.date,
    time: args.time,
    sessionId: args.sessionId,
    messageId: args.messageId,
    userMessage: args.userMessage,
    assistantResponse: args.assistantResponse,
  });

// ── BL-CATFISH-WIKI-MODE P3.3.2: wiki read API ──
/** P3.5.132 #5 (6/29 鸿波): typed relations.
 *  rel 字段 free-text (跟 catfish 现有 tags/kind 同款软约定),
 *  UI 真 datalist autocomplete 治 typo. 旧 frontmatter `related: [name]` 也兼容, rel=null. */
export interface RelatedRef {
  name: string;
  rel?: string | null;
}

export interface WikiFileInfo {
  rel_path: string;
  kind: "entity" | "concept" | "query";
  slug: string;
  title: string;
  subtype: string | null;
  tags: string[];
  /** P3.5.132 #5: 升级 typed RelatedRef 数组, 旧 frontmatter 自动 rel=null. */
  related: RelatedRef[];
  sources: string[];
  /** 8/4: 同一实体的其他叫法 (frontmatter `aliases: [...]`)。
   *  「中电福富」→「中电福富信息科技有限公司」这类简称/全称靠它连上, 而不是
   *  靠 title 子串猜 —— 子串会同时命中「销售许可证-中电福富API…」那种无关条目。 */
  aliases: string[];
  size_bytes: number;
  mtime: number;
  /** 8/4: 员工亲手写/改的条目带 'employee' —— 没有这个标记的都是 LLM 生成、
   *  没人看过的。数据上区分开之前, 员工分不出哪些可信。 */
  authored_by?: string | null;
  /** 缺失表示历史条目，按 active 兼容；pending 不进入关系图。 */
  ontology_status?: string | null;
}

export interface WikiFileFull {
  info: WikiFileInfo;
  content: string;
  frontmatter: string;
  body: string;
}

// P37 (6/5 鸿波): wiki 全文搜索 — BM25 + title/tag scoring
export interface WikiSearchHit {
  rel_path: string;
  title: string;
  kind: string;
  score: number;
  snippet: string;
  matched_in: string[];
}

export const wikiSearchText = (query: string) =>
  rawInvoke<WikiSearchHit[]>("wiki_search_text", { query });

export const wikiSearchHybrid = (query: string, topK?: number) =>
  rawInvoke<WikiSearchHit[]>("wiki_search_hybrid", { query, topK });

// P38 (6/5 鸿波): wiki 语义搜索 (本机 BGE-M3 ONNX). model 未装时 hits=[] + message 提示装法.
export interface WikiSemanticHit {
  rel_path: string;
  title: string;
  kind: string;
  score: number;
  snippet: string;
}
export interface WikiSemanticResult {
  hits: WikiSemanticHit[];
  model_loaded: boolean;
  indexed_count: number;
  message: string;
}

export const wikiSearchSemantic = (query: string, topK?: number) =>
  rawInvoke<WikiSemanticResult>("wiki_search_semantic", { query, topK });

export const wikiListFiles = () => rawInvoke<WikiFileInfo[]>("wiki_list_files");
export const wikiReadFile = (relPath: string) =>
  rawInvoke<WikiFileFull>("wiki_read_file", { relPath });

// P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本 (软删 → wiki-shared/.trash/)
export const wikiUninstallShared = (relPath: string) =>
  rawInvoke<WikiWriteResult>("wiki_uninstall_shared", { relPath });

// P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding (catfish_wiki_publish 扫用)
export interface SensitiveTermsCheck {
  exists: boolean;
  path: string;
  created: boolean;  // true = 本次刚创建模板
}
export const wikiSensitiveTermsEnsure = () =>
  rawInvoke<SensitiveTermsCheck>("wiki_sensitive_terms_ensure");

// P3.3.18 Phase 4 (6/10): 扫 ~/.catfish/wiki-shared/ 已装部门 wiki
export interface InstalledWikiSharedInfo {
  relPath: string;          // wiki-shared/dept/<name>/<file_id>.md (相对 ~/.catfish/)
  namespace: string;        // dept/finance
  fileId: string;           // hub 分配的 UUID
  title: string;
  kind: string;             // entity | concept | query
  publishedBy: string;
  publishedAt: string;      // ISO-8601 or ""
  installedAt: string;      // ISO-8601 or ""
  sizeBytes: number;
}
export const listInstalledWikiShared = () =>
  rawInvoke<InstalledWikiSharedInfo[]>("list_installed_wiki_shared");

// ── BL-CATFISH-WIKI-MODE P3.3.7: wiki write API ──
export interface WikiWriteResult {
  rel_path: string;
  bytes: number;
  created: boolean;
}

export const wikiCreateEntityOrConcept = (args: {
  kind: "entity" | "concept";
  title: string;
  subtype: string;
  tags: string[];
  /** P3.5.132 #5: dual-shape — 老 caller 真 string[] 仍可,
   *  新 caller 用 RelatedRef[] 真带 rel 字段. Rust 真 #[serde(untagged)] 自动 deserialize. */
  related: Array<string | RelatedRef>;
  body: string;
}) => rawInvoke<WikiWriteResult>("wiki_create_entity_or_concept", args);

export const wikiUpdateFile = (relPath: string, content: string) =>
  rawInvoke<WikiWriteResult>("wiki_update_file", { relPath, content });

/** P3.3.4 (6/9 鸿波): 软删 entity/concept/query → mv 到 wiki/.trash/<ts>-原名.md.
 * P3.5.132 #3 (6/29 鸿波): 加 dryRun + affectedFiles 先报谁会变 dangling. */
export interface AffectedFile {
  rel_path: string;
  title: string;
}

export interface WikiDeleteResult {
  dry_run: boolean;
  affected_files: AffectedFile[];
  trash_path: string | null;
  bytes: number;
}

export const wikiDeleteFile = (relPath: string, dryRun = false) =>
  rawInvoke<WikiDeleteResult>("wiki_delete_file", { relPath, dryRun });

// P28 / P29 (6/5 鸿波): Companion Dashboard 改 gateway/identity URL
// P3.4.1 (6/13 hb): 砍 secret_broker_url — 中央 secret-broker 服务删, OAuth
// token 改 Companion 本机存. ServerConfigCard 不再让员工配 broker URL.
export interface ServerConfig {
  gateway_url: string;
  gateway_token: string;
  token_source: "yaml" | "env" | "none";
  identity_url: string;
  /** P3.5.80 (7/28): catfish-web 中央门户 URL (endpoints.web_url).
   *
   *  跟 gateway_url、identity_url 是**三个独立配置**。
   *
   *  ⚠ **空串 = 未配置**, 不是默认值。Rust 侧刻意不返回
   *  `http://127.0.0.1:5173` —— 返了 UI 会显示得像已经配好, 而员工点开
   *  每个门户链接都指向自己这台机器。所以判空要用 `!cfg.web_url`,
   *  别假设它一定是个能打开的地址。
   *
   *  类型是必选而非 `?:` —— Rust 侧 `pub web_url: String` 恒返, 标成
   *  optional 会让调用方以为"可能没有这个字段", 掩盖掉"有字段但是空串"
   *  这个真正要处理的情况。 */
  web_url: string;
  // secret_broker_url 字段砍, 旧 build 兼容靠 optional
  secret_broker_url?: string;  // deprecated, 永远不返
}

export const readServerConfig = () =>
  rawInvoke<ServerConfig>("read_server_config");

export const writeServerConfig = (
  gatewayUrl: string,
  gatewayToken: string,
  identityUrl?: string,
  /** P3.5.80 (7/28): 不传 = 保持 yaml 里已有的 web_url 不动.
   *  登录门那张卡 (ServerSetupCard) 就是不传的, 别让它把配好的擦掉. */
  webUrl?: string,
) =>
  rawInvoke<void>("write_server_config", {
    gatewayUrl,
    gatewayToken,
    identityUrl,
    webUrl,
  });
