//! A healthy import does not prove that an installed addon matches the new MSI.
use sha2::{Digest, Sha256};
use std::io::Read;
use std::path::Path;

pub fn fingerprint(files: &[&Path]) -> std::io::Result<String> {
    let mut hash = Sha256::new();
    for path in files {
        let mut file = std::fs::File::open(path)?;
        hash.update(file.metadata()?.len().to_le_bytes());
        let mut buffer = [0u8; 65536];
        loop {
            let count = file.read(&mut buffer)?;
            if count == 0 { break; }
            hash.update(&buffer[..count]);
        }
    }
    Ok(hex::encode(hash.finalize()))
}

pub fn matches(marker: &Path, expected: &str, healthy: bool) -> bool {
    healthy && std::fs::read_to_string(marker).is_ok_and(|value| value.trim() == expected)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upgrade_same_version_or_changed_script_requires_reinstall() {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("email.tar.gz");
        let script = dir.path().join("install.ps1");
        let marker = dir.path().join("installed.sha256");
        std::fs::write(&archive, "old wheel version 0.1.0").unwrap();
        std::fs::write(&script, "old installer").unwrap();
        let old = fingerprint(&[&archive, &script]).unwrap();
        assert!(!matches(&marker, &old, true));
        std::fs::write(&marker, &old).unwrap();
        assert!(matches(&marker, &old, true));
        assert!(!matches(&marker, &old, false));
        std::fs::write(&archive, "new wheel version 0.1.0").unwrap();
        let new = fingerprint(&[&archive, &script]).unwrap();
        assert!(!matches(&marker, &new, true));
        std::fs::write(&marker, &new).unwrap();
        std::fs::write(&script, "new installer").unwrap();
        assert!(!matches(&marker, &fingerprint(&[&archive, &script]).unwrap(), true));
    }
}
