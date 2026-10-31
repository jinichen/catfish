//! 换机迁移: 扫描本机数据、一致性地读出来、导入时改写文件里的绝对路径 (设计稿 §4 / §6.1 / §6.3)。

use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};

use super::catalog::{classify, is_sqlite, is_sqlite_sidecar, Class, Root};

/// 两个数据根目录 + 用户主目录 (改写路径用)。测试里指到临时目录。
#[derive(Debug, Clone)]
pub struct Roots {
    pub home: PathBuf,
    pub catfish: PathBuf,
    pub hermes: PathBuf,
}

impl Roots {
    pub fn current() -> Result<Roots, String> {
        use crate::services::catfish_paths as p;
        Ok(Roots {
            home: p::user_home().ok_or("找不到用户目录")?,
            catfish: p::catfish_home().ok_or("找不到 ~/.catfish")?,
            hermes: p::hermes_home().ok_or("找不到 Hermes 目录")?,
        })
    }
    pub fn of(&self, r: Root) -> &Path {
        match r {
            Root::Catfish => &self.catfish,
            Root::Hermes => &self.hermes,
        }
    }
}

/// 第一层的一项 (给界面列清单 / manifest 摘要)。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Entry {
    pub root: Root,
    pub name: String,
    pub class: Class,
    pub label: String,
    pub files: u64,
    pub bytes: u64,
}

pub fn scan(roots: &Roots) -> Vec<Entry> {
    let mut out = vec![];
    for root in [Root::Catfish, Root::Hermes] {
        let Ok(rd) = fs::read_dir(roots.of(root)) else { continue };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for name in names {
            let (class, label) = classify(root, &name);
            let mut files = 0;
            let mut bytes = 0;
            for f in walk(&roots.of(root).join(&name)) {
                files += 1;
                bytes += fs::metadata(&f).map(|m| m.len()).unwrap_or(0);
            }
            out.push(Entry { root, name, class, label: label.into(), files, bytes });
        }
    }
    out
}

/// 一项下的所有普通文件 (不跟符号链接; 跳过数据库旁路文件和 .DS_Store)。
pub fn walk(path: &Path) -> Vec<PathBuf> {
    let mut out = vec![];
    let Ok(meta) = fs::symlink_metadata(path) else { return out };
    if meta.file_type().is_symlink() {
        return out;
    }
    if meta.is_file() {
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        if !is_sqlite_sidecar(&name) && name != ".DS_Store" {
            out.push(path.to_path_buf());
        }
        return out;
    }
    if meta.is_dir() {
        if let Ok(rd) = fs::read_dir(path) {
            let mut kids: Vec<PathBuf> = rd.flatten().map(|e| e.path()).collect();
            kids.sort();
            for k in kids {
                out.extend(walk(&k));
            }
        }
    }
    out
}

/// 根目录下的相对路径, 一律 `/` 分隔 (包里存的格式)。
pub fn rel_slash(root: &Path, file: &Path) -> Option<String> {
    let rel = file.strip_prefix(root).ok()?;
    let parts: Vec<String> = rel.components().map(|c| c.as_os_str().to_string_lossy().into_owned()).collect();
    Some(parts.join("/"))
}

/// SQLite 一致性快照: `VACUUM INTO`, 只读打开, 别的进程 (Hermes / tool-bridge) 在写也安全。
pub fn snapshot_sqlite(src: &Path, dst: &Path) -> Result<(), String> {
    if let Some(p) = dst.parent() {
        fs::create_dir_all(p).map_err(|e| e.to_string())?;
    }
    let _ = fs::remove_file(dst);
    let conn = Connection::open_with_flags(src, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|e| format!("打开 {} 失败: {e}", src.display()))?;
    let _ = conn.busy_timeout(std::time::Duration::from_secs(30));
    conn.execute("VACUUM INTO ?1", [dst.to_string_lossy().as_ref()])
        .map_err(|e| format!("{} 做快照失败: {e}", src.display()))?;
    Ok(())
}

/// 追加写的 JSONL 可能正写到一半: 只取到最后一个换行 (整行)。其余文件取全长。
pub fn stable_len(path: &Path) -> Result<u64, String> {
    let len = fs::metadata(path).map_err(|e| e.to_string())?.len();
    if !path.to_string_lossy().ends_with(".jsonl") || len == 0 {
        return Ok(len);
    }
    let mut f = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut end = len;
    let mut buf = vec![0u8; 64 * 1024];
    while end > 0 {
        let start = end.saturating_sub(buf.len() as u64);
        let n = (end - start) as usize;
        f.seek(SeekFrom::Start(start)).map_err(|e| e.to_string())?;
        f.read_exact(&mut buf[..n]).map_err(|e| e.to_string())?;
        if let Some(i) = buf[..n].iter().rposition(|&b| b == b'\n') {
            return Ok(start + i as u64 + 1);
        }
        end = start;
    }
    Ok(0)
}

