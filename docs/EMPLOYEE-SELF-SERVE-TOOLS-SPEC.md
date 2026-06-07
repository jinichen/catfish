# Catfish 员工自助工具集 Spec (10 个按钮)

> **目的**: catfish UI 内**给员工自己用**的工具集, 取代主流 fleet 的 "IT 远程操作" 模式.
>
> **manifesto 公理 4 落地**: 数据 / 配置 / 升级 / 诊断这些操作, IT 中央**物理上**做不到 (没有"远程触发 Tauri command"的 API), 只有员工自己在 UI 点按钮才会执行.
>
> **跟 ADVISORY-FEED-SPEC.md 配合**: advisory feed 是 "中央 publish 建议", 自助工具是 "员工自己执行". 两者完整取代 fleet push.
>
> **目标读者**: 前端 (React) + Rust 后端 (Tauri command) + 测试 (E2E)

---

## 0. 总览

10 个按钮分 3 个 tier, 按优先级:

| Tier | 主题 | 按钮数 | 优先级 |
|---|---|---|---|
| A | 数据主权 | 4 | P0 (3 个月内必 ship) |
| B | 自助运维 | 3 | P1 (6 个月内 ship) |
| C | 账户 / 配置 | 3 | P2 (12 个月内 ship) |

**总工程量**: 10-13 周 (1 工程师), 取决于 BYO key 集成深度.

**brand 一致性**: 全套走 `globals.css` 已有 class (E5 + E7 改造的 `.approval-banner__*` / `.dashboard-skills__*` / `.install-dialog__*`). 不新建 design system, 复用即可.

---

## Tier A: 数据主权类 (P0, 4 个)

### A1. 「重置我的所有 catfish 数据」

#### 触发场景

- 员工离职前 (BYOD 合同要求 wipe)
- 员工换电脑前
- 员工想清空重来 (debug / 实验)

#### UI 位置

`设置` → `数据 & 隐私` → `危险区` → 红色按钮

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ ⚠ 危险区                                                │
│                                                          │
│ 重置我的所有 catfish 数据                               │
│ 删除你本机所有 catfish 内容 — 对话历史, 录屏, wiki,    │
│ 已装 skill, 配置. 中央服务的 metering 历史 metadata    │
│ 保留 (无法本机操作中央). 此操作不可恢复.                │
│                                                          │
│                              [取消] [重置...]            │
└──────────────────────────────────────────────────────────┘
```

点 `[重置...]` 后弹二次 confirm:

```
┌──────────────────────────────────────────────────────────┐
│ ⚠ 真要清空 catfish?                                     │
│                                                          │
│ 这将永久删除:                                            │
│  • 全部对话历史 (1247 条)                               │
│  • 全部录屏 (8 个, 246MB)                               │
│  • 全部 wiki 笔记 (45 篇)                               │
│  • 已装 skill (12 个, 含 3 个员工自录)                  │
│  • 本机配置 + advisory 状态                              │
│                                                          │
│ 输入 "我确认" 继续:                                      │
│ [____________]                                           │
│                                                          │
│                              [取消] [确认重置]           │
└──────────────────────────────────────────────────────────┘
```

`[确认重置]` 在用户输入 "我确认" 之前 disabled.

#### Tauri command

```rust
// edge/companion-app/src-tauri/src/commands/self_serve.rs
#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ResetSummary {
    pub conversations_deleted: u64,
    pub recordings_deleted: u64,
    pub recording_bytes_freed: u64,
    pub wiki_files_deleted: u64,
    pub skills_deleted: u64,
    pub bytes_freed_total: u64,
}

