//! 误删保护 (11/21, docs/DEVICE-MIGRATION-DESIGN.md §13): 每天在本机自动做一份加密快照, 留最近几份。
//!
//! 跟换机迁移包是同一套导出 (同一份清单、同一种一致性快照), 只是:
//! - 不用口令: 用一把只在这台电脑上的密钥 (age x25519, 存**系统钥匙串**) 加密; 快照文件被拷走也打不开。
//! - 不记「导出迁移包」那行外发记录 (没有离开这台电脑)。
//! - 不带「连同文件」那些大件 (附件 / 生成的文件), 只带积累 (日志、记忆、知识库、对话、技能、待办 …)。
//! - 还原可以只挑几项 (误删了知识库就只还原知识库); 还原前当前数据先挪进备份目录。
//!   外发记录 / 审计类**不还原**: 那是只追加的链, 退回去会被当成被人改过 (中央锚点对不上)。
//!
//! 文件: `~/.catfish/snapshots/鲶鱼快照-<时间>.catfishsnap` + 同名 `.json` (明文摘要: 时间、大小、带了哪些项 ——
//! 列表页不用解密几 GB 的包)。设置在 `settings.json` (开关、留几份)。

use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::str::FromStr;

use age::secrecy::ExposeSecret;
use serde::{Deserialize, Serialize};

use super::package::{Lock, RoomsDb, Unlock};
use super::snapshot::{Entry, Roots};
use super::{apply, Me};

pub const DIR: &str = "snapshots";
const EXT: &str = "catfishsnap";
const PREFIX: &str = "鲶鱼快照-";
pub const KEEP_DEFAULT: u32 = 3;
pub const KEEP_MAX: u32 = 7;
/// 最新一份比这个旧就该做新的了
pub const EVERY_SECS: i64 = 24 * 3600;
/// 磁盘至少留这么多才做 (做的时候临时 zip + 加密包同时在, 约 2 倍)
const SPARE_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    pub enabled: bool,
    pub keep: u32,
    #[serde(default)]
    pub last_error: Option<String>,
    #[serde(default)]
    pub last_try_at: Option<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings { enabled: true, keep: KEEP_DEFAULT, last_error: None, last_try_at: None }
    }
}

/// 一份快照的明文摘要 (`<快照>.json`)。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SnapInfo {
    /// 文件名 (不含目录), 还原时用它指定哪一份
    pub name: String,
    pub created_at: String,
    pub bytes: u64,
    pub files: u64,
    pub companion_version: String,
    /// 带了的各项 (给员工挑「还原哪几项」)
    pub entries: Vec<Entry>,
}

pub fn dir(roots: &Roots) -> PathBuf {
    roots.catfish.join(DIR)
}

