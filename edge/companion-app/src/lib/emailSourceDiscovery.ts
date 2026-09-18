import type { EmailSourceDiscovery } from "./tauri";

export function parseEmailSourceDiscovery(raw: string): EmailSourceDiscovery {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") {
    throw new Error("邮件发现返回格式异常");
  }
  const payload = value as Partial<EmailSourceDiscovery>;
  if (!Array.isArray(payload.sources) || typeof payload.platform !== "string") {
    throw new Error("邮件发现缺少来源信息");
  }
  if (payload.selected_client !== undefined && payload.selected_client !== null &&
      payload.selected_client !== "outlook-win" && payload.selected_client !== "eml-dir" &&
      payload.selected_client !== "imap") {
    throw new Error("邮件发现的已选来源无效");
  }
  return payload as EmailSourceDiscovery;
}

export function readyEmailSources(discovery: EmailSourceDiscovery) {
  return discovery.sources.filter((source) => source.status === "ready");
}

/** 要不要把「配置邮件来源」那张卡片摆出来。
 *
 * 9/18 从 EmailTab.tsx 搬出来 (那个文件 807 行, 越过仓库 800 行红线), 顺便
 * 变成可测的 —— 这是条纯判断, 不该只能靠开着 App 看。
 *
 * # 判据换过一次
 *
 * 原来是 `readySourceCount === 0`。鸿波 catch "现在同时从客户端和 IMAP 一起
 * 吗？会打架的" —— 他那台 Mac 上 Apple Mail 好好收着 342 封信, 配置卡片却
 * 一直摆在那。
 *
 * 真因: readySourceCount 数的是**发现模块认得的来源** (imap / outlook-win /
 * eml-dir), 而 macOS 真正在用的 Apple Mail 根本不在那个列表里, 所以永远是 0。
 *
 * 在一个明明能正常收信的界面上摆"配置别的来源"的入口, 等于邀请员工去制造
 * 来源冲突: 同一个邮箱两边都能读到, 列表看不出区别, 但删除按钮可能就废了。
 *
 * 换成"**实际有没有拿到邮件**" —— 这是个跟平台无关的事实, 不需要发现模块
 * 认得那个来源。
 */
export function needsEmailSourceSetup(input: {
  discovery: EmailSourceDiscovery | null;
  /** 列表真的空了 (且不是正在加载) */
  noMailAtAll: boolean;
  /** 拉取本身失败了 */
  failed: boolean;
}): boolean {
  const { discovery, noMailAtAll, failed } = input;
  if (!discovery) return false;
  if (failed || noMailAtAll) return true;
  // 认得好几个来源、员工又没选过 —— 这时候得让他挑一个, 否则合并结果里
  // 哪个赢是我们替他定的。
  return readyEmailSources(discovery).length > 1 && !discovery.selected_client;
}