#[tauri::command]
pub async fn self_serve_preview_reset() -> Result<ResetSummary, String> {
    // 不删, 只 preview 数量给 confirmation dialog 显
    tokio::task::spawn_blocking(preview_reset_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}

#[tauri::command]
pub async fn self_serve_execute_reset(
    confirmation: String,
) -> Result<ResetSummary, String> {
    if confirmation != "我确认" {
        return Err("二次确认字符串不匹配".into());
    }
    tokio::task::spawn_blocking(execute_reset_blocking)
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
```

#### 实现逻辑

```rust
fn execute_reset_blocking() -> Result<ResetSummary, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let catfish_dir = home.join(".catfish");

    // 0. preview 数量 (再算一遍, 跟 confirmation 内容 sync)
    let summary = preview_reset_blocking()?;

    // 1. 关 SQLite connection (catfish-gateway / hermes daemon 应该没在跑, 或先 stop)
    //    BL: 跟 daemon stop / restart hook 集成

    // 2. 移到 trash (不真删, 7 天 grace period 内可恢复)
    let trash_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)?.as_secs();
    let trash_dir = home.join(".catfish-reset-trash").join(format!("{trash_ts}"));
    std::fs::create_dir_all(&trash_dir)?;
    std::fs::rename(&catfish_dir, trash_dir.join("catfish"))?;

    // 3. 触发 daemon restart (catfish 重启后 ~/.catfish/ 是空的, 跟首次安装一样)
    //    BL: 跟 services/watchdog.rs 配合

    Ok(summary)
}
```

#### 边界 case

- catfish-gateway daemon 还在跑 → 先 graceful stop daemon, 再 rename, 再 restart
- `~/.catfish/` 不存在 → 直接 return summary (0, 0, 0)
- 磁盘满 (trash 不能存)  → 不能 rename, 弹错误 "磁盘空间不足, 请先清理"
- 用户 7 天后想恢复? → 提供 `self_serve_restore_from_trash(trash_ts)` (可选, BL)

#### 测试

```rust
#[test]
fn test_reset_requires_correct_confirmation() {
    let r = block_on(self_serve_execute_reset("wrong".into()));
    assert!(r.is_err());
}

#[test]
fn test_reset_moves_to_trash_not_delete() {
    setup_catfish_dir_with_data();
    let r = block_on(self_serve_execute_reset("我确认".into())).unwrap();
    assert!(r.bytes_freed_total > 0);
    // 验证 trash 里有数据
    let trash = home_dir().unwrap().join(".catfish-reset-trash");
    assert!(trash.exists());
}
```

---

### A2. 「导出我的所有数据 (备份)」

#### 触发场景

- 员工换电脑前
- 员工离职想合法带走自己数据
- 员工定期备份

#### UI 位置

`设置` → `数据 & 隐私` → `数据导出` → `导出全部数据`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 数据导出                                                │
│                                                          │
│ 你的全部 catfish 数据是你的, 可以随时打包带走.         │
│                                                          │
│ 包含:                                                    │
│  ☑ 对话历史 (1247 条)                                   │
│  ☑ 录屏 (8 个, 246MB)                                   │
│  ☑ wiki 笔记 (45 篇)                                    │
│  ☑ 自录 skill (3 个)                                    │
│  ☑ 配置 + advisory 状态                                  │
│  ☐ 已装 skill (从中央 marketplace 装的, 一般不用打包)  │
│                                                          │
│ 输出格式: ZIP                                            │
│ 估算大小: ~290MB                                         │
│                                                          │
│                              [取消] [导出到...]          │
└──────────────────────────────────────────────────────────┘
```

点 `[导出到...]` 调系统 file picker, 默认文件名 `catfish-export-2026-06-07.zip`.

#### Tauri command

```rust
#[derive(serde::Deserialize)]
pub struct ExportOptions {
    pub include_conversations: bool,    // 默认 true
    pub include_recordings: bool,        // 默认 true
    pub include_wiki: bool,              // 默认 true
    pub include_my_skills: bool,         // 默认 true
    pub include_installed_skills: bool,  // 默认 false (中央装的, 重新装就行)
    pub include_config: bool,            // 默认 true
}

#[tauri::command]
pub async fn self_serve_export_data(
    options: ExportOptions,
    output_path: String,
) -> Result<u64, String> {
    // 返导出的 byte 数
    tokio::task::spawn_blocking(move || export_blocking(options, output_path))
        .await
        .map_err(|e| format!("内部错误: {e}"))?
}
```

#### 实现逻辑

