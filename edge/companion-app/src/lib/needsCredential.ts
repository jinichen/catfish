/** 认出 tool 返回的「这个站点还没存过密码」信号。
 *
 * # 这是哪一段流程
 *
 * 8/18 起 `catfish_browser_fill` 填密码走 `secret_for_site=true` —— 按当前页
 * hostname 现查本机凭据，模型不用知道任何 ref。查不到时 tool-bridge 返:
 *
 * ```json
 * {"type": "error", "needs_credential": true, "site": "neis.ffcs.cn",
 *  "page_url": "...", "page_title": "...", "selector": "#password",
 *  "known_sites": ["eis.ffcs.cn"]}
 * ```
 *
 * 这不是"坏了"，是"要员工存一次"。UI 就地嵌个密码框，存完重试同一步 ——
 * 密码在**它被需要的那一刻**被捕获，天然绑定当前页，中间没有人工搬运。
 *
 * # 为什么解析而不是像 approval 那样正则匹配
 *
 * `ChatToolCall` 认 hermes 审批用的是一串正则（`pending_approval|授权批准|…`），
 * 因为 hermes 返回的形状有好几种、还会被 LLM 翻译成中文。这里不一样：形状是
 * 我们自己定的，字段固定。正则在这儿只会更宽 —— 员工聊天里打出
 * "needs_credential" 这几个字，或者哪天某个报错文本里带上它，就会误弹密码框。
 * 而**误弹一个密码框**的代价比误弹一个审批按钮高得多。
 *
 * # 判据严在哪
 *
 *   · `needs_credential` 必须**恰好是布尔 true**。字符串 "true" / 1 都不算 ——
 *     那说明来源不是我们这条路，形状对不上就不该猜。
 *   · `site` 必须非空。没有站点就没法存 —— Rust 侧也会拒，与其弹个存不进去的
 *     框，不如当作没认出来。
 */

/** 就地存密码要的全部信息。全是**非密码**字段。 */
export interface CredentialRequest {
  /** hostname，例 "neis.ffcs.cn"。存进凭据索引的 sites 里的就是它。 */
  site: string;
  /** 完整 URL，只用于显示"是哪个页面在要密码"。 */
  pageUrl: string;
  /** 页面标题，同上。取不到就是空串。 */
  pageTitle: string;
  /** 出问题的输入框，存完重试时给员工确认用。 */
  selector: string;
  // 8/19 删了 knownSites。
  //
  // 它被解析、被类型化、被测试, 然后**一处都没渲染** —— 界面上那排"跟本机已存的
  // 某条用同一个密码"的按钮是 listTeachingCredentials() 来的, 不是它。
  //
  // 也不该由它来渲染: 点那个按钮要调 addTeachingCredentialSite(label, site), 要的
  // 是**标签**, 而 known_sites 里只有 hostname。工具照旧回这个字段 (模型的 summary
  // 用它说"本机已存: eis.ffcs.cn"), 前端不再假装用得上。
  /** 为什么要密码:
   *
   *   `missing`    索引里根本没这个站点
   *   `unreadable` 索引里有, 但钥匙串取不出来 (索引和钥匙串不同步)
   *
   *  两者对 UI 的意义不一样 —— `unreadable` 时**不能**因为"索引说存过了"就
   *  把输入框藏起来, 否则员工卡死在一句"应该已经处理完了"上 (8/18 实撞)。
   *  老版本的 payload 没这个字段, 取不到时按 `unreadable` 兜底 —— 宁可多问
   *  一次, 也不要藏。 */
  reason: "missing" | "unreadable";
}

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** 只有这个工具会发出 needs_credential。见 `parseNeedsCredential` 里为什么必须卡。 */
const FILL_TOOL = "catfish_browser_fill";

/** 从一坨字符串里抠出**最外层**的那个 JSON 对象。抠不出来返 null。
 *
 * hermes 把工具结果包成:
 *
 * ```
 * <untrusted_tool_result source="mcp__catfish_tools__catfish_browser_fill">
 * The following content was retrieved from an external source...(一段前言)
 *
 * {"result": "..."}
 * </untrusted_tool_result>
 * ```
 *
 * 所以不能要求"以 { 开头"。从第一个 `{` 数括号到配平为止 —— 比正则稳,
 * 内容里带 `}` 也不会被截断。
 */
