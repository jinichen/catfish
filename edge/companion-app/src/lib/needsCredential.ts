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
  /** 本机已经存过哪些站点 —— 员工看到"eis 存过、neis 没存"才知道是多入口。 */
  knownSites: string[];
}

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** 认出来就返结构，认不出来返 null。**永远不抛**。 */
export function parseNeedsCredential(result: unknown): CredentialRequest | null {
  let obj: unknown = result;
  if (typeof result === "string") {
    const s = result.trim();
    if (!s.startsWith("{")) return null;
    try {
      obj = JSON.parse(s);
    } catch {
      return null;
    }
  }
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) return null;

  const o = obj as Record<string, unknown>;
  // === true，不是 truthy。见文件头。
  if (o.needs_credential !== true) return null;

  const site = str(o.site);
  if (!site) return null;

  const known = Array.isArray(o.known_sites)
    ? o.known_sites.map(str).filter(Boolean)
    : [];

  return {
    site,
    pageUrl: str(o.page_url),
    pageTitle: str(o.page_title),
    selector: str(o.selector),
    knownSites: known,
  };
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