```rust
fn export_blocking(options: ExportOptions, output_path: String) -> Result<u64, String> {
    let home = home_dir().ok_or("找不到 HOME")?;
    let catfish_dir = home.join(".catfish");

    let file = std::fs::File::create(&output_path)?;
    let mut zip = zip::ZipWriter::new(file);

    // 加 manifest 文件 (说明 export 出处 + 时间 + 包含什么)
    let manifest = serde_json::json!({
        "format": "catfish-export-v1",
        "exported_at": Utc::now().to_rfc3339(),
        "catfish_version": env!("CARGO_PKG_VERSION"),
        "options": options,
    });
    zip.start_file("MANIFEST.json", Default::default())?;
    zip.write_all(manifest.to_string().as_bytes())?;

    let mut total_bytes = 0u64;
    if options.include_conversations {
        total_bytes += zip_dir(&mut zip, &catfish_dir.join("conversations"), "conversations/")?;
    }
    if options.include_recordings {
        total_bytes += zip_dir(&mut zip, &catfish_dir.join("recordings"), "recordings/")?;
    }
    if options.include_wiki {
        total_bytes += zip_dir(&mut zip, &catfish_dir.join("wiki"), "wiki/")?;
    }
    if options.include_my_skills {
        total_bytes += zip_dir(&mut zip, &catfish_dir.join("skills"), "skills/")?;
    }
    if options.include_installed_skills {
        let hermes_skills = home.join(".hermes").join("skills");
        total_bytes += zip_dir(&mut zip, &hermes_skills, "hermes_skills/")?;
    }
    if options.include_config {
        total_bytes += zip_file(&mut zip, &catfish_dir.join("config.yaml"), "config.yaml")?;
    }

    zip.finish()?;
    Ok(total_bytes)
}
```

#### 边界 case

- output_path 已存在 → 弹覆盖确认
- 磁盘空间不足 → 显示错误 + 估算大小
- 录屏 246MB 时间长 → 显进度条 (Tauri event `export-progress`)

---

### A3. 「从备份导入 / 还原」

#### 触发场景

- 员工换电脑后想导入
- 员工 A1 重置后悔了想恢复

#### UI 位置

`设置` → `数据 & 隐私` → `数据导入`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 数据导入                                                │
│                                                          │
│ 从之前导出的 catfish-export-*.zip 还原数据.             │
│                                                          │
│ 选择文件: [catfish-export-2026-06-07.zip    ] [选择...] │
│                                                          │
│ 文件验证: ✓ catfish-export-v1, 2026-06-07 导出         │
│   包含: conversations, recordings, wiki, skills, config │
│                                                          │
│ 冲突策略:                                                │
│   ○ 合并 — 现有数据保留, 不冲突的新内容追加            │
│   ● 覆盖 — 清空现有数据, 全部替换 (类似重置 + 导入)    │
│   ○ 跳过冲突 — 现有数据保留, 冲突的新内容跳过          │
│                                                          │
│                              [取消] [导入]               │
└──────────────────────────────────────────────────────────┘
```

#### Tauri command

```rust
#[derive(serde::Deserialize)]
pub enum ConflictStrategy {
    Merge,
    Overwrite,
    SkipConflicts,
}

#[tauri::command]
pub async fn self_serve_import_validate(
    zip_path: String,
) -> Result<ImportPreview, String> {
    // 验证 zip + 返 manifest + 估算 conflict count
}

