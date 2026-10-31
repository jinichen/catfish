//! 换机迁移导入: 解到暂存目录 → 改写路径 → 备份新机原有的 → 整项换入; 中途出错整体回滚 (设计稿 §7)。

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};
use serde::Serialize;

use super::catalog::{Class, Root};
use super::package::{extract_to, split_path, Manifest};
use super::snapshot::{is_db_file, is_text, snapshot_sqlite, walk, Rewrite, Roots};

/// 新机上要员工自己重新做的事 (凭证刻意不带, 见设计稿 §4.4)。
pub const REDO: &[&str] = &[
    "邮箱：到「邮件」页重新授权",
    "微信：到「设置 → 隐私 → 微信接入」重新扫码",
    "MCP 工具：用到时会提示重新授权",
    "教学录制里存的网站密码：到「设置 → 技能」重新录入",
    "本地模型 / 会议组件：用到时会提示下载",
];

#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ImportReport {
    pub files: u64,
    pub bytes: u64,
    pub paths_rewritten: u64,
    /// Hermes 技能里新机已有、没覆盖的
    pub kept_existing: u64,
    /// 新机原有数据挪到这里 (覆盖导入时)
    pub backup_dir: Option<String>,
    pub redo: Vec<String>,
    pub warnings: Vec<String>,
}

/// 新机是不是已经有鲶鱼数据 (有就默认拒绝导入, 见设计稿 §7)。
pub fn target_has_data(roots: &Roots) -> bool {
    let journal = fs::read_to_string(roots.catfish.join("employee_journal.md")).unwrap_or_default();
    if !journal.trim().is_empty() {
        return true;
    }
    if !walk(&roots.catfish.join("task_chat")).is_empty() {
        return true;
    }
    if walk(&roots.catfish.join("wiki")).iter().any(|p| p.extension().is_some_and(|e| e == "md")) {
        return true;
    }
    let db = roots.hermes.join("state.db");
    if db.exists() {
        if let Ok(c) = Connection::open_with_flags(&db, OpenFlags::SQLITE_OPEN_READ_ONLY) {
            let n: i64 = c.query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0)).unwrap_or(0);
            return n > 0;
        }
    }
    false
}

fn move_path(from: &Path, to: &Path) -> Result<(), String> {
    if let Some(p) = to.parent() {
        fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    if fs::rename(from, to).is_ok() {
        return Ok(());
    }
    // 跨盘 (Windows 上 .catfish 和 Hermes 目录可能不在一个卷): 拷完再删
    copy_tree(from, to)?;
    remove_path(from)
}

fn copy_tree(from: &Path, to: &Path) -> Result<(), String> {
    if from.is_dir() {
        fs::create_dir_all(to).map_err(|e| e.to_string())?;
        for e in fs::read_dir(from).map_err(|e| e.to_string())?.flatten() {
            copy_tree(&e.path(), &to.join(e.file_name()))?;
        }
        Ok(())
    } else {
        fs::copy(from, to).map(|_| ()).map_err(|e| format!("拷贝 {} 失败: {e}", from.display()))
    }
}

fn remove_path(p: &Path) -> Result<(), String> {
    if p.is_dir() {
        fs::remove_dir_all(p).map_err(|e| e.to_string())
    } else if p.exists() {
        fs::remove_file(p).map_err(|e| e.to_string())
    } else {
        Ok(())
    }
}

/// 在线还原: 用 SQLite backup 接口把暂存库整库写进正在用的库 (Hermes 开着也行, 不用停服务)。
fn restore_live(staged: &Path, target: &Path) -> Result<(), String> {
    let mut dst = Connection::open(target).map_err(|e| format!("打开 {} 失败: {e}", target.display()))?;
    let _ = dst.busy_timeout(std::time::Duration::from_secs(30));
    dst.restore(rusqlite::DatabaseName::Main, staged, None::<fn(rusqlite::backup::Progress)>)
        .map_err(|e| format!("写入 {} 失败: {e}", target.display()))
}

enum Done {
    /// 整项换入; 原来有的挪进了备份
    Moved { target: PathBuf, backup: Option<PathBuf> },
    /// 数据库在线还原; 原来的快照在备份里
    Restored { target: PathBuf, backup: Option<PathBuf> },
    /// 合并进来的新文件
    Added(Vec<PathBuf>),
}

fn rollback(done: Vec<Done>) {
    for d in done.into_iter().rev() {
        match d {
            Done::Moved { target, backup } => {
                let _ = remove_path(&target);
                if let Some(b) = backup {
                    let _ = move_path(&b, &target);
                }
            }
            Done::Restored { target, backup } => match backup {
                Some(b) => {
                    let _ = restore_live(&b, &target);
                }
                None => {
                    let _ = remove_path(&target);
                }
            },
            Done::Added(files) => files.iter().for_each(|f| {
                let _ = fs::remove_file(f);
            }),
        }
    }
}

pub struct Target<'a> {
    pub roots: &'a Roots,
    pub zip: &'a Path,
    pub tmp: &'a Path,
    pub stamp: &'a str,
}

