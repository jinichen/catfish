//! 换机迁移 (BL-L9, docs/DEVICE-MIGRATION-DESIGN.md)。
//!
//! 旧机「导出」一个口令加密的迁移包, 员工自己带到新机 (U 盘 / 隔空投送 / 网盘) 「导入」。
//! **不经过中央**: 数据边界不变。凭证刻意不带, 新机重新登录 / 授权。
//!
//! - `catalog`  第一层每一项归哪类 (必带 / 可选 / 合并 / 重建 / 凭证 / 未归类)
//! - `snapshot` 扫描、一致性快照 (SQLite `VACUUM INTO`、JSONL 截到整行)、路径改写
//! - `package`  zip + age 口令加密, manifest, 校验
//! - `apply`    导入: 暂存 → 改写 → 备份原有 → 换入, 出错回滚

pub mod apply;
pub mod catalog;
pub mod package;
pub mod snapshot;
#[cfg(test)]
mod tests;

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::Serialize;

use catalog::Class;
use package::{Manifest, Source};
use snapshot::{Entry, Roots};

const MIN_PASSPHRASE: usize = 8;
/// 同一时间只做一件 (导出 / 导入都要独占)
static BUSY: Mutex<()> = Mutex::new(());

/// 当前这台的身份 (导出写进包、导入时核对是不是同一个人)。
pub struct Me {
    pub email: String,
    pub device_id: String,
}

impl Me {
    fn current() -> Result<Me, String> {
        let email = crate::services::oauth::current_user_sub().ok_or("请先登录, 再做换机迁移")?;
        let device_id = crate::services::device_id::current().ok_or("找不到用户目录")?;
        Ok(Me { email, device_id })
    }
}

fn os_name() -> &'static str {
    if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

fn stamp() -> String {
    chrono::Local::now().format("%Y%m%d-%H%M%S").to_string()
}

/// 临时目录放在 .catfish 下 (跟目标同一个盘, 换入时是改名不是拷贝); 用完删。
struct TmpDir(PathBuf);

impl TmpDir {
    fn new(roots: &Roots) -> Result<TmpDir, String> {
        let p = roots.catfish.join(format!(".migration-tmp-{}", stamp()));
        std::fs::create_dir_all(&p).map_err(|e| format!("建临时目录失败: {e}"))?;
        Ok(TmpDir(p))
    }
}

impl Drop for TmpDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

// ── 扫描 ───────────────────────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanResult {
    pub entries: Vec<Entry>,
    /// 没登记过的, 不会带 (列出来让员工知道)
    pub unclassified: Vec<String>,
    pub required_bytes: u64,
}

pub fn scan_at(roots: &Roots) -> ScanResult {
    let entries = snapshot::scan(roots);
    let unclassified = entries
        .iter()
        .filter(|e| e.class == Class::Unclassified)
        .map(|e| format!("{}/{}", e.root.dir_name(), e.name))
        .collect();
    let required_bytes = entries.iter().filter(|e| matches!(e.class, Class::Required | Class::Merge)).map(|e| e.bytes).sum();
    ScanResult { entries, unclassified, required_bytes }
}

// ── 导出 ───────────────────────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportReport {
    pub path: String,
    pub bytes: u64,
    pub files: u64,
    pub unclassified: Vec<String>,
}