#[tauri::command]
pub async fn self_serve_import_execute(
    zip_path: String,
    strategy: ConflictStrategy,
) -> Result<ImportSummary, String> {
    // 真执行 import
}
```

#### 边界 case

- zip 不是 catfish-export 格式 → reject + 错误信息
- catfish version mismatch (export from 0.16, import to 0.15) → warn 但允许
- 磁盘空间不足 → reject

---

### A4. 「查看我跟 catfish 中央服务的数据交换日志」⭐

这是 catfish 真 transparency 的体现, 让员工**自己审计** catfish 是否符合数据零出端.

#### 触发场景

- 员工想验证 catfish 真的没偷传数据
- 员工 / IT 等保审计需要"举证 catfish 中央上传了什么"
- 员工排查 "我每月用量这么少, 为啥 catfish 一直网络活动?"

#### UI 位置

`设置` → `隐私` → `我的数据外发记录` (顶部 prominent 位置)

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 我的数据外发记录                              [导出]    │
│                                                          │
│ 完整记录 catfish 客户端发往中央服务的每个 HTTP 请求.   │
│ 你可以审计 catfish 是否真符合"数据零出端"承诺.         │
│                                                          │
│ ⏱ 时间筛选: [今天 ▼]   类型: [全部 ▼]   [搜索]        │
│                                                          │
│ ─────────────────────────────────────────────────────── │
│ 2026-06-07 10:23:15 (5 分钟前)                          │
│ POST https://catfish.example.com/v1/metering/usage      │
│ Status: 200 OK · 234 bytes ↑ · 89 bytes ↓             │
│                                                          │
│ Payload (你可以查看具体发了什么):                       │
│ ```json                                                  │
│ {                                                        │
│   "user_email": "you@company.com",                      │
│   "model": "qwen-3-32b",                                │
│   "tokens_in": 1240,                                    │
│   "tokens_out": 870,                                    │
│   "cost_rmb": 0.034,                                    │
│   "ts": "2026-06-07T10:23:10Z"                          │
│ }                                                        │
│ ```                                                      │
│                                                          │
│ ✓ 不含: prompt 内容, response 内容, tool 调用参数,    │
│   tool 结果, 屏幕录制. 跟 manifesto 公理 2 一致.       │
│                                                          │
│                                              [展开/收起] │
│ ─────────────────────────────────────────────────────── │
│ 2026-06-07 10:18:00 (10 分钟前)                         │
│ GET https://catfish.example.com/advisory/feed.json      │
│ Status: 304 Not Modified · 0 bytes ↑ · 0 bytes ↓      │
│                                                          │
│ Payload: (无)                                            │
│                                              [展开/收起] │
│ ─────────────────────────────────────────────────────── │
│ ...                                                      │
│                                                          │
│ 今日总计: 27 个请求, 共 8.2 KB 上行 / 3.4 KB 下行     │
└──────────────────────────────────────────────────────────┘
```

#### 实现核心 — Rust HTTP client transparent log

```rust
// edge/companion-app/src-tauri/src/services/transparent_log.rs
//
// 所有发往 catfish 中央服务的 HTTP 请求, 在客户端 transparent 记录 (本机 SQLite).
//
// 这跟 SDK / middleware 一起做 — 任何走 catfish_central_http_client 的请求都自动记录.

pub struct TransparentHttpClient {
    inner: reqwest::Client,
    log: Arc<TransparentLog>,
}

impl TransparentHttpClient {
    pub async fn post(&self, url: &str, body: impl Serialize) -> reqwest::Result<reqwest::Response> {
        let body_bytes = serde_json::to_vec(&body)?;
        let body_str = String::from_utf8_lossy(&body_bytes).to_string();

        let req_at = Utc::now();
        let resp = self.inner.post(url).body(body_bytes.clone()).send().await?;
        let resp_at = Utc::now();

        // 记录请求 + 响应 metadata + 完整 payload
        self.log.record(TransparentLogEntry {
            ts_request: req_at,
            ts_response: resp_at,
            method: "POST".into(),
            url: url.into(),
            request_bytes: body_bytes.len() as u64,
            response_bytes: resp.content_length().unwrap_or(0),
            status: resp.status().as_u16(),
            request_payload_preview: truncate(body_str, 4096),  // 防 4KB+ payload 撑爆
            request_payload_full_path: store_full_payload_if_large(&body_bytes),
        }).await;

        Ok(resp)
    }
}
```

#### Tauri command

```rust
#[tauri::command]
pub async fn self_serve_get_outbound_log(
    since: Option<String>,  // ISO 8601, default = 今天
    kind: Option<String>,    // metering | advisory | identity | all
    limit: Option<u32>,      // default = 100
) -> Result<Vec<OutboundLogEntry>, String> {
    // 从本机 SQLite 拉
}

#[tauri::command]
pub async fn self_serve_export_outbound_log(
    output_path: String,
    since: Option<String>,
) -> Result<u64, String> {
    // 导出 CSV / JSON 给员工存证
}
```

#### 关键设计