pub fn load_settings(roots: &Roots) -> Settings {
    fs::read(dir(roots).join("settings.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Settings>(&b).ok())
        .map(|mut s| {
            s.keep = s.keep.clamp(1, KEEP_MAX);
            s
        })
        .unwrap_or_default()
}

pub fn save_settings(roots: &Roots, s: &Settings) -> Result<(), String> {
    let d = dir(roots);
    fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    let tmp = d.join("settings.json.tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(s).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    fs::rename(&tmp, d.join("settings.json")).map_err(|e| e.to_string())
}

/// 最新的在前。只认有摘要的 (半截的 / 别人放进来的不算)。
pub fn list(roots: &Roots) -> Vec<SnapInfo> {
    let mut out: Vec<SnapInfo> = fs::read_dir(dir(roots))
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            if !(name.starts_with(PREFIX) && name.ends_with(&format!(".{EXT}"))) {
                return None;
            }
            let info: SnapInfo = serde_json::from_slice(&fs::read(dir(roots).join(format!("{name}.json"))).ok()?).ok()?;
            (info.name == name).then_some(info)
        })
        .collect();
    out.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    out
}

/// 最新一份做了多久 (秒); 没有 = None。
pub fn age_secs(roots: &Roots) -> Option<i64> {
    let newest = list(roots).into_iter().next()?;
    let t = chrono::DateTime::parse_from_rfc3339(&newest.created_at).ok()?;
    Some((chrono::Local::now().fixed_offset() - t).num_seconds())
}

/// 外发记录 / 审计类: 只追加的链, 还原会被当成篡改 —— 快照里带着 (完整), 但不还原。
pub fn restorable(e: &Entry) -> bool {
    let n = e.name.to_ascii_lowercase();
    !(n.starts_with("outbound_log") || n.contains("audit"))
}

fn key_of(e: &Entry) -> String {
    format!("{}/{}", e.root.dir_name(), e.name)
}

/// 做一份。`identity` = 钥匙串里那把 (测试里直接给)。
pub fn make(roots: &Roots, me: &Me, identity: &age::x25519::Identity, rooms: &RoomsDb, keep: u32) -> Result<SnapInfo, String> {
    let scan = super::scan_at(roots);
    let need = scan.required_bytes.saturating_mul(2).saturating_add(SPARE_BYTES);
    if let Ok(free) = fs2::available_space(&roots.catfish) {
        if free < need {
            return Err(format!(
                "磁盘空间不够 (要约 {} GB, 剩 {} GB), 这次没做快照",
                need / 1024 / 1024 / 1024 + 1,
                free / 1024 / 1024 / 1024
            ));
        }
    }
    let d = dir(roots);
    fs::create_dir_all(&d).map_err(|e| format!("建快照目录失败: {e}"))?;
    // 同一秒做两份 (员工连点 / 测试) 不能互相盖掉
    let base = format!("{PREFIX}{}", super::stamp());
    let name = (1..100)
        .map(|k| if k == 1 { format!("{base}.{EXT}") } else { format!("{base}-{k}.{EXT}") })
        .find(|n| !d.join(n).exists())
        .ok_or("快照名字重了")?;
    let dest = d.join(&name);
    let rep = super::export_with(roots, me, &dest, &Lock::Recipient(identity.to_public()), &[], rooms, false)?;
    let carried: Vec<Entry> = scan.entries.into_iter().filter(|e| super::catalog::exported(e.class, false) && e.files > 0).collect();
    let info = SnapInfo {
        name: name.clone(),
        created_at: chrono::Local::now().to_rfc3339(),
        bytes: rep.bytes,
        files: rep.files,
        companion_version: env!("CARGO_PKG_VERSION").to_string(),
        entries: carried,
    };
    fs::write(d.join(format!("{name}.json")), serde_json::to_vec_pretty(&info).map_err(|e| e.to_string())?)
        .map_err(|e| format!("写快照摘要失败: {e}"))?;
    prune(roots, keep);
    Ok(info)
}

/// 只留最新 `keep` 份 (连摘要一起删)。
pub fn prune(roots: &Roots, keep: u32) {
    for old in list(roots).into_iter().skip(keep.clamp(1, KEEP_MAX) as usize) {
        let p = dir(roots).join(&old.name);
        let _ = fs::remove_file(&p);
        let _ = fs::remove_file(dir(roots).join(format!("{}.json", old.name)));
    }
}

fn find(roots: &Roots, name: &str) -> Result<(PathBuf, SnapInfo), String> {
    let info = list(roots).into_iter().find(|s| s.name == name).ok_or("没有这份快照 (可能已经按保留份数删掉了)")?;
    Ok((dir(roots).join(&info.name), info))
}

/// 还原: `only` = 要还原的几项 (`catfish/wiki` 这种); 不可还原的 (外发记录 / 审计) 一律去掉。
pub fn restore(roots: &Roots, me: &Me, identity: &age::x25519::Identity, name: &str, only: &[String]) -> Result<apply::ImportReport, String> {
    let (path, info) = find(roots, name)?;
    let allowed: HashSet<String> = info.entries.iter().filter(|e| restorable(e)).map(key_of).collect();
    let chosen: HashSet<String> = only.iter().filter(|k| allowed.contains(*k)).cloned().collect();
    if chosen.is_empty() {
        return Err("选一项要还原的".into());
    }
    let o = super::ImportOpts { overwrite: true, only: Some(chosen), lineage: false };
    super::import_with(roots, me, &path, &Unlock::Identity(identity.clone()), &o)
}

/// 还原会动到 Hermes 的东西 (对话 / 记忆 / 技能 …) 吗 —— 动到了要重启 Hermes 让它重新加载。
pub fn touches_hermes(only: &[String]) -> bool {
    only.iter().any(|k| k.starts_with("hermes/"))
}

// ── 钥匙串里的密钥 ─────────────────────────────────────────

#[cfg(any(target_os = "macos", target_os = "windows"))]
const SERVICE: &str = "catfish-snapshots";
#[cfg(any(target_os = "macos", target_os = "windows"))]
const ACCOUNT: &str = "snapshot-key";

#[cfg(any(target_os = "macos", target_os = "windows"))]
fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("无法打开本机凭据库: {e}"))
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn entry() -> Result<keyring::Entry, String> {
    Err("本平台没有配置系统凭据库后端, 误删保护快照不可用".to_string())
}