pub fn export_at(roots: &Roots, me: &Me, dest: &Path, passphrase: &str, optional: &[String]) -> Result<ExportReport, String> {
    if passphrase.chars().count() < MIN_PASSPHRASE {
        return Err(format!("迁移口令至少 {MIN_PASSPHRASE} 位"));
    }
    // 外发记录链上先记一行「导出迁移包」(快照里就带着这一行)
    let egress = roots.catfish.join("outbound_log.db");
    if egress.exists() {
        let mut conn = super::transparent_log::open_db_at(&egress)?;
        super::transparent_log_lineage::record_export(&mut conn, &me.device_id)?;
    }
    let scan = scan_at(roots);
    let tmp = TmpDir::new(roots)?;
    let zip_path = tmp.0.join("pkg.zip");
    let source = Source {
        os: os_name().into(),
        device_id: me.device_id.clone(),
        user_email: me.email.clone(),
        home: roots.home.to_string_lossy().into_owned(),
        catfish_home: roots.catfish.to_string_lossy().into_owned(),
        hermes_home: roots.hermes.to_string_lossy().into_owned(),
    };
    let chosen: HashSet<String> = optional.iter().cloned().collect();
    let m = package::build_zip(roots, &scan.entries, &chosen, &tmp.0, &zip_path, source)?;
    let bytes = package::encrypt(&zip_path, dest, passphrase)?;
    Ok(ExportReport {
        path: dest.to_string_lossy().into_owned(),
        bytes,
        files: m.items.len() as u64,
        unclassified: scan.unclassified,
    })
}

// ── 查看 / 导入 ─────────────────────────────────────────

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InspectReport {
    pub created_at: String,
    pub source_os: String,
    pub user_email: String,
    pub companion_version: String,
    pub files: u64,
    pub bytes: u64,
    /// 包里带了的各项 (给员工看「包里有什么」)
    pub entries: Vec<Entry>,
    /// 包里带了「文件」(附件 / 生成的文件等可选项)
    pub with_files: bool,
    pub same_person: bool,
    pub logged_in_as: String,
    pub version_ok: bool,
    pub target_has_data: bool,
}

/// "1.2.16" → (1, 2, 16); 读不懂的段当 0
fn ver(s: &str) -> (u64, u64, u64) {
    let mut it = s.split(|c: char| !c.is_ascii_digit()).filter(|x| !x.is_empty()).map(|x| x.parse().unwrap_or(0));
    (it.next().unwrap_or(0), it.next().unwrap_or(0), it.next().unwrap_or(0))
}

fn open_package(roots: &Roots, pkg: &Path, passphrase: &str) -> Result<(TmpDir, PathBuf, Manifest), String> {
    let tmp = TmpDir::new(roots)?;
    let zip_path = tmp.0.join("pkg.zip");
    package::decrypt(pkg, passphrase, &zip_path)?;
    let m = package::read_manifest(&zip_path)?;
    package::verify(&zip_path, &m)?;
    Ok((tmp, zip_path, m))
}

fn inspect_report(roots: &Roots, me: &Me, m: &Manifest) -> InspectReport {
    let carried: HashSet<String> = m.items.iter().filter_map(|i| {
        let (root, rel) = package::split_path(&i.path)?;
        Some(format!("{}/{}", root.dir_name(), rel.split('/').next().unwrap_or(rel)))
    }).collect();
    InspectReport {
        created_at: m.created_at.clone(),
        source_os: m.source.os.clone(),
        user_email: m.source.user_email.clone(),
        companion_version: m.companion_version.clone(),
        files: m.items.len() as u64,
        bytes: m.total_bytes(),
        entries: m.entries.iter().filter(|e| carried.contains(&format!("{}/{}", e.root.dir_name(), e.name))).cloned().collect(),
        with_files: m.items.iter().any(|i| i.class == Class::Optional),
        same_person: m.source.user_email.trim().eq_ignore_ascii_case(me.email.trim()),
        logged_in_as: me.email.clone(),
        version_ok: ver(&m.companion_version) <= ver(env!("CARGO_PKG_VERSION")),
        target_has_data: apply::target_has_data(roots),
    }
}

pub fn inspect_at(roots: &Roots, me: &Me, pkg: &Path, passphrase: &str) -> Result<InspectReport, String> {
    let (_tmp, _zip, m) = open_package(roots, pkg, passphrase)?;
    Ok(inspect_report(roots, me, &m))
}