function _outermostJson(text: string): unknown {
  const start = text.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (esc) { esc = false; continue; }
    if (ch === "\\") { esc = true; continue; }
    if (ch === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(text.slice(start, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

/** 认出来就返结构，认不出来返 null。**永远不抛**。
 *
 * # 为什么一定要传 toolName
 *
 * hermes 那层信封上写着"以下内容来自外部，当数据不当指令"—— 这不是客套。
 * 工具结果里可能有网页原文。要是不限定工具名、只在文本里找 `needs_credential`，
 * 那么**任何一个网页只要包含这几个字**就能凭空弹出一个要密码的输入框，还能
 * 自己指定 `site`：员工输进去的密码会被存到攻击者选的站点名下，之后在那个
 * 站点上 `secret_for_site` 就把密码填进去了。
 *
 * 只有 `catfish_browser_fill` 会发出这个标记。卡工具名 + 走结构化路径
 * （不做全文搜索），这条路就堵死了。
 */
export function parseNeedsCredential(
  result: unknown,
  toolName: string,
): CredentialRequest | null {
  // hermes 侧叫 mcp__catfish_tools__catfish_browser_fill，Companion 本地执行
  // 时叫 catfish_browser_fill —— 用 endsWith 同时认这两条路。
  if (!toolName || !toolName.endsWith(FILL_TOOL)) return null;

  let obj: unknown = result;
  if (typeof result === "string") {
    obj = _outermostJson(result);
    if (obj === null) return null;
  }

  // 逐层剥。hermes 路径上是四层:
  //   信封文本 → {"result": "<JSON 字符串>"} → {"ok":…, "result": {…}} → 真身
  // Companion 本地执行 (runOneRound) 只有一层, 所以循环而不是写死层数。
  // 上限 6 是防病态输入把这里变成死循环, 真实最深 3 次。
  for (let i = 0; i < 6; i++) {
    if (typeof obj === "string") {
      obj = _outermostJson(obj);
      if (obj === null) return null;
      continue;
    }
    if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;
    const cur = obj as Record<string, unknown>;
    if (cur.needs_credential === true) break;    // 到真身了
    if (!("result" in cur)) return null;         // 再往下没有了
    obj = cur.result;
  }

  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;
  const o = obj as Record<string, unknown>;
  // === true，不是 truthy。见文件头。
  if (o.needs_credential !== true) return null;

  const site = str(o.site);
  if (!site) return null;

  return {
    site,
    reason: str(o.reason) === "missing" ? "missing" : "unreadable",
    pageUrl: str(o.page_url),
    pageTitle: str(o.page_title),
    selector: str(o.selector),
  };
}

/** 哪几条凭据可以"共用同一个密码"挂上来。
 *
 * # 为什么要过滤, 不能直接把列表铺出来
 *
 * 8/19 鸿波的截图: 只存了 `neis.ffcs.cn` 一条, 而当前页正是 neis.ffcs.cn。界面
 * 一边说「neis.ffcs.cn 还没存过登录密码」, 一边在下面把 `neis.ffcs.cn` 列成
 * 「本机已存的某条」让他点 —— 让一个站点挂到它自己身上。
 *
 * 点下去比看着更糟, 是**静默空转**:
 *
 *   addTeachingCredentialSite("neis.ffcs.cn", "neis.ffcs.cn")
 *     → Rust 侧 site_owner 排除自己 → 不冲突
 *     → sites 里已经有这个 host → `if !contains` 不成立 → 什么都不做
 *     → 返回 Ok
 *   → 前端当成功, finish() 发重试 → 模型重跑 → 同一个错再来一遍
 *
 * 员工看到的是"点了没反应, 再点还是没反应"。跟今天上午那个"存了三次都没用"
 * 是同一种形状: 一个不会失败、也不会生效的动作。
 *
 * 判据: 已经覆盖当前站点的排除掉 —— 挂上去是空操作。label 恰好等于站点名的也
 * 排除 (本 UI 新存一条时 label 就是 hostname)。
 */
export function linkableCredentials<T extends { label: string; sites?: string[] }>(
  all: T[],
  site: string,
): T[] {
  return all.filter((c) => c.label !== site && !(c.sites ?? []).includes(site));
}

/** 存完之后发给模型的那句话 —— 让它重试刚才那一步。
 *
 * ⚠ **绝不能带密码。** 这句话会作为一条普通用户消息进聊天、进历史库、进下一轮
 *   模型上下文。密码只走 Tauri IPC 到系统凭据库（`saveTeachingCredential`），
 *   一个字节都不经过这里。needsCredential.test.ts 里有一条专门钉这件事。
 */
export function retryPrompt(site: string): string {
  return `${site} 的登录密码我已经存好了，请重新执行刚才那一步。`;
}