/// 取快照密钥, 没有就生成一把存进钥匙串 (存完再读一遍核对)。⚠ 不进日志、不给前端。
pub fn identity() -> Result<age::x25519::Identity, String> {
    let e = entry()?;
    match e.get_password() {
        Ok(s) => age::x25519::Identity::from_str(s.trim()).map_err(|_| "钥匙串里的快照密钥格式不对".to_string()),
        Err(keyring::Error::NoEntry) => {
            let id = age::x25519::Identity::generate();
            let s = id.to_string();
            e.set_password(s.expose_secret()).map_err(|err| format!("快照密钥存不进钥匙串: {err}"))?;
            match e.get_password() {
                Ok(back) if back == *s.expose_secret() => {
                    log::info!("[snapshots] 生成了误删保护快照的密钥 (存在系统钥匙串)");
                    Ok(id)
                }
                _ => Err("快照密钥存进钥匙串后读回来对不上".into()),
            }
        }
        Err(err) => Err(format!("读不到快照密钥: {err}")),
    }
}

// ── Tauri 命令 + 每天自动做 ───────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub settings: Settings,
    pub snapshots: Vec<SnapInfo>,
    pub dir: String,
    /// 正在做 / 正在还原 / 在做换机迁移
    pub busy: bool,
}

fn status_of(roots: &Roots) -> Status {
    Status {
        settings: load_settings(roots),
        snapshots: list(roots),
        dir: dir(roots).to_string_lossy().into_owned(),
        busy: super::BUSY.try_lock().is_err(),
    }
}

#[tauri::command]
pub async fn snapshots_status() -> Result<Status, String> {
    super::blocking(|| Ok(status_of(&Roots::current()?))).await
}

#[tauri::command]
pub async fn snapshots_set(enabled: bool, keep: u32) -> Result<Status, String> {
    super::blocking(move || {
        let roots = Roots::current()?;
        let mut s = load_settings(&roots);
        s.enabled = enabled;
        s.keep = keep.clamp(1, KEEP_MAX);
        save_settings(&roots, &s)?;
        prune(&roots, s.keep);
        Ok(status_of(&roots))
    })
    .await
}

/// 做一份 (员工点「现在做一份」, 或每天自动)。结果 / 出错记进设置, 界面看得到。
async fn make_now_inner() -> Result<SnapInfo, String> {
    let roots = Roots::current()?;
    let rooms = super::rooms_for_export(&roots).await?;
    let snap = rooms.snapshot.clone();
    let r2 = roots.clone();
    let r = super::exclusive(move || {
        let id = identity()?;
        let keep = load_settings(&r2).keep;
        make(&r2, &Me::current()?, &id, &rooms, keep)
    })
    .await;
    if let Some(p) = snap {
        let _ = fs::remove_file(p);
    }
    let mut s = load_settings(&roots);
    s.last_try_at = Some(chrono::Local::now().to_rfc3339());
    s.last_error = r.as_ref().err().cloned();
    let _ = save_settings(&roots, &s);
    r
}

#[tauri::command]
pub async fn snapshots_make_now() -> Result<SnapInfo, String> {
    make_now_inner().await
}

#[tauri::command]
pub async fn snapshots_restore(name: String, only: Vec<String>) -> Result<apply::ImportReport, String> {
    let hermes = touches_hermes(&only);
    let rep = super::exclusive(move || restore(&Roots::current()?, &Me::current()?, &identity()?, &name, &only)).await?;
    if !hermes {
        return Ok(rep);
    }
    // 对话 / 记忆 / 技能在 Hermes 那边: 重启让它重新加载 (群聊密钥同一台电脑不变, after_import 里是空操作)
    Ok(super::after_import(rep).await)
}

/// 启动后 10 分钟起, 每小时看一眼: 开着、最新一份超过一天 (或还没有)、没在做别的迁移 → 做一份。
pub fn spawn_daily() {
    tauri::async_runtime::spawn(async {
        tokio::time::sleep(std::time::Duration::from_secs(600)).await;
        loop {
            let due = tokio::task::spawn_blocking(|| {
                let roots = Roots::current().ok()?;
                let s = load_settings(&roots);
                let stale = age_secs(&roots).is_none_or(|a| a >= EVERY_SECS);
                // 上次失败 (磁盘满 / 没登录) 的, 隔 6 小时再试, 不每小时刷
                let backoff = s.last_error.is_some()
                    && s.last_try_at.as_deref().and_then(|t| chrono::DateTime::parse_from_rfc3339(t).ok()).is_some_and(|t| {
                        (chrono::Local::now().fixed_offset() - t).num_seconds() < 6 * 3600
                    });
                Some(s.enabled && stale && !backoff)
            })
            .await
            .ok()
            .flatten()
            .unwrap_or(false);
            if due && crate::services::oauth::current_user_sub().is_some() {
                match make_now_inner().await {
                    Ok(i) => log::info!("[snapshots] 做了一份误删保护快照: {} ({} 字节)", i.name, i.bytes),
                    Err(e) => log::warn!("[snapshots] 这次没做成: {e}"),
                }
            }
            tokio::time::sleep(std::time::Duration::from_secs(3600)).await;
        }
    });
}