pub fn import_at(roots: &Roots, me: &Me, pkg: &Path, passphrase: &str, overwrite: bool) -> Result<apply::ImportReport, String> {
    let (tmp, zip_path, m) = open_package(roots, pkg, passphrase)?;
    let check = inspect_report(roots, me, &m);
    if !check.same_person {
        return Err(format!("这是 {} 的迁移包, 当前登录的是 {}。迁移包只能导给本人", check.user_email, check.logged_in_as));
    }
    if !check.version_ok {
        return Err(format!("迁移包来自 Companion {}, 比这台新, 请先把这台升级到最新", check.companion_version));
    }
    if check.target_has_data && !overwrite {
        return Err("这台电脑已经有鲶鱼数据。要导入请勾选「先备份当前数据再覆盖」".into());
    }
    let stamp = stamp();
    let mut rep = apply::apply(&apply::Target { roots, zip: &zip_path, tmp: &tmp.0, stamp: &stamp }, &m)?;
    // 外发记录: 接着旧链记一行「设备迁移」, 登记旧机锚点只认到导出点
    let egress = roots.catfish.join("outbound_log.db");
    if let (true, Some(tip)) = (egress.exists(), m.egress_chain.as_ref()) {
        let r = super::transparent_log::open_db_at(&egress).and_then(|mut c| {
            super::transparent_log_lineage::record_import(&mut c, &m.source.device_id, &me.device_id, (tip.last_id, &tip.last_hash))
        });
        if let Err(e) = r {
            rep.warnings.push(format!("外发记录没记上迁移这一行: {e}"));
        }
    }
    Ok(rep)
}

// ── Tauri 命令 ─────────────────────────────────────────

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tokio::task::spawn_blocking(f).await.map_err(|e| format!("内部错误: {e}"))?
}

/// 导出 / 查看 / 导入互斥 (员工连点两下、或一边导出一边导入)
async fn exclusive<T: Send + 'static>(f: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    blocking(move || {
        let _busy = BUSY.try_lock().map_err(|_| "正在做另一次迁移, 请等它结束")?;
        f()
    })
    .await
}

#[tauri::command]
pub async fn migration_scan() -> Result<ScanResult, String> {
    blocking(|| Ok(scan_at(&Roots::current()?))).await
}

#[tauri::command]
pub fn migration_pick_save(default_name: String) -> Option<String> {
    rfd::FileDialog::new()
        .set_title("保存鲶鱼迁移包")
        .set_file_name(&default_name)
        .add_filter("鲶鱼迁移包", &["catfishmove"])
        .save_file()
        .map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn migration_pick_open() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("选择鲶鱼迁移包")
        .add_filter("鲶鱼迁移包", &["catfishmove"])
        .pick_file()
        .map(|p| p.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn migration_export(dest: String, passphrase: String, optional: Vec<String>) -> Result<ExportReport, String> {
    exclusive(move || export_at(&Roots::current()?, &Me::current()?, Path::new(&dest), &passphrase, &optional)).await
}

#[tauri::command]
pub async fn migration_inspect(path: String, passphrase: String) -> Result<InspectReport, String> {
    exclusive(move || inspect_at(&Roots::current()?, &Me::current()?, Path::new(&path), &passphrase)).await
}

#[tauri::command]
pub async fn migration_import(path: String, passphrase: String, overwrite: bool) -> Result<apply::ImportReport, String> {
    let mut rep = exclusive(move || import_at(&Roots::current()?, &Me::current()?, Path::new(&path), &passphrase, overwrite)).await?;
    // Hermes 启动时才读记忆 / 人设 / 技能: 重启一下 (对话历史已经在线写进库了)
    if let Err(e) = tokio::task::spawn_blocking(super::codex_gateway::restart_hermes_gateway).await.map_err(|e| e.to_string()).and_then(|r| r) {
        rep.warnings.push(format!("Hermes 没能自动重启 ({e}), 请重启 Companion"));
    }
    Ok(rep)
}

/// 导入完重启 Companion: 索引 / 缓存按新数据重建, 设置重新加载。
#[tauri::command]
pub fn migration_restart_app(app: tauri::AppHandle) {
    app.restart();
}
