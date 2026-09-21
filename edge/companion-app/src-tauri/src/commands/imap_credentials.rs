//! IMAP 邮箱凭据 —— 密码进系统凭据库, 配置进一份不含密码的 JSON。
//!
//! # 为什么要有 IMAP 这条路 (9/18)
//!
//! 这一天在两条"读客户端本地数据"的路上各撞一次墙:
//!
//!   Foxmail 7.2 (Win)  邮件文件**加密** —— 有数据读不了 (熵 7.96, 无共同前缀)
//!   新版 Outlook       **无 COM 且本地无邮件** —— WebView 套壳, 只有 HTTP 缓存
//!
//! 共同点: 依赖厂商的私有接口/私有格式, 存废不由我们决定。IMAP 是公开协议,
//! 跟客户端版本无关。代价就是这个文件 —— 得持有凭据。
//!
//! # 布局照 teaching_credentials 抄, 因为那套是对的
//!
//!   密码      → 系统凭据库 (macOS 钥匙串 / Windows 凭据管理器)
//!   主机/账号 → `~/.catfish/imap-source.json`, **里面没有密码**
//!
//! keyring 3.6.3 没有枚举 API, 所以"配没配过"这个问题只能靠旁边那份 JSON 回答
//! —— 跟 teaching_credentials 维护标签索引是同一个理由。
//!
//! # 红线: 密码只进不出
//!
//! 保存走 `#[tauri::command]`, 读取**不走**。前端只负责把密码送进来, 不负责
//! 取出去 —— 一旦读取挂上 command, 任何一段前端 JS 都能把邮箱密码要出来。
//! 唯一的消费者是 `commands::email` 那条给 catfish-email 子进程注入环境变量的
//! 路径, 它在 Rust 里直接调 `password_for`。
//!
//! 这条跟 teaching_credentials 里那条是同一条, 不是我新发明的。

// ⚠ 跟 teaching_credentials 同款的平台门控。keyring v3 没开平台 feature 时会
//   `pub use mock as default` —— set_password 返 Ok(()) 而密码只进一个进程内的
//   假存储, 界面显示"已保存"但下次读不出来。8/18 在教学凭据上真撞过, 查了三轮。
//   Linux 故意放行: 产品不发 Linux, 但 CI 在 ubuntu 上编 (`cargo test --no-run`
//   那步跟平台无关却标着必过), 写进 compile_error 会把 CI 直接堵死。
#[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
compile_error!(
    "keyring 只在 macOS / Windows 配了平台后端 (见 Cargo.toml)。当前目标没配 —— \
     编过去的话密码存了等于没存, 而且界面还显示成功。"
);

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[cfg(any(target_os = "macos", target_os = "windows"))]
use keyring::Entry;

/// 凭据库里的服务名。**只有 Windows 用**: 那边是 `Entry::new(SERVICE, target)`,
/// macOS 那边是 `Entry::new(target, account)` —— 两边位置不同, 跟
/// teaching_credentials 的用法一致 (写进去读不出来就白存了)。
///
/// cfg 写成 macos|windows 会在 macOS 上留一条 unused const 警告 —— 编译器逮到的。
#[cfg(target_os = "windows")]
const SERVICE: &str = "catfish";

const DEFAULT_PORT: u16 = 993;

/// 只有没配后端的平台用得到 —— 不 cfg 的话 mac/win 上是个 unused const 警告。
#[cfg(not(any(target_os = "macos", target_os = "windows")))]
const UNSUPPORTED_PLATFORM: &str =
    "本平台没有配置系统凭据库后端 —— IMAP 密码只在 macOS / Windows 可用";

/// 不含密码的那一半。前端读得到这个, 读不到密码。
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ImapSource {
    pub host: String,
    pub user: String,
    #[serde(default = "default_port")]
    pub port: u16,
    /// 发信服务器。空 = 从 IMAP 主机猜 (imap.x → smtp.x), Python 那边
    /// smtp_send.guess_host 负责。
    ///
    /// 9/18: 发信走 **SMTP**, 不是 IMAP —— 另一个端口、另一次认证。猜得对
    /// 的话员工什么都不用填; 猜错了没有这两个字段就**完全没有补救的办法**,
    /// 只能眼看着回复发不出去。
    ///
    /// serde(default) 是必须的: 已经配过 IMAP 的机器上 JSON 里没有这两个键,
    /// 升级后不该直接读不出配置。
    #[serde(default)]
    pub smtp_host: String,
    /// 0 = 用默认 465 (隐式 TLS)。
    #[serde(default)]
    pub smtp_port: u16,

    /// 归档校验通过之后多久把服务器上那份删掉:
    /// `immediate` / `1w` / `2w` / `never`。
    ///
    /// ⚠ 默认 never, 而且 serde(default) 让老配置读出来也是 never。
    /// 删服务器上的邮件**不可逆** —— 升级之后不能因为多了个字段就开始删。
    /// Python 那边认不出来的值同样退回 never 并出声 (imap_config)。
    #[serde(default = "default_retention")]
    pub retention: String,
}

