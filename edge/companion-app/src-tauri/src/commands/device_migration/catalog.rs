//! 换机迁移: 本机数据目录里每一项归哪一类 (docs/DEVICE-MIGRATION-DESIGN.md §4)。
//!
//! 只看两个根目录的**第一层**名字: `~/.catfish/` 和 Hermes 目录。归到哪类, 整项 (文件或整个目录)
//! 就按哪类处理。新增的数据文件没在这里登记 → 归「未归类」, 导出时列出来提醒, **不悄悄带走、
//! 也不悄悄丢下** —— 以后谁加了新文件, 看到提醒就来这里补一行。

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Class {
    /// 员工的积累, 丢了找不回: 必带
    Required,
    /// 可能很大, 员工勾选才带 (附件 / 生成的文件 / 录屏 …)
    Optional,
    /// 必带, 但导入时**不覆盖**新机已有的同名文件 (Hermes skills: 新装自带的内置技能以新机为准)
    Merge,
    /// 派生数据 / 缓存 / 安装产物: 不带, 新机自己重建或重装
    Rebuild,
    /// 凭证: 刻意不带 (包丢了也不泄露账号), 新机重新登录 / 授权
    Secret,
    /// 没登记过的: 不带, 列出来提醒
    Unclassified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Root {
    Catfish,
    Hermes,
}

impl Root {
    pub fn dir_name(self) -> &'static str {
        match self {
            Root::Catfish => "catfish",
            Root::Hermes => "hermes",
        }
    }
    pub fn parse(s: &str) -> Option<Root> {
        match s {
            "catfish" => Some(Root::Catfish),
            "hermes" => Some(Root::Hermes),
            _ => None,
        }
    }
}