- **客户端是 catfish 全部 outbound 唯一 source of truth** — 中央服务再"诚实"也是单方面的, 客户端 transparent log 是审计基础
- payload 完整 (不脱敏) — 员工能看到 catfish 真的发了什么
- log 留员工本机, 中央不知道员工查没查 (跟 manifesto 公理 2/4 一致)
- 跟"数据零出端" 承诺 **直接验证** — 员工看 payload 没 prompt / response 内容就是 100% 信任的基础

#### 边界 case

- 日 log 超大 (重度用户) → 按天分表, 自动清理 90 天前
- catfish 离线时 → log 仍记录失败的请求 (本机)
- 不同 catfish 进程 (catfish-gateway / tool-bridge / companion-app) 都走同一份 log

---

## Tier B: 自助运维类 (P1, 3 个)

### B1. 「报告这个 bug」

#### 触发场景

- catfish 崩了 / 出 bug
- 员工想给 IT / catfish 团队报问题

#### UI 位置

- 帮助菜单 → `报告 bug`
- 崩溃后的恢复对话框 (自动弹)

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 报告 bug                                                │
│                                                          │
│ 帮我们改进 catfish — 你的报告对所有员工都有帮助.        │
│                                                          │
│ 包含什么:                                                │
│  ☑ 崩溃 minidump (无你的对话内容)                       │
│  ☑ 最近 100 行错误日志 (PII 已脱敏)                    │
│  ☑ catfish 版本 + 系统信息                              │
│  ☐ 屏幕截图 (可选, 你确认后才附)                       │
│  ☐ 我跟 catfish 中央的最近 10 个请求 log (可选)        │
│                                                          │
│ 描述你遇到的问题 (可选):                                │
│ ┌────────────────────────────────────────────────────┐ │
│ │                                                    │ │
│ └────────────────────────────────────────────────────┘ │
│                                                          │
│ ⚠ 默认不上报. 你点 [发送] 后才发到 IT.                  │
│                                                          │
│                              [取消] [发送]               │
└──────────────────────────────────────────────────────────┘
```

#### Tauri command

```rust
#[derive(serde::Deserialize)]
pub struct BugReportOptions {
    pub include_minidump: bool,
    pub include_logs: bool,
    pub include_system_info: bool,
    pub include_screenshot: bool,
    pub include_outbound_log: bool,
    pub description: String,
}

#[tauri::command]
pub async fn self_serve_generate_bug_report(
    options: BugReportOptions,
) -> Result<BugReportPreview, String> {
    // 生成 preview (不发), 让员工看再决定
}

#[tauri::command]
pub async fn self_serve_send_bug_report(
    report_path: String,
) -> Result<String, String> {
    // 上报 catfish 中央 (上报 endpoint 严格脱敏)
}
```

#### PII 脱敏

```rust
fn scrub_pii(log_line: &str) -> String {
    let log_line = EMAIL_REGEX.replace_all(log_line, "<email>");
    let log_line = IP_REGEX.replace_all(&log_line, "<ip>");
    let log_line = PATH_REGEX.replace_all(&log_line, "<path>");  // /Users/xxx/ → /Users/<user>/
    let log_line = TOKEN_REGEX.replace_all(&log_line, "<token>");
    log_line.to_string()
}
```

---

### B2. 「检查更新 / 回滚到上一版」

#### 触发场景

- 员工想升级
- 新版本不稳定, 员工想回滚

#### UI 位置

`设置` → `关于 catfish` → 更新区

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ catfish 0.15.2 (你的当前版本)                           │
│                                                          │
│ 最新版本: 0.15.3 (3 天前发布) [查看更新内容]           │
│                                                          │
│ ☑ 自动检查更新 (每天)                                   │
│ ☑ 后台自动下载 (装时弹通知)                             │
│                                                          │
│ [检查更新] [立即升级 0.15.3] [跳过此版本]               │
│                                                          │
│ ─────────────────────────────────────────────────────── │
│ 历史版本 (本机保留 3 个最近版本可回滚)                  │
│                                                          │
│ • 0.15.2 (当前)                                          │
│ • 0.15.1 (上一版, 2026-05-30 装)         [回滚到这]    │
│ • 0.15.0                                  [回滚到这]    │
└──────────────────────────────────────────────────────────┘
```

#### Tauri command