fn default_retention() -> String {
    "never".to_string()
}

fn default_port() -> u16 {
    DEFAULT_PORT
}

impl ImapSource {
    /// 能进日志的形态。这个类型本来就不含密码, 但写出来提醒后来人别加字段。
    pub fn redacted(&self) -> String {
        // 保留策略一起打 —— 它是这套配置里唯一不可逆的一项, 查"邮件怎么
        // 没了"时第一眼就该看到。跟 Python 侧 ImapConfig.redacted 对齐。
        format!("{}@{}:{} (保留策略={})", self.user, self.host, self.port, self.retention)
    }
}

/// 前端问"配好了吗"的答复。**刻意不含密码字段** —— 连 Option<String> 都不给,
/// 免得哪天有人图省事填进去。
#[derive(Debug, Clone, Serialize)]
pub struct ImapStatus {
    pub configured: bool,
    pub host: String,
    pub user: String,
    pub port: u16,
    /// 凭据库里到底有没有那条密码。JSON 在但密码没了是真实状态 ——
    /// 用户清过钥匙串、或者换了机器同步了配置没同步凭据。
    pub password_present: bool,
    /// 发信服务器。空串 = 没显式配过, 发信时从 IMAP 主机猜。
    /// 界面要把这个**如实显示**: 员工得知道回复会从哪台服务器发出去,
    /// 而不是等发失败了才去猜我们猜了什么。
    pub smtp_host: String,
    /// 0 = 用默认 465。
    pub smtp_port: u16,
    /// immediate / 1w / 2w / never —— 界面上那个下拉要回显它。
    pub retention: String,
}

fn config_path() -> Result<PathBuf, String> {
    let home = crate::util::paths::home_env().map_err(|e| format!("读不到 HOME: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish").join("imap-source.json"))
}