enum Pat {
    Exact(&'static str),
    Prefix(&'static str),
    Suffix(&'static str),
    Contains(&'static str),
}

impl Pat {
    fn hit(&self, name: &str) -> bool {
        match self {
            Pat::Exact(s) => name == *s,
            Pat::Prefix(s) => name.starts_with(s),
            Pat::Suffix(s) => name.ends_with(s),
            Pat::Contains(s) => name.contains(s),
        }
    }
}

use Class::*;
use Pat::*;

/// `~/.catfish/` 第一层。按顺序匹配, 先中先得 (所以具体的写前面)。
const CATFISH: &[(Pat, Class, &str)] = &[
    // ── 各种手工 / 升级时留下的旧备份 (放最前: `employee_journal.md.backup.*` 不能被当成日志) ──
    (Contains(".bak"), Rebuild, "旧备份"),
    (Contains(".backup"), Rebuild, "旧备份"),
    (Contains("_backup_"), Rebuild, "旧备份"),
    (Contains("-backups"), Rebuild, "旧备份"),
    (Exact("backups"), Rebuild, "旧备份"),
    (Contains(".broken"), Rebuild, "旧备份"),
    (Contains(".corrupt"), Rebuild, "旧备份"),
    (Contains(".legacy"), Rebuild, "旧备份"),
    (Contains(".pre-"), Rebuild, "旧备份"),
    (Prefix("tmp"), Rebuild, "临时文件"),
    (Prefix("probe"), Rebuild, "临时文件"),
    // ── 凭证: 刻意不带 ──
    (Exact("chrome-profile"), Secret, "浏览器登录状态 (含网站 cookie)"),
    (Exact("feishu.yaml"), Secret, "飞书应用凭证"),
    (Exact("oauth"), Secret, "登录令牌"),
    (Exact("auth"), Secret, "登录令牌"),
    (Exact("mcp"), Secret, "MCP 授权"),
    (Exact("teaching_credentials.json"), Secret, "教学录制里的网站密码"),
    (Exact("hub_signing_key.json"), Secret, "技能发布签名密钥"),
    (Exact("server-ca.pem"), Secret, "服务器证书 (跟单位部署走)"),
    (Exact(".env"), Secret, "密钥"),
    (Exact("device_id"), Secret, "本机设备号 (每台电脑自己的)"),
    // ── 记忆 / 画像 / 日志 ──
    (Prefix("employee_journal"), Required, "工作日志"),
    (Exact("journal"), Required, "工作日志"),
    (Exact("distilled_facts.md"), Required, "提炼出的事实"),
    (Exact("session_facts.json"), Required, "记忆"),
    (Exact("session_meta.json"), Required, "记忆"),
    (Exact("session_goal.txt"), Required, "记忆"),
    (Exact("memory_distill_state.json"), Required, "记忆蒸馏进度"),
    (Exact("memory"), Required, "记忆"),
    (Exact(".catfish_memory_buffer.jsonl"), Required, "待蒸馏的记忆"),
    (Exact(".catfish_memory_state.json"), Required, "记忆蒸馏进度"),
    (Exact("profile.json"), Required, "画像"),
    (Exact("profile_hints.md"), Required, "画像"),
    (Exact("user_profile.json"), Required, "画像"),
    (Exact("style_fingerprint.json"), Required, "文书风格"),
    (Exact("expertise.yaml"), Required, "专长"),
    (Exact("projects.md"), Required, "项目"),
    (Exact("workplan.md"), Required, "工作计划"),
    (Exact("current_todos.md"), Required, "待办"),
    (Exact("todos_archive"), Required, "待办归档"),
    (Exact("journaled_sessions.txt"), Required, "已记入日志的会话"),
    (Exact("notes"), Required, "笔记"),
    (Exact("bookkeep.jsonl"), Required, "记账"),
    // ── 早安 / 待办 / 决策 ──
    (Exact("task_chat"), Required, "早安事项对话"),
    (Exact("tasks.jsonl"), Required, "任务"),
    (Exact("tasks.db"), Required, "任务"),
    (Exact("task_library.db"), Required, "事项库"),
    (Exact("todo_store"), Required, "待办"),
    (Exact("todo_state"), Required, "待办"),
    (Exact("decisions.jsonl"), Required, "决策记录"),
    (Exact("decisions.jsonl.chain.json"), Required, "决策记录"),
    (Exact("advisor_closed.jsonl"), Required, "早安已办"),
    (Exact("advisor_task_state.json"), Required, "早安事项状态"),
    (Exact("situation"), Required, "近况"),
    // ── 知识 / 技能 / 专家 ──
    (Exact("wiki"), Required, "个人知识库"),
    (Exact("wiki_relation_vocab.custom.json"), Required, "知识库关系词"),
    (Exact("wiki_publish_state.json"), Required, "知识库发布状态"),
    (Exact("wiki_ingested_state.json"), Required, "知识库摄取进度"),
    (Exact("wiki_source_progress.json"), Required, "资料入库进度"),
    (Exact("action-registry.yaml"), Required, "知识节点动作"),
    (Exact(".obsidian"), Required, "知识库 Obsidian 设置"),
    (Exact(".obsidianignore"), Required, "知识库 Obsidian 设置"),
    (Exact("README.md"), Required, "说明"),
    (Exact("skills"), Required, "我的技能"),
    (Exact("skill_proposals.jsonl"), Required, "技能建议"),
    (Exact("skill_revisions.jsonl"), Required, "技能修订"),
    (Exact("skill_quality.jsonl"), Required, "技能质量"),
    (Exact("tool_archives"), Required, "工具存档"),
    (Exact("experts"), Required, "专家"),
    (Exact("expert-packs"), Required, "专家包"),
    (Exact("sales"), Required, "销售跟进"),
    (Exact("sales_template.json"), Required, "销售模板"),
    (Exact("meeting-templates"), Required, "会议模板"),
    (Exact("meetings"), Required, "会议记录"),
    (Exact("forms"), Required, "协作填表"),
    (Exact("workflow_drafts"), Required, "工作流"),
    (Exact("workflow_runs"), Optional, "工作流运行记录"),
    // ── 审计 ──
    (Exact("outbound_log.db"), Required, "数据外发记录"),
    (Exact("outbound_log_payloads"), Rebuild, "旧版外发记录内容 (新版已不用)"),
    (Exact("approval_audit.jsonl"), Required, "审批记录"),
    (Exact("memory_audit.jsonl"), Required, "记忆审计"),
    (Exact("skill_audit.jsonl"), Required, "技能审计"),
    (Exact("gateway_audit.jsonl"), Required, "调用审计"),
    (Exact("feedback.jsonl"), Required, "反馈"),
    (Exact("audit"), Required, "审计"),
    // ── 偏好设置 ──
    (Exact("companion.yaml"), Required, "Companion 设置"),
    (Exact("embedding.yaml"), Required, "设置"),
    (Exact("memory_plugin.yaml"), Required, "设置"),
    (Exact("picker_state.json"), Required, "模型选择"),
    (Exact("picker_model"), Required, "模型选择"),
    (Exact("notify_prefs.json"), Required, "提醒设置"),
    (Exact("weather_config.json"), Required, "天气设置"),
    (Exact("search-scope.yaml"), Required, "搜索范围"),
    (Exact("wechat_expert_routes.json"), Required, "微信专家路由 (绑定本身要重新扫码)"),
    (Exact("advisory_state.db"), Required, "通知已读状态"),
    // ── 可选 ──
    (Exact("uploads"), Optional, "上传的附件"),
    (Exact("outputs"), Optional, "生成的文件"),
    (Exact("output"), Optional, "生成的文件"),
    (Exact("exports"), Optional, "导出过的文件"),
    (Exact("strategic_docs"), Optional, "资料文档"),
    (Exact("recordings"), Optional, "教学录制"),
    (Exact("wechat-exports"), Optional, "导入的微信聊天"),
    (Exact("downloads"), Optional, "下载的文件"),
    (Exact("mail_archive"), Optional, "邮件归档"),
    (Exact("conversations"), Optional, "旧版对话文件"),
    // ── 重建 / 缓存 / 安装产物 ──
    (Prefix(".migration-"), Rebuild, "迁移临时文件"),
    (Exact("search.db"), Rebuild, "搜索索引"),
    (Exact("wiki_embeddings.db"), Rebuild, "知识库向量"),
    (Exact("wiki_graph.db"), Rebuild, "知识图谱索引"),
    (Exact("attachments.db"), Rebuild, "附件索引"),
    (Exact("advisor_embed_cache.db"), Rebuild, "缓存"),
    (Exact("advisor_cache.json"), Rebuild, "缓存"),
    (Exact("memory_vectors.db"), Rebuild, "记忆向量"),
    (Exact("task_uid_cache.json"), Rebuild, "缓存"),
    (Exact("skill_names_backfill.json"), Rebuild, "缓存"),
    (Exact("apple_mail_outgoing.json"), Rebuild, "邮件缓存"),
    (Exact("inflight_streams"), Rebuild, "运行状态"),
    (Prefix("last_wiki_"), Rebuild, "运行状态"),
    (Exact(".wiki-integrity-backups"), Rebuild, "旧备份"),
    (Exact(".weekly_reset_last"), Rebuild, "运行状态"),
    (Exact("piper-venv"), Rebuild, "语音组件 (重新下载)"),
    (Exact("piper-voices"), Rebuild, "语音组件 (重新下载)"),
    (Exact("asr-venv"), Rebuild, "语音组件 (重新下载)"),
    (Exact("whisper-models"), Rebuild, "语音组件 (重新下载)"),
    (Exact("cache"), Rebuild, "缓存"),
    (Exact("health"), Rebuild, "运行状态"),
    (Exact("eval"), Rebuild, "开发测试数据"),
    (Exact("scripts"), Rebuild, "开发测试数据"),
    (Exact("captcha-test"), Rebuild, "开发测试数据"),
    // 开发机上本机跑中央端留下的 (客户电脑上没有)
    (Exact("gateway.db"), Rebuild, "开发机中央端数据"),
    (Exact("quota.db"), Rebuild, "开发机中央端数据"),
    (Exact("mcp_registry.db"), Rebuild, "开发机中央端数据"),
    (Exact("identity-server"), Rebuild, "开发机中央端数据"),
    (Prefix("email"), Rebuild, "邮件分诊 / 列表缓存 (新机重新跑)"),
    (Exact("imap-source.json"), Rebuild, "邮箱来源 (新机重新配)"),
    (Exact("wechat_archive.json"), Rebuild, "微信导出文件位置 (本机路径)"),
    (Exact("wiki-shared"), Rebuild, "部门共享知识 (从中央重新拉)"),
    (Exact("models"), Rebuild, "本地模型 (重新下载)"),
    (Exact("meeting-asr"), Rebuild, "会议组件包 (重新下载)"),
    (Prefix("edge-runtime"), Rebuild, "运行环境"),
    (Exact("runtime"), Rebuild, "运行环境"),
    (Exact("venv"), Rebuild, "运行环境"),
    (Exact("bin"), Rebuild, "运行环境"),
    (Exact("companion-state"), Rebuild, "运行状态"),
    (Exact("state"), Rebuild, "运行状态"),
    (Exact("misc"), Rebuild, "运行状态"),
    (Exact("traces"), Rebuild, "诊断记录"),
    (Exact("skill-trash"), Rebuild, "技能回收站"),
    (Exact("skill-staging"), Rebuild, "技能暂存"),
    (Exact(".trash"), Rebuild, "回收站"),
    (Exact(".forget_backup"), Rebuild, "遗忘备份"),
    (Exact("weather_cache.json"), Rebuild, "缓存"),
    (Exact("notify_status.json"), Rebuild, "运行状态"),
    (Exact("pet_pending_bubbles.jsonl"), Rebuild, "运行状态"),
    (Exact("codex-backend-shim.json"), Rebuild, "运行状态"),
    (Exact("migration_v1_done"), Rebuild, "升级标记"),
    (Prefix("a2a_"), Rebuild, "已废弃功能"),
    (Exact(".DS_Store"), Rebuild, "系统文件"),
    (Suffix(".log"), Rebuild, "日志"),
    (Suffix(".sock"), Rebuild, "运行状态"),
    (Suffix(".port"), Rebuild, "运行状态"),
    (Suffix(".pid"), Rebuild, "运行状态"),
    (Suffix(".lock"), Rebuild, "运行状态"),
];

/// Hermes 目录第一层。
const HERMES: &[(Pat, Class, &str)] = &[
    (Contains(".bak"), Rebuild, "旧备份"),
    (Contains(".corrupt"), Rebuild, "旧备份"),
    (Exact("backups"), Rebuild, "旧备份"),
    (Exact(".curator_backups"), Rebuild, "旧备份"),
    (Contains("-backup"), Rebuild, "旧备份"),
    (Exact("plugin-update-checks"), Rebuild, "运行状态"),
    (Exact(".env"), Secret, "密钥 (登录后自动重写)"),
    (Exact("catfish-companion-token"), Secret, "本机令牌"),
    (Exact("pairing"), Secret, "设备配对"),
    (Exact("channel_directory.json"), Secret, "消息平台绑定"),
    (Exact("auth.json"), Secret, "登录令牌"),
    (Exact("weixin"), Secret, "微信绑定 (重新扫码)"),
    (Exact("platforms"), Secret, "消息平台绑定"),
    (Exact("state.db"), Required, "对话历史"),
    (Exact("memories"), Required, "记忆"),
    (Exact("USER.md"), Required, "画像"),
    (Prefix("SOUL"), Required, "小鲶人设"),
    (Exact("cron"), Required, "定时任务"),
    (Exact(".catfish_audit.jsonl"), Required, "审计"),
    (Exact("kanban.db"), Required, "任务看板"),
    (Exact("kanban"), Required, "任务看板"),
    (Exact("sessions.db"), Required, "对话历史"),
    (Exact(".catfish-skills-default-off.json"), Required, "关掉的默认技能"),
    (Exact("skills"), Merge, "技能 (新机自带的以新机为准)"),
    (Exact("sessions"), Optional, "旧版会话文件"),
    (Exact("config.yaml"), Rebuild, "配置 (Companion 按新机重新生成)"),
    (Exact("hermes-agent"), Rebuild, "运行环境"),
    (Exact("skills-src"), Rebuild, "技能源 (Companion 重新同步)"),
    (Exact("response_store.db"), Rebuild, "缓存"),
    (Exact("runs_idempotency.db"), Rebuild, "运行状态"),
    (Exact("spawn-ledger.json"), Rebuild, "运行状态"),
    (Exact("pending_messages"), Rebuild, "运行状态"),
    (Exact("gateway"), Rebuild, "运行状态"),
    (Exact("gateway_state.json"), Rebuild, "运行状态"),
    (Exact("install_id"), Rebuild, "本机安装号"),
    (Exact("state"), Rebuild, "运行状态"),
    (Exact("runtime"), Rebuild, "运行环境"),
    (Exact("bin"), Rebuild, "运行环境"),
    (Exact("node"), Rebuild, "运行环境"),
    (Prefix("python-"), Rebuild, "运行环境"),
    (Exact("lsp"), Rebuild, "运行环境"),
    (Exact("tools"), Rebuild, "运行环境"),
    (Exact("sandboxes"), Rebuild, "运行环境"),
    (Exact("scripts"), Rebuild, "运行环境"),
    (Prefix("models_dev_cache"), Rebuild, "缓存"),
    (Exact("context_length_cache.yaml"), Rebuild, "缓存"),
    (Prefix(".catfish-hermes-"), Rebuild, "安装残留"),
    (Prefix(".fuse_hidden"), Rebuild, "系统临时文件"),
    (Prefix(".models_dev_cache"), Rebuild, "缓存"),
    (Exact(".update_check"), Rebuild, "运行状态"),
    (Exact(".restart_pending.json"), Rebuild, "运行状态"),
    (Prefix(".codex_"), Rebuild, "运行状态"),
    (Suffix(".sock"), Rebuild, "运行状态"),
    (Exact("plugins"), Rebuild, "插件 (Companion 重新同步)"),
    (Exact("profiles"), Rebuild, "专家运行环境 (按 ~/.catfish/experts 重新生成)"),
    (Exact("logs"), Rebuild, "日志"),
    (Exact("installs"), Rebuild, "安装记录"),
    (Exact("cache"), Rebuild, "缓存"),
    (Exact("image_cache"), Rebuild, "缓存"),
    (Exact("audio_cache"), Rebuild, "缓存"),
    (Prefix("catfish-xcatfish-user"), Rebuild, "插件备份"),
    (Exact(".DS_Store"), Rebuild, "系统文件"),
    (Suffix(".log"), Rebuild, "日志"),
    (Suffix(".pid"), Rebuild, "运行状态"),
    (Suffix(".lock"), Rebuild, "运行状态"),
    (Suffix(".bak"), Rebuild, "备份"),
];

/// 归类 + 一句给员工看的说明。
pub fn classify(root: Root, name: &str) -> (Class, &'static str) {
    // SQLite 的旁路文件跟着主库走 (主库做一致性快照, 这些不单独带)
    if is_sqlite_sidecar(name) {
        return (Rebuild, "数据库临时文件");
    }
    let table = match root {
        Root::Catfish => CATFISH,
        Root::Hermes => HERMES,
    };
    table
        .iter()
        .find(|(p, _, _)| p.hit(name))
        .map(|(_, c, why)| (*c, *why))
        .unwrap_or((Unclassified, "没登记过"))
}

pub fn is_sqlite_sidecar(name: &str) -> bool {
    name.ends_with("-wal") || name.ends_with("-shm") || name.ends_with("-journal")
}

pub fn is_sqlite(name: &str) -> bool {
    name.ends_with(".db") || name.ends_with(".sqlite") || name.ends_with(".sqlite3")
}

/// 导出时要不要带 (可选项看员工勾没勾)。
pub fn exported(class: Class, optional_chosen: bool) -> bool {
    match class {
        Required | Merge => true,
        Optional => optional_chosen,
        Rebuild | Secret | Unclassified => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 积累必带_凭证不带_缓存不带() {
        assert_eq!(classify(Root::Catfish, "employee_journal.md").0, Required);
        assert_eq!(classify(Root::Catfish, "outbound_log.db").0, Required);
        assert_eq!(classify(Root::Catfish, "oauth").0, Secret);
        assert_eq!(classify(Root::Catfish, "device_id").0, Secret);
        assert_eq!(classify(Root::Catfish, "mcp").0, Secret);
        assert_eq!(classify(Root::Catfish, "search.db").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "email_phishing.json").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "uploads").0, Optional);
        assert_eq!(classify(Root::Hermes, "state.db").0, Required);
        assert_eq!(classify(Root::Hermes, "SOUL_BROWSER.md").0, Required);
        assert_eq!(classify(Root::Hermes, "skills").0, Merge);
        assert_eq!(classify(Root::Hermes, ".env").0, Secret);
        assert_eq!(classify(Root::Hermes, "config.yaml").0, Rebuild);
    }

    #[test]
    fn 数据库旁路文件不单独带_新文件归未归类() {
        assert_eq!(classify(Root::Hermes, "state.db-wal").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "outbound_log.db-shm").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "brand_new_feature.json").0, Unclassified);
        // 旧备份不能被前缀规则当成正本
        assert_eq!(classify(Root::Catfish, "employee_journal.md.backup.20260629150325").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "employee_journal_archive_2026.md").0, Required);
        assert_eq!(classify(Root::Catfish, "distilled_facts.md.bak-1008").0, Rebuild);
        assert_eq!(classify(Root::Catfish, "chrome-profile").0, Secret);
        assert_eq!(classify(Root::Hermes, "auth.json.bak-1784445367").0, Rebuild);
        assert!(!exported(Unclassified, true));
        assert!(exported(Optional, true) && !exported(Optional, false));
        assert!(exported(Merge, false));
    }
}
