//! Build the std-only GUI maintenance helper and a WiX fragment with an absolute source.
pub fn build() {
    use std::{env, fs, path::PathBuf, process::Command};
    let sources = ["wix/maintenance.rs", "wix/windows-maintenance.ps1", "build_windows.rs"];
    for source in sources { println!("cargo:rerun-if-changed={source}"); }
    let dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap()).join("wix/generated");
    fs::create_dir_all(&dir).unwrap();
    let binary = dir.join("catfish-maintenance.exe");
    let modified = binary.metadata().and_then(|m| m.modified()).ok();
    if sources.iter().any(|p| modified.is_none() || fs::metadata(p).unwrap().modified().ok() > modified) {
        let status = Command::new(env::var_os("RUSTC").unwrap())
            .args(["--edition=2021", "--target", &env::var("TARGET").unwrap(), "-Dwarnings", "-C", "opt-level=s", "-C", "target-feature=+crt-static", "wix/maintenance.rs", "-o"])
            .arg(&binary).status().expect("build MSI maintenance helper");
        assert!(status.success(), "MSI maintenance helper failed to compile");
    }
    let escaped = binary.to_string_lossy().replace('&', "&amp;").replace('"', "&quot;");
    let fragment = format!(r#"<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Fragment>
    <Binary Id="CatfishMaintenance" SourceFile="{escaped}" />
    <CustomAction Id="CatfishMaintainInstall" BinaryKey="CatfishMaintenance" Execute="deferred" Impersonate="yes" Return="check" ExeCommand='install "[INSTALLDIR]."' />
    <CustomAction Id="CatfishMaintainUninstall" BinaryKey="CatfishMaintenance" Execute="deferred" Impersonate="yes" Return="check" ExeCommand='uninstall "[INSTALLDIR]."' />
    <InstallExecuteSequence>
      <Custom Action="CatfishMaintainInstall" Before="InstallFiles">NOT REMOVE</Custom>
      <Custom Action="CatfishMaintainUninstall" Before="RemoveFiles">REMOVE="ALL"</Custom>
    </InstallExecuteSequence>
  </Fragment>
</Wix>
"#);
    let path = dir.join("maintenance.wxs");
    if fs::read_to_string(&path).ok().as_deref() != Some(&fragment) { fs::write(path, fragment).unwrap(); }
}
