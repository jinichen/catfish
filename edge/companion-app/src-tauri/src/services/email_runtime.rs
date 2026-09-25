//! Resolve the validated isolated Windows email environment.
use std::path::{Path, PathBuf};

pub fn scripts(hermes_home: &Path) -> Option<PathBuf> {
    let root = hermes_home.join("email-runtime");
    let generation = std::fs::read_to_string(root.join("current.txt")).ok()?;
    let generation = generation.trim();
    let suffix = generation.strip_prefix("env-")?;
    if suffix.len() != 32 || !suffix.bytes().all(|c| c.is_ascii_hexdigit()) { return None; }
    let scripts = root.join(generation).join("Scripts");
    if scripts.join("python.exe").is_file() && scripts.join("catfish-email.exe").is_file() {
        Some(scripts)
    } else { None }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_published_complete_generation_is_used() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("email-runtime");
        let name = "env-0123456789abcdef0123456789abcdef";
        let bin = root.join(name).join("Scripts");
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(root.join("current.txt"), name).unwrap();
        assert!(scripts(dir.path()).is_none());
        for exe in ["python.exe", "catfish-email.exe"] { std::fs::write(bin.join(exe), b"test").unwrap(); }
        assert_eq!(scripts(dir.path()), Some(bin));
        std::fs::write(root.join("current.txt"), "../hermes-agent/venv").unwrap();
        assert!(scripts(dir.path()).is_none());
    }
}