pub fn is_text(name: &str) -> bool {
    let n = name.to_ascii_lowercase();
    [".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".csv", ".toml"].iter().any(|e| n.ends_with(e))
}

pub fn is_db_file(name: &str) -> bool {
    is_sqlite(name)
}

// ── 路径改写 (§6.3) ─────────────────────────────────────

/// 旧机的一个目录前缀 → 新机的。按旧前缀从长到短试 (Hermes / .catfish 在 home 前面)。
#[derive(Debug, Clone)]
pub struct Rewrite {
    pairs: Vec<(String, String)>,
    /// 旧机是 Windows、新机不是: 改写过的路径后半截 `\` 换成 `/`
    win_to_posix: bool,
}

impl Rewrite {
    pub fn new(old: [&str; 3], new: [&str; 3], old_windows: bool, new_windows: bool) -> Rewrite {
        let mut pairs = vec![];
        for (o, n) in old.iter().zip(new.iter()) {
            if o.is_empty() || o == n {
                continue;
            }
            // 原样 / JSON 转义过的反斜杠 / 正斜杠写法, 三种都认
            let n_fwd = n.replace('\\', "/");
            pairs.push((o.to_string(), n.to_string()));
            if o.contains('\\') {
                pairs.push((o.replace('\\', "\\\\"), n.replace('\\', "\\\\")));
                pairs.push((o.replace('\\', "/"), n_fwd.clone()));
            }
        }
        pairs.sort_by_key(|p| std::cmp::Reverse(p.0.len()));
        pairs.dedup();
        Rewrite { pairs, win_to_posix: old_windows && !new_windows }
    }

    /// 返回 (改写后的文本, 改了几处)。前缀后面必须紧跟分隔符或结束符, 防止 `/Users/li` 误改 `/Users/lily`。
    pub fn apply(&self, text: &str) -> (String, usize) {
        if self.pairs.is_empty() {
            return (text.to_string(), 0);
        }
        let mut out = String::with_capacity(text.len());
        let mut hits = 0;
        let mut i = 0;
        'outer: while i < text.len() {
            for (o, n) in &self.pairs {
                if text[i..].starts_with(o.as_str()) {
                    let after = text[i + o.len()..].chars().next();
                    if after.map_or(true, |c| matches!(c, '/' | '\\' | '"' | '\'' | ' ' | '\n' | '\r' | '\t' | ',' | ')' | ']' | '}' | '`' | '|')) {
                        out.push_str(n);
                        i += o.len();
                        hits += 1;
                        if self.win_to_posix {
                            // 后半截到下一个结束符为止, 反斜杠 (含 JSON 转义的 `\\`) 换成 `/`
                            while i < text.len() {
                                let c = text[i..].chars().next().unwrap();
                                if matches!(c, '"' | '\'' | '\n' | '\r' | '\t' | ',' | ')' | ']' | '}' | '`' | '|') {
                                    break;
                                }
                                if c == '\\' {
                                    out.push('/');
                                    i += if text[i + 1..].starts_with('\\') { 2 } else { 1 };
                                } else {
                                    out.push(c);
                                    i += c.len_utf8();
                                }
                            }
                        }
                        continue 'outer;
                    }
                }
            }
            let c = text[i..].chars().next().unwrap();
            out.push(c);
            i += c.len_utf8();
        }
        (out, hits)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jsonl_只取整行() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("a.jsonl");
        fs::write(&p, b"{\"a\":1}\n{\"b\":2}\n{\"c\":").unwrap();
        assert_eq!(stable_len(&p).unwrap(), 16);
        let m = t.path().join("a.md");
        fs::write(&m, b"no newline").unwrap();
        assert_eq!(stable_len(&m).unwrap(), 10);
    }

    #[test]
    fn 路径改写_前缀边界_mac到mac() {
        let rw = Rewrite::new(
            ["/Users/li", "/Users/li/.catfish", "/Users/li/.hermes"],
            ["/Users/wang", "/Users/wang/.catfish", "/Users/wang/.hermes"],
            false,
            false,
        );
        let (s, n) = rw.apply(r#"{"p":"/Users/li/.catfish/uploads/a.pdf","q":"/Users/lily/x","r":"/Users/li"}"#);
        assert_eq!(s, r#"{"p":"/Users/wang/.catfish/uploads/a.pdf","q":"/Users/lily/x","r":"/Users/wang"}"#);
        assert_eq!(n, 2);
    }

    #[test]
    fn 路径改写_windows到mac_含json转义() {
        let rw = Rewrite::new(
            [r"C:\Users\li", r"C:\Users\li\.catfish", r"C:\Users\li\AppData\Local\hermes"],
            ["/Users/li", "/Users/li/.catfish", "/Users/li/.hermes"],
            true,
            false,
        );
        let (s, n) = rw.apply(r#"{"p":"C:\\Users\\li\\.catfish\\out\\a.xlsx"} mail_dir: C:\Users\li\Mail"#);
        assert_eq!(s, r#"{"p":"/Users/li/.catfish/out/a.xlsx"} mail_dir: /Users/li/Mail"#);
        assert_eq!(n, 2);
    }

    #[test]
    fn 路径改写_mac到windows_正斜杠可用() {
        let rw = Rewrite::new(
            ["/Users/li", "/Users/li/.catfish", "/Users/li/.hermes"],
            [r"C:\Users\li", r"C:\Users\li\.catfish", r"C:\Users\li\AppData\Local\hermes"],
            false,
            true,
        );
        let (s, _) = rw.apply("kept_path: /Users/li/.catfish/uploads/a.pdf");
        assert_eq!(s, r"kept_path: C:\Users\li\.catfish/uploads/a.pdf");
    }

    #[test]
    fn sqlite_快照() {
        let t = tempfile::tempdir().unwrap();
        let src = t.path().join("a.db");
        let c = Connection::open(&src).unwrap();
        c.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE t(x); INSERT INTO t VALUES (1),(2);").unwrap();
        let dst = t.path().join("snap/a.db");
        snapshot_sqlite(&src, &dst).unwrap();
        let n: i64 = Connection::open(&dst).unwrap().query_row("SELECT COUNT(*) FROM t", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 2);
    }
}
