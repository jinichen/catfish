// 10/22: 配额日志「来源」列 —— 网关审计里的 X-Catfish-Source 翻成管理员看得懂的话。
//
// 原来这一页只有「员工」列, 后台任务一律显示成 client:hermes-cli (hermes 的服务账号),
// 管理员看到一串失败不知道是什么在跑 (10/22 那串 504 其实是员工机的记忆蒸馏)。
// 名字由各调用方写死 (Companion / hermes 插件 / tool-bridge), 这里只管翻译;
// 不认识的原样显示, 不猜。

const LABELS: Record<string, string> = {
  "companion-chat": "对话",
  "companion-advisor": "早安 · 智能参谋",
  "companion-advisor-transform": "早安 · 智能参谋",
  "companion-briefing-card": "早安 · 卡片",
  "companion-profile": "后台 · 画像识别",
  "companion-email-scheduler": "后台 · 邮件分类",
  "companion-phishing-scan": "后台 · 钓鱼扫描",
  "companion-email-draft": "邮件拟稿",
  "companion-wiki-suggest": "知识库 · 关联建议",
  "plugin:memory-distill": "后台 · 记忆蒸馏",
  "plugin:memory-summarize": "后台 · 对话摘要",
  "plugin:memory-classify": "后台 · 记忆分类",
  "plugin:memory-wiki-analysis": "后台 · 知识库整理",
  "plugin:memory-wiki-generation": "后台 · 知识库整理",
  "plugin:memory-wiki-merge": "后台 · 知识库整理",
  "plugin:toolbridge-meeting": "会议纪要",
  "plugin:catfish-xcatfish-user": "后台 · 界面翻译",
};

// hermes 自己发起、没有外部请求带来源时, 鲶鱼插件按 hermes 的 platform 标 `hermes:<platform>`
const HERMES_PLATFORMS: Record<string, string> = {
  cron: "后台 · 定时任务",
  curator: "后台 · 技能整理",
  subagent: "子任务",
  api_server: "对话",
  webui: "对话",
  tui: "对话 (命令行)",
  cli: "对话 (命令行)",
  weixin: "微信",
  wechat: "微信",
};

/** 审计一行的来源说明。`user` 用来区分没标来源的服务账号调用 (client:xxx) 和员工自己的。 */
export function auditSourceLabel(source: string | undefined, user: string): string {
  const s = (source ?? "").trim();
  if (s) {
    if (LABELS[s]) return LABELS[s];
    if (s.startsWith("hermes:")) {
      const p = s.slice("hermes:".length);
      return HERMES_PLATFORMS[p] ?? `hermes · ${p}`;
    }
    return s;
  }
  // 没标来源: 服务账号 = hermes 内部辅助调用 (会话标题 / 上下文压缩等, 不经过插件打标)
  if (user.startsWith("client:")) return "后台 · hermes 辅助";
  // 员工账号没标来源: 10/22 之前 hermes 主对话 / 微信 / 定时任务都不带来源, 分不出是哪个
  return "未标来源";
}