/// 已经校验过 (decrypt + verify + 同一个人 + 版本) 之后调。
pub fn apply(t: &Target, m: &Manifest) -> Result<ImportReport, String> {
    let mut rep = ImportReport { redo: REDO.iter().map(|s| s.to_string()).collect(), ..Default::default() };
    let s = &m.source;
    let r = t.roots;
    let rw = Rewrite::new(
        [s.home.as_str(), s.catfish_home.as_str(), s.hermes_home.as_str()],
        [&*r.home.to_string_lossy(), &*r.catfish.to_string_lossy(), &*r.hermes.to_string_lossy()],
        s.os == "windows",
        cfg!(windows),
    );
    // 1. 解到暂存目录 (改写文本里的路径)
    let stage = t.tmp.join("stage");
    let mut a = zip::ZipArchive::new(fs::File::open(t.zip).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let mut groups: BTreeMap<(String, String), (Root, Class)> = BTreeMap::new();
    for item in &m.items {
        let (root, rel) = split_path(&item.path).ok_or("迁移包路径不安全")?;
        let dst = stage.join(root.dir_name()).join(rel);
        extract_to(&mut a, &item.path, &dst)?;
        if is_text(rel) && item.bytes < 64 * 1024 * 1024 {
            if let Ok(text) = fs::read_to_string(&dst) {
                let (new, n) = rw.apply(&text);
                if n > 0 {
                    fs::write(&dst, new).map_err(|e| e.to_string())?;
                    rep.paths_rewritten += n as u64;
                }
            }
        }
        rep.files += 1;
        rep.bytes += item.bytes;
        let top = rel.split('/').next().unwrap_or(rel).to_string();
        groups.insert((root.dir_name().to_string(), top), (root, item.class));
    }
    // 2. 逐项换入, 记下做了什么, 出错整体回滚
    let backup_root = r.catfish.join(format!(".migration-backup-{}", t.stamp));
    let mut done: Vec<Done> = vec![];
    let res = (|| -> Result<(), String> {
        for ((_, top), (root, class)) in &groups {
            let staged = stage.join(root.dir_name()).join(top);
            let target = r.of(*root).join(top);
            let backup = backup_root.join(root.dir_name()).join(top);
            if *class == Class::Merge {
                let mut added = vec![];
                for f in walk(&staged) {
                    let rel = f.strip_prefix(&staged).map_err(|e| e.to_string())?;
                    let dst = target.join(rel);
                    if dst.exists() {
                        rep.kept_existing += 1;
                        continue;
                    }
                    move_path(&f, &dst)?;
                    added.push(dst);
                }
                done.push(Done::Added(added));
            } else if staged.is_file() && is_db_file(top) {
                let had = if target.exists() {
                    snapshot_sqlite(&target, &backup)?;
                    Some(backup.clone())
                } else {
                    None
                };
                if had.is_some() {
                    restore_live(&staged, &target)?;
                } else {
                    move_path(&staged, &target)?;
                }
                done.push(Done::Restored { target, backup: had });
            } else {
                let had = if target.exists() {
                    move_path(&target, &backup)?;
                    Some(backup.clone())
                } else {
                    None
                };
                if let Err(e) = move_path(&staged, &target) {
                    if let Some(b) = &had {
                        let _ = move_path(b, &target);
                    }
                    return Err(e);
                }
                done.push(Done::Moved { target, backup: had });
            }
        }
        Ok(())
    })();
    if let Err(e) = res {
        rollback(done);
        return Err(format!("导入没完成, 已恢复原样: {e}"));
    }
    if backup_root.exists() {
        rep.backup_dir = Some(backup_root.to_string_lossy().into_owned());
    }
    Ok(rep)
}