跟 E7 phase 2 的 `installSkillFromUrl` 同思路, 但走 catfish auto-updater (tauri-plugin-updater):

```rust
#[tauri::command]
pub async fn self_serve_check_update() -> Result<UpdateCheck, String>;

#[tauri::command]
pub async fn self_serve_apply_update(version: String) -> Result<(), String>;

#[tauri::command]
pub async fn self_serve_rollback_to(version: String) -> Result<(), String>;

#[tauri::command]
pub async fn self_serve_list_local_versions() -> Result<Vec<VersionInfo>, String>;
```

---

### B3. 「查看 / 处理安全 advisory」

#### 触发场景

- 顶部 banner 点 "详情"
- 员工主动检查 advisory 列表

#### UI 位置

顶部菜单 → `安全` → `advisory`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 安全 advisory                                           │
│                                                          │
│ 🔴 高危 (1)                                              │
│ ─────────────────────────────────────────────────────── │
│ eis-login v0.1.0 SSO token 泄漏漏洞                     │
│ CATFISH-ADV-2026-007 · 2 天前发布                       │
│                                                          │
│ 在特定输入下, skill 会把 SSO token 写到日志...           │
│                                                          │
│ 推荐: 立即卸载并升级到 v0.2.0                            │
│                                                          │
│ [立即处理] [详情] [稍后提醒 24h] [忽略]                 │
│                                                          │
│ 🟡 中等 (2)                                              │
│ ─────────────────────────────────────────────────────── │
│ catfish 0.15.2 安全更新 ...                              │
│ ...                                                      │
│                                                          │
│ ⚫ 信息 (5)                                              │
│ ─────────────────────────────────────────────────────── │
│ 钉钉 SSO 接入指南 ...                                   │
│ ...                                                      │
└──────────────────────────────────────────────────────────┘
```

#### Tauri command

跟 ADVISORY-FEED-SPEC §4 配套 — 客户端 advisory_local_state 表读 + ack 上报.

---

## Tier C: 账户 / 配置类 (P2, 3 个)

### C1. 「解绑 / 重新绑定 SSO 账户」

#### 触发场景

- 员工换公司账户 (从 a@A.com 切换 b@B.com)
- 员工换部门
- 离职后重新绑定个人 catfish

#### UI 位置

`设置` → `账户`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 账户                                                    │
│                                                          │
│ 已绑定: you@company.com (Tech 部门)                    │
│ SSO 类型: 钉钉 OAuth                                    │
│ 有效期: 2026-07-07 (剩 30 天)                            │
│                                                          │
│ [刷新 token] [解绑] [切换账户]                          │
│                                                          │
│ ⚠ 解绑不会删除你的本机 catfish 数据.                   │
│   只是清掉中央 SSO 凭证.                                │
└──────────────────────────────────────────────────────────┘
```

#### Tauri command

```rust
#[tauri::command]
pub async fn self_serve_unbind_sso() -> Result<(), String>;

#[tauri::command]
pub async fn self_serve_rebind_sso(provider: String) -> Result<(), String>;
```

---

### C2. 「绑定我的私人 LLM API key (BYO key)」

#### 触发场景

- 公司 quota 超 → 员工想用自己的 OpenAI key fallback
- 员工想用一个公司没接入的 model (e.g. Cursor Pro 包月)
- 员工私人项目想用 catfish

#### UI 位置

`设置` → `LLM`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ LLM Provider                                            │
│                                                          │
│ 公司 LLM (默认):                                         │
│   你的配额: 1.2M / 2M tokens (本月)                     │
│   超额策略: ● fallback 私人 key ○ 阻止使用              │
│                                                          │
│ 我的私人 LLM key (BYO):                                  │
│ ─────────────────────────────────────────────────────── │
│ Anthropic              [sk-ant-... 已绑]      [移除]   │
│ OpenAI                 [sk-...     已绑]      [移除]   │
│ DeepSeek               [未绑定]               [添加]   │
│ Qwen (阿里云)          [未绑定]               [添加]   │
│                                                          │
│ ⚠ 私人 key 仅留你本机 (catfish keychain).               │
│   公司 IT 看不到, 中央服务也不知道你有.                 │
│                                                          │
│                                            [保存]       │
└──────────────────────────────────────────────────────────┘
```

#### 关键设计

- 私人 key 用 OS keychain 存 (Tauri keyring crate) — 跟现有 SSO token 同存储
- BYO key 走员工本机 catfish-tool-bridge / direct API call, **绕过中央 gateway**
- 公司 quota 超时 → catfish 自动 prompt "切换私人 key?"

#### Tauri command

```rust
#[tauri::command]
pub async fn self_serve_set_byo_key(
    provider: String,  // "anthropic" | "openai" | "deepseek" | "qwen"
    api_key: String,
) -> Result<(), String>;