fn read_source() -> Option<ImapSource> {
    let path = config_path().ok()?;
    let bytes = std::fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

fn write_source(source: &ImapSource) -> Result<(), String> {
    let path = config_path()?;
    let parent = path.parent().ok_or_else(|| "配置目录无效".to_string())?;
    std::fs::create_dir_all(parent).map_err(|e| format!("创建配置目录失败: {e}"))?;
    let body = serde_json::to_vec_pretty(source).map_err(|e| format!("序列化失败: {e}"))?;
    // 先写临时文件再改名 —— 半截的配置比没有配置更难查
    let temp = path.with_extension("json.tmp");
    std::fs::write(&temp, body).map_err(|e| format!("写配置失败: {e}"))?;
    if let Err(error) = std::fs::rename(&temp, &path) {
        // Windows 的 rename 不能覆盖已有目标
        if path.exists() {
            std::fs::remove_file(&path).map_err(|e| format!("替换旧配置失败: {e}"))?;
            std::fs::rename(&temp, &path)
                .map_err(|e| format!("保存配置失败: {e}; 首次错误: {error}"))?;
        } else {
            return Err(format!("保存配置失败: {error}"));
        }
    }
    Ok(())
}

// ── 凭据库 ────────────────────────────────────────────────

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn entry_for(user: &str) -> Result<Entry, String> {
    let target = format!("imap:{user}");
    #[cfg(target_os = "windows")]
    let entry = Entry::new(SERVICE, &target);
    #[cfg(not(target_os = "windows"))]
    let entry = Entry::new(&target, user);
    entry.map_err(|e| format!("无法打开本机凭据库: {e}"))
}

/// 下面三个是**唯一**碰 keyring 的地方 —— cfg 只出现在它们内部。
///
/// 原来的写法是给 Linux 造一个假 `Entry` 类型来凑签名。那种"为了编译而存在"的
/// 东西最容易在没编过的平台上出错, 而产品不发 Linux、CI 却在 ubuntu 上编。
/// 收成三个小函数之后, 调用方一个 cfg 都不用写。
fn keyring_set(user: &str, password: &str) -> Result<(), String> {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        entry_for(user)?
            .set_password(password)
            .map_err(|e| format!("写入本机凭据库失败: {e}"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = (user, password);
        Err(UNSUPPORTED_PLATFORM.to_string())
    }
}

fn keyring_delete(user: &str) {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    if let Ok(entry) = entry_for(user) {
        // 凭据库里已经没有那条时照样当成功 —— 不然残项永远删不掉
        // (teaching_credentials 踩过这个)。
        let _ = entry.delete_credential();
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let _ = user;
}

/// **唯一的读出口, 而且刻意不是 Tauri command。**
///
/// 前端永远不该拿到邮箱密码。唯一的消费者是 `commands::email` 里给
/// catfish-email 子进程注入 `CATFISH_IMAP_PASSWORD` 的那一步 —— 那是 Rust
/// 内部调用。一旦这里挂上 `#[tauri::command]`, 任何一段前端 JS 都能把密码要出去。
pub fn password_for(user: &str) -> Option<String> {
    #[cfg(any(target_os = "macos", target_os = "windows"))]
    {
        entry_for(user).ok()?.get_password().ok()
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        let _ = user;
        None
    }
}

/// 配好了的 IMAP 来源 (含密码)。给 email.rs 注入环境变量用, 同样不是 command。
pub fn configured_source() -> Option<(ImapSource, String)> {
    let source = read_source()?;
    let password = password_for(&source.user)?;
    Some((source, password))
}

// ── Tauri 命令 (只有"进"和"删", 没有"出") ─────────────────

/// 保存 IMAP 配置。密码进凭据库, 其余进 JSON。
///
/// 保存前先**真连一次**: 配错了当场就该知道, 而不是等下次刷邮件才发现空列表。
/// 今天在 Foxmail 上吃够了"以为配好了其实读不到"的亏。
#[tauri::command]
pub async fn imap_credential_save(
    host: String,
    user: String,
    password: String,
    port: Option<u16>,
    smtp_host: Option<String>,
    smtp_port: Option<u16>,
    retention: Option<String>,
) -> Result<ImapStatus, String> {
    let host = host.trim().to_string();
    let user = user.trim().to_string();
    if host.is_empty() || user.is_empty() {
        return Err("服务器地址和邮箱账号都不能为空".to_string());
    }
    if password.is_empty() {
        return Err("密码/授权码不能为空".to_string());
    }
    let source = ImapSource {
        host,
        user,
        port: port.unwrap_or(DEFAULT_PORT),
        // 空着就让 Python 那边从 IMAP 主机猜 (smtp_send.guess_host)。
        // 这里不替它猜 —— 猜法只该有一处, 两处早晚会漂。
        smtp_host: smtp_host.unwrap_or_default().trim().to_string(),
        smtp_port: smtp_port.unwrap_or(0),
        // 认不出来的一律 never。**这个方向不能反** —— 前端传了个拼错的值
        // 就开始删员工服务器上的邮件, 是不可接受的。Python 那边同样兜一次:
        // 两层都兜是因为这一步不可逆, 而且没有任何后悔的余地。
        retention: match retention.as_deref().map(str::trim) {
            Some("immediate") => "immediate".to_string(),
            Some("1w") => "1w".to_string(),
            Some("2w") => "2w".to_string(),
            _ => default_retention(),
        },
    };

    // 先验证再保存 —— 存一份连不上的配置没有意义
    verify_login(&source, &password).await?;

    keyring_set(&source.user, &password)?;
    write_source(&source)?;
    log::info!("[imap] 已保存凭据 {}", source.redacted());
    Ok(status_of(Some(source)))
}

/// 删配置 + 删密码。凭据库里已经没有那条时**照样**清掉 JSON ——
/// 不然残项永远删不掉 (teaching_credentials 踩过这个)。
#[tauri::command]
pub fn imap_credential_clear() -> Result<ImapStatus, String> {
    if let Some(source) = read_source() {
        keyring_delete(&source.user);
        if let Ok(path) = config_path() {
            let _ = std::fs::remove_file(path);
        }
        log::info!("[imap] 已清除凭据 {}", source.redacted());
    }
    Ok(status_of(None))
}

/// 前端问状态。**不返回密码**, 只说有没有。
#[tauri::command]
pub fn imap_credential_status() -> ImapStatus {
    status_of(read_source())
}

fn status_of(source: Option<ImapSource>) -> ImapStatus {
    match source {
        Some(s) => ImapStatus {
            password_present: password_for(&s.user).is_some(),
            configured: true,
            host: s.host,
            user: s.user,
            port: s.port,
            smtp_host: s.smtp_host,
            smtp_port: s.smtp_port,
            retention: s.retention,
        },
        None => ImapStatus {
            configured: false,
            host: String::new(),
            user: String::new(),
            port: DEFAULT_PORT,
            password_present: false,
            smtp_host: String::new(),
            smtp_port: 0,
            retention: default_retention(),
        },
    }
}

/// 拿 catfish-email 自己去连一次, 验证配置能用。
///
/// 为什么不在 Rust 里直接开 IMAP 连接: 验证要跟**实际取数的那条路**走同一套
/// 代码, 否则"验证通过但取不到邮件"完全可能 —— 今天 Foxmail 那条线就是
/// 每一层都"对"但合起来不通。
async fn verify_login(source: &ImapSource, password: &str) -> Result<(), String> {
    let bin = crate::services::catfish_paths::catfish_email_bin()
        .ok_or_else(|| "邮件组件还没装好, 稍后再试".to_string())?;
    let mut command = crate::services::process::background_command(&bin);
    command
        .env("CATFISH_IMAP_HOST", &source.host)
        .env("CATFISH_IMAP_PORT", source.port.to_string())
        .env("CATFISH_IMAP_USER", &source.user)
        .env("CATFISH_IMAP_PASSWORD", password)
        .env("CATFISH_IMAP_RETENTION", &source.retention)
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .args(["--client", "imap", "accounts"]);
    let output = command
        .output()
        .map_err(|e| format!("启动邮件组件失败: {e}"))?;
    if output.status.success() {
        return Ok(());
    }
    // stderr 里可能带服务器原文, 截断并压成一行, 别把一坨东西丢给界面
    let reason = String::from_utf8_lossy(&output.stderr);
    let reason: String = reason.split_whitespace().collect::<Vec<_>>().join(" ");
    Err(format!(
        "连接失败 ({}): {}",
        source.redacted(),
        reason.chars().take(300).collect::<String>()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_shape_is_pinned_so_nobody_adds_a_secret_field() {
        // 这条的目的是防止后来人往状态里塞密码。
        //
        // 第一版的判据是"JSON 里不能出现 password 这个词" —— 判错了对象:
        // `password_present` 是个**布尔标志**, 合法地含有这个词, 于是测试自己红了。
        // 要防的是密码**值**泄漏, 不是字段名里有这个词。
        //
        // 所以改成钉住字段集合: 加任何新字段都会让这条红, 那时人得停下来想一下
        // "这个字段该不该给前端"。
        let json = serde_json::to_value(status_of(None)).unwrap();
        let object = json.as_object().expect("状态应该是个 JSON object");
        let mut keys: Vec<&str> = object.keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(
            keys,
            [
                "configured",
                "host",
                "password_present",
                "port",
                "smtp_host",
                "smtp_port",
                "user",
            ],
            "状态的字段集合变了 —— 新字段会不会把秘密带给前端?"
        );
        // 9/18 加 smtp_host / smtp_port 时这条红了, 正是它该干的事。停下来想过:
        // 这两个不是秘密 (主机名和端口号), 而且员工**必须**看得见回复会从哪台
        // 服务器发出去 —— 我们会替他猜 smtp.<域名>, 猜错了得让他能发现并改掉,
        // 而不是等回复发不出去再去猜我们猜了什么。
        assert!(
            object["password_present"].is_boolean(),
            "password_present 必须是布尔, 绝不能变成密码本身"
        );
    }

    #[test]
    fn redacted_form_has_no_password() {
        let source = ImapSource {
            host: "imap.example.cn".into(),
            user: "me@example.cn".into(),
            port: 993,
            smtp_host: String::new(),
            smtp_port: 0,
            retention: "never".into(),
        };
        // 保留策略也在里面 —— 它是唯一不可逆的一项, 查"邮件怎么没了"时
        // 第一眼就该看到。跟 Python 侧 ImapConfig.redacted() 对齐。
        assert!(!source.redacted().contains("s3cret"));
        assert!(source.redacted().contains("me@example.cn@imap.example.cn:993"));
        assert!(source.redacted().contains("never"));
    }

    #[test]
    fn old_config_without_smtp_fields_still_loads() {
        // 9/18 之前配过 IMAP 的机器上, JSON 里没有这两个键。升级后要是读不出来,
        // 员工的邮箱配置会凭空消失 —— 而他只会觉得"鲶鱼把我配置弄丢了"。
        let parsed: ImapSource = serde_json::from_str(
            r#"{"host":"imap.example.cn","user":"me@example.cn","port":993}"#,
        )
        .expect("老配置必须还能读");
        assert_eq!(parsed.smtp_host, "");
        assert_eq!(parsed.smtp_port, 0, "0 = 用默认 465");
    }

    #[test]
    fn source_json_roundtrips_without_port() {
        // 老配置可能没有 port 字段, 要能读出来用默认值
        let parsed: ImapSource =
            serde_json::from_str(r#"{"host":"h","user":"u"}"#).unwrap();
        assert_eq!(parsed.port, 993);
    }

    #[test]
    fn unconfigured_status_is_all_empty() {
        let s = status_of(None);
        assert!(!s.configured && !s.password_present);
        assert!(s.host.is_empty() && s.user.is_empty());
        assert_eq!(s.port, 993);
    }
}
