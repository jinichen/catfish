//! 换机迁移包: zip (manifest + 文件) 外面套 age 口令加密 (设计稿 §5)。
//!
//! 包里路径一律 `<catfish|hermes>/<相对路径>`, `/` 分隔; 解包时逐条校验 sha256 和路径安全。

use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};

use age::secrecy::SecretString;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use zip::write::SimpleFileOptions;

use super::catalog::{exported, Class, Root};
use super::snapshot::{is_db_file, rel_slash, snapshot_sqlite, stable_len, walk, Entry, Roots};

pub const FORMAT: u32 = 1;
const MANIFEST: &str = "manifest.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Source {
    pub os: String,
    pub device_id: String,
    pub user_email: String,
    pub home: String,
    pub catfish_home: String,
    pub hermes_home: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Item {
    pub path: String,
    pub sha256: String,
    pub bytes: u64,
    pub class: Class,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChainTip {
    pub last_id: i64,
    pub last_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Manifest {
    pub format: u32,
    pub created_at: String,
    pub companion_version: String,
    pub source: Source,
    /// 第一层每项的摘要 (带了的、没带的都在, 给导入页说清楚)
    pub entries: Vec<Entry>,
    pub items: Vec<Item>,
    pub egress_chain: Option<ChainTip>,
}

impl Manifest {
    pub fn total_bytes(&self) -> u64 {
        self.items.iter().map(|i| i.bytes).sum()
    }
}

/// 包里一条路径 → (根, 根下相对路径)。不合规的 (绝对路径 / `..` / 反斜杠 / 空段) 一律拒绝。
pub fn split_path(p: &str) -> Option<(Root, &str)> {
    let (root, rel) = p.split_once('/')?;
    let root = Root::parse(root)?;
    if rel.is_empty() || rel.contains('\\') || rel.contains(':') || rel.split('/').any(|s| s.is_empty() || s == "." || s == "..") {
        return None;
    }
    Some((root, rel))
}

fn hash_copy(mut r: impl Read, mut w: impl Write, limit: u64) -> io::Result<(String, u64)> {
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 256 * 1024];
    let mut left = limit;
    let mut n_total = 0;
    while left > 0 {
        let want = buf.len().min(left as usize);
        let n = r.read(&mut buf[..want])?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        w.write_all(&buf[..n])?;
        left -= n as u64;
        n_total += n as u64;
    }
    Ok((hex::encode(h.finalize()), n_total))
}

/// 打 zip (未加密)。`optional` = 员工勾选要带的可选项名字 (`catfish/uploads` 这种)。
pub fn build_zip(
    roots: &Roots,
    entries: &[Entry],
    optional: &HashSet<String>,
    tmp: &Path,
    zip_path: &Path,
    source: Source,
) -> Result<Manifest, String> {
    let file = File::create(zip_path).map_err(|e| format!("建临时文件失败: {e}"))?;
    let mut z = zip::ZipWriter::new(BufWriter::new(file));
    // 已经压缩过的 / 数据库: 存储不压缩 (省时间); 文本压缩
    let opts = |name: &str| {
        let lower = name.to_ascii_lowercase();
        let stored = [".zip", ".png", ".jpg", ".jpeg", ".mp4", ".mp3", ".m4a", ".pdf", ".xlsx", ".docx", ".pptx", ".gz", ".onnx"]
            .iter()
            .any(|e| lower.ends_with(e));
        SimpleFileOptions::default()
            .compression_method(if stored { zip::CompressionMethod::Stored } else { zip::CompressionMethod::Deflated })
            .large_file(true)
    };
    let mut items = vec![];
    for e in entries {
        let key = format!("{}/{}", e.root.dir_name(), e.name);
        if !exported(e.class, optional.contains(&key)) {
            continue;
        }
        let base = roots.of(e.root);
        for f in walk(&base.join(&e.name)) {
            let Some(rel) = rel_slash(base, &f) else { continue };
            let name = format!("{}/{}", e.root.dir_name(), rel);
            // 数据库先做一致性快照, 再从快照读
            let (src, len) = if is_db_file(&rel) {
                let snap = tmp.join("db").join(&name);
                snapshot_sqlite(&f, &snap)?;
                let len = fs::metadata(&snap).map_err(|e| e.to_string())?.len();
                (snap, len)
            } else {
                (f.clone(), stable_len(&f)?)
            };
            z.start_file(name.as_str(), opts(&name)).map_err(|e| format!("写包失败: {e}"))?;
            let r = File::open(&src).map_err(|e| format!("读 {} 失败: {e}", src.display()))?;
            let (sha, bytes) = hash_copy(BufReader::new(r), &mut z, len).map_err(|e| format!("写包失败: {e}"))?;
            items.push(Item { path: name, sha256: sha, bytes, class: e.class });
        }
    }
    // 外发记录链头取自快照 (跟包里那份库一致), 新机据此登记「旧机的锚点只认到这里」
    let snap = tmp.join("db").join("catfish").join("outbound_log.db");
    let egress_chain = snap
        .exists()
        .then(|| rusqlite::Connection::open(&snap).ok())
        .flatten()
        .map(|c| super::super::transparent_log_lineage::chain_tip(&c))
        .map(|(last_id, last_hash)| ChainTip { last_id, last_hash });
    let manifest = Manifest {
        format: FORMAT,
        created_at: chrono::Local::now().to_rfc3339(),
        companion_version: env!("CARGO_PKG_VERSION").to_string(),
        source,
        entries: entries.to_vec(),
        items,
        egress_chain,
    };
    z.start_file(MANIFEST, SimpleFileOptions::default()).map_err(|e| e.to_string())?;
    z.write_all(&serde_json::to_vec_pretty(&manifest).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    z.finish().map_err(|e| format!("写包失败: {e}"))?.flush().map_err(|e| e.to_string())?;
    Ok(manifest)
}

/// zip → age 加密 → `dest` (先写 .part 再改名, 写一半不留半个包)。
pub fn encrypt(zip_path: &Path, dest: &Path, passphrase: &str) -> Result<u64, String> {
    let part = PathBuf::from(format!("{}.part", dest.display()));
    let out = File::create(&part).map_err(|e| format!("写 {} 失败: {e}", dest.display()))?;
    let enc = age::Encryptor::with_user_passphrase(SecretString::from(passphrase.to_owned()));
    let mut w = enc.wrap_output(BufWriter::new(out)).map_err(|e| format!("加密失败: {e}"))?;
    let mut r = BufReader::new(File::open(zip_path).map_err(|e| e.to_string())?);
    io::copy(&mut r, &mut w).map_err(|e| format!("加密失败: {e}"))?;
    w.finish().and_then(|mut f| f.flush()).map_err(|e| format!("加密失败: {e}"))?;
    fs::rename(&part, dest).map_err(|e| format!("保存失败: {e}"))?;
    Ok(fs::metadata(dest).map(|m| m.len()).unwrap_or(0))
}

/// 解密到临时 zip。口令不对单独报 (员工最常见的错)。
pub fn decrypt(pkg: &Path, passphrase: &str, zip_path: &Path) -> Result<(), String> {
    let f = File::open(pkg).map_err(|e| format!("打不开迁移包: {e}"))?;
    let dec = age::Decryptor::new(BufReader::new(f)).map_err(|_| "这不是鲶鱼迁移包 (或文件已损坏)".to_string())?;
    let id = age::scrypt::Identity::new(SecretString::from(passphrase.to_owned()));
    let mut r = dec.decrypt(std::iter::once(&id as &dyn age::Identity)).map_err(|e| match e {
        age::DecryptError::DecryptionFailed | age::DecryptError::NoMatchingKeys => "迁移口令不对".to_string(),
        other => format!("解密失败: {other}"),
    })?;
    let mut out = BufWriter::new(File::create(zip_path).map_err(|e| e.to_string())?);
    io::copy(&mut r, &mut out).map_err(|_| "迁移包已损坏 (解密到一半出错)".to_string())?;
    out.flush().map_err(|e| e.to_string())
}

pub fn read_manifest(zip_path: &Path) -> Result<Manifest, String> {
    let mut a = zip::ZipArchive::new(File::open(zip_path).map_err(|e| e.to_string())?).map_err(|_| "迁移包已损坏".to_string())?;
    let mut s = String::new();
    a.by_name(MANIFEST).map_err(|_| "迁移包里没有清单".to_string())?.read_to_string(&mut s).map_err(|e| e.to_string())?;
    let m: Manifest = serde_json::from_str(&s).map_err(|e| format!("清单读不懂: {e}"))?;
    if m.format > FORMAT {
        return Err("这个迁移包是更新版本的 Companion 导出的, 请先把这台电脑的 Companion 升到最新".into());
    }
    Ok(m)
}

/// 每条都在、sha256 对得上、路径安全、没有清单外的东西。
pub fn verify(zip_path: &Path, m: &Manifest) -> Result<(), String> {
    let mut a = zip::ZipArchive::new(File::open(zip_path).map_err(|e| e.to_string())?).map_err(|_| "迁移包已损坏".to_string())?;
    let want: HashMap<&str, &Item> = m.items.iter().map(|i| (i.path.as_str(), i)).collect();
    let mut seen = 0;
    for i in 0..a.len() {
        let mut f = a.by_index(i).map_err(|_| "迁移包已损坏".to_string())?;
        let name = f.name().map_err(|_| "迁移包已损坏".to_string())?.to_string();
        if name == MANIFEST || f.is_dir() {
            continue;
        }
        let item = want.get(name.as_str()).ok_or_else(|| format!("迁移包里有清单外的文件: {name}"))?;
        split_path(&name).ok_or_else(|| format!("迁移包里有不安全的路径: {name}"))?;
        let (sha, n) = hash_copy(&mut f, io::sink(), u64::MAX).map_err(|_| "迁移包已损坏".to_string())?;
        if sha != item.sha256 || n != item.bytes {
            return Err(format!("迁移包被改过或损坏: {name}"));
        }
        seen += 1;
    }
    if seen != m.items.len() {
        return Err("迁移包不完整 (少了文件)".into());
    }
    Ok(())
}

/// 解出一条到 `dst` (已校验过)。
pub fn extract_to(a: &mut zip::ZipArchive<File>, name: &str, dst: &Path) -> Result<(), String> {
    if let Some(p) = dst.parent() {
        fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    let mut f = a.by_name(name).map_err(|e| e.to_string())?;
    let mut out = BufWriter::new(File::create(dst).map_err(|e| format!("写 {} 失败: {e}", dst.display()))?);
    io::copy(&mut f, &mut out).map_err(|e| e.to_string())?;
    out.flush().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 包内路径校验() {
        assert!(split_path("catfish/wiki/a.md").is_some());
        assert!(split_path("hermes/state.db").is_some());
        assert!(split_path("catfish/../etc/passwd").is_none());
        assert!(split_path("catfish//a").is_none());
        assert!(split_path("other/a").is_none());
        assert!(split_path("catfish/C:/x").is_none());
        assert!(split_path(r"catfish/a\..\b").is_none());
    }
}