#[tauri::command]
pub async fn self_serve_remove_byo_key(provider: String) -> Result<(), String>;

#[tauri::command]
pub async fn self_serve_list_byo_providers() -> Result<Vec<BYOProviderInfo>, String>;
```

---

### C3. 「生成合规审计摘要」

#### 触发场景

- 等保 / 内审需要时
- 员工自证 "我过去 90 天没用 catfish 干违规事"

#### UI 位置

`设置` → `合规` → `生成审计摘要`

#### UI mockup

```
┌──────────────────────────────────────────────────────────┐
│ 合规审计摘要                                            │
│                                                          │
│ 生成你过去 N 天 catfish 使用的统计摘要 (脱敏, 不含具体 │
│ 对话内容). 适合配合等保 / 内审 / 法律调查.              │
│                                                          │
│ 时间范围: [最近 90 天 ▼]                                │
│                                                          │
│ 包含:                                                    │
│  ☑ LLM 调用次数 + token + 模型分布                      │
│  ☑ 使用的 tool 列表 + 调用次数 (不含 args)              │
│  ☑ 装过的 skill 列表 (含教学/审定标识)                  │
│  ☐ dangerous_command 调用清单 (含简要原因)              │
│  ☐ MCP 连接记录                                          │
│                                                          │
│ 输出格式: ○ PDF ● Excel ○ JSON                          │
│                                                          │
│                              [取消] [生成]               │
└──────────────────────────────────────────────────────────┘
```

#### 关键设计

- 全本机生成, 中央不知道员工生成了摘要
- 摘要**纯统计 + 元数据**, 不含原始对话内容
- 员工自愿提供给 IT / HR / 法务

---

## 各按钮总览表

| # | Tier | 按钮 | UI 位置 | Tauri command 数 | 工程量 |
|---|---|---|---|---|---|
| A1 | A | 重置我的所有 catfish 数据 | 设置 → 危险区 | 2 (preview + execute) | 1 周 |
| A2 | A | 导出我的所有数据 | 设置 → 数据 | 1 (export_data) | 1 周 |
| A3 | A | 从备份导入 | 设置 → 数据 | 2 (validate + execute) | 1 周 |
| A4 | A | 查看数据外发日志 ⭐ | 设置 → 隐私 | 2 (get + export) + transparent_log infra | 2 周 |
| B1 | B | 报告 bug | 帮助 / 崩溃恢复对话框 | 2 (generate + send) | 1 周 |
| B2 | B | 检查更新 / 回滚 | 设置 → 关于 | 4 (check/apply/rollback/list) | 1.5 周 (含 auto-updater 集成) |
| B3 | B | advisory list | 顶部菜单 → 安全 | 跟 ADVISORY-FEED-SPEC 一起 | 跟 advisory 一起 |
| C1 | C | 解绑 / 重绑 SSO | 设置 → 账户 | 2 (unbind + rebind) | 0.5 周 |
| C2 | C | BYO LLM key | 设置 → LLM | 3 (set/remove/list) | 2 周 (含 gateway 协议改) |
| C3 | C | 合规审计摘要 | 设置 → 合规 | 1 (generate_audit_summary) | 1.5 周 |

**总工程量: 11.5 周 (1 工程师)**, 取决于 BYO key 跟 gateway 集成深度.

---

## 实施 3 Phase

### Phase 1: Tier A 数据主权类 (3-4 周)

最重要的 4 个按钮, 直接落地 manifesto 公理 1 (员工主权):
- A1 重置 (1 周)
- A2 导出 (1 周)
- A3 导入 (1 周)
- A4 数据外发日志 ⭐ (2 周, 含 transparent log infra)

**验收**: 员工能完整管理自己 catfish 数据, 不需要 IT.

### Phase 2: Tier B 自助运维类 (2.5 周)

50+ 人规模必备:
- B1 报告 bug (1 周)
- B2 检查更新 / 回滚 (1.5 周)
- B3 advisory list (跟 ADVISORY-FEED-SPEC §5 一起 ship)

**验收**: catfish 出 bug, 员工自己能解决.

### Phase 3: Tier C 账户配置类 (4 周)

商业化必备:
- C1 解绑 / 重绑 SSO (0.5 周)
- C2 BYO LLM key (2 周, 跟 catfish-gateway quota 协议改配合)
- C3 合规审计摘要 (1.5 周)

**验收**: 员工有完整自主权, 不依赖 IT 任何配置.

---

## CSS / Brand 一致性

复用 E5 + E7 已有 class:

| 用途 | class |
|---|---|
| 设置 page 卡片容器 | `.dashboard-skills` (微调) |
| 危险按钮 (重置 / 删除) | `.approval-banner__btn-deny` |
| 主操作按钮 (导出 / 升级) | `.approval-banner__btn-primary` |
| 次要按钮 (取消) | `.approval-banner__btn-link` |
| 输入框 | `.install-dialog__input` |
| Modal 对话框 | `.install-dialog__backdrop` + `.install-dialog` |
| Spinner | `.install-dialog__spinner` |
| 提示 hint | `.dashboard-skills__hint` |
| Toast (操作成功 / 失败) | `.undo-toast` (微调) |

**不新建** design system, 复用即可. brand 一致性是 catfish 整套 UI 的核心特征.

---

## 测试

### Unit test (Rust)

每个 Tauri command 一个 unit test:
- preview / execute 分离的 (A1) 验证 preview 不动数据
- export / import 相反验证 (A2/A3) 验证 round-trip 完整
- transparent log (A4) 验证 PII 不泄漏 + payload 完整

### Integration test (Playwright)

10 个 button 各一个 E2E:
- 点 button → 弹对话框 → 输入二次确认 → 验证 command 调用 → 验证 UI 反馈
- 错误 case: 磁盘满 / 网络断 / SSO 过期

---

## 跟 manifesto / patent / moat 联动

### Manifesto

| Manifesto 公理 | 哪个按钮直接体现 |
|---|---|
| 1. 员工主权 | A1, A2, A3, C3 (员工 own + 自由 export/import + 审计) |
| 2. 数据零出端 | A4 ⭐ (员工自己审计中央交换数据) |
| 3. 中央 0 控制 | A1 (员工自己 wipe, 不是 IT 远程), C1 (解绑自主), B2 (升级自主) |
| 4. API 物理无能 | A4 + B1 + C3 (员工自助生成 audit / 数据 / 报错, 中央无法主动获取) |

### Patent

A4 (数据外发日志) + manifesto 公理 4 可以作 patent 1 的 dependent claim 加进去:

> "...所述中央服务的全部 outbound 请求由客户端 transparent 记录到员工本机审计 log, 员工可随时查看 / 导出该 log 以审计中央服务实际接收数据是否符合声明..."

这把 patent 1 从 "我们承诺不传内容" 升级到 "**员工自己能审计验证**" — 不可篡改的工程证据.

### Moat

- A4 + C2 (BYO key) 是 catfish 的真"员工主权" 体现, Anthropic / OpenAI Desktop 都不会做 (砍他们 quota 控制权)
- C3 (合规摘要) 替代主流 fleet 的"IT 远程调取" — 走员工自助 + voluntary disclosure
- A1 + A2 + A3 是 BYOD 哲学的产品落地, 跟劳动合同配套 (manifesto 第 6 节)

---

## 报告结尾

10 个按钮分 3 个 Phase, 总工程量 11.5 周. 按 Phase 1 → 2 → 3 顺序 ship.

ship 完 Phase 1 (4 周), catfish 就**正式有了"员工主权"产品形态** — 不再只是哲学声明, 是用户能看见摸得着的 button.

— spec 完
