//! Windows optional components are independent of the live Hermes core.
use anyhow::{Context, Result};
use std::{ffi::OsString, io::Write, path::Path};
use super::{run_hidden_powershell, run_hidden_status, open_bootstrap_log};
use crate::commands::hermes_install_artifacts::{resolve_addon_runtime_dir, RuntimeArtifacts, HERMES_DEPS_ARCHIVE};
use crate::commands::hermes_install_state::{BootstrapPaths, write_bytes_atomic};
use crate::commands::hermes_install_steps::install_hermes_deps;
use crate::services::catfish_paths::{hermes_venv_python, hermes_venv_tool};

pub(super) fn install_optional_components(resource_dir: &Path, paths: &BootstrapPaths) -> Vec<String> {
    let resources = match resolve_addon_runtime_dir(resource_dir) {
        Ok(resources) => resources,
        Err(error) => return vec![format!("附加组件资源不可用: {error:#}")],
    };
    let artifacts = RuntimeArtifacts::from_dir(resources.clone());
    let mut failures = Vec::new();

    let deps_ready = run_hidden_status(
        &hermes_venv_python(&paths.install_dir),
        &["-c", crate::commands::hermes_install_artifacts::HERMES_EXTRA_IMPORT_CHECK],
        "hermes 额外依赖 (jieba/playwright/watchdog)",
    );
    if !deps_ready {
        match artifacts.deps_tar.as_ref() {
            Some(_) => {
                if let Err(error) = install_hermes_deps(&artifacts, paths) {
                    failures.push(format!("{}: {error:#}", HERMES_DEPS_ARCHIVE));
                }
            }
            None => failures.push(format!("缺少 {HERMES_DEPS_ARCHIVE}")),
        }
    }

    let email_ready = optional_components_ready(paths);
    let script = resources.join("install-catfish-email.ps1");
    let marker = paths.install_dir.join(".catfish-email-installed.sha256");
    let expected = artifacts.email_tar.as_ref().and_then(|archive| {
        crate::services::addon_fingerprint::fingerprint(&[archive, &script]).ok()
    });
    let email_current = expected.as_ref().is_some_and(|hash| {
        crate::services::addon_fingerprint::matches(&marker, hash, email_ready)
    });
    if !email_current {
        match artifacts.email_tar.as_ref() {
            Some(archive) if script.is_file() => {
                let args = vec![
                    OsString::from("-DistributionPath"),
                    archive.clone().into_os_string(),
                ];
                let installed = run_hidden_powershell(&script, &args, paths, "安装/更新 catfish-email", None)
                    .and_then(|()| {
                        anyhow::ensure!(optional_components_ready(paths), "邮件组件更新后自检失败");
                        let hash = expected.as_ref().context("无法读取邮件安装资源指纹")?;
                        crate::commands::hermes_install_state::write_bytes_atomic(&marker, hash.as_bytes())
                    });
                if let Err(error) = installed {
                    failures.push(format!("catfish-email: {error:#}"));
                }
            }
            Some(_) => failures.push("缺少 install-catfish-email.ps1".to_owned()),
            None => failures.push("缺少 catfish-email-dist.tar.gz".to_owned()),
        }
    }

    let reader_exe = hermes_venv_tool(&paths.install_dir, "catfish-wechat-reader");
    let script = resources.join("install-wechat-reader.ps1");
    let marker = paths.install_dir.join(".catfish-wechat-reader-installed.sha256");
    let expected = artifacts.wechat_reader_tar.as_ref().and_then(|archive| {
        crate::services::addon_fingerprint::fingerprint(&[archive, &script]).ok()
    });
    // An exe left behind by a failed safety check is NOT a successful install.
    let reader_current = expected.as_ref().is_some_and(|hash| {
        crate::services::addon_fingerprint::matches(&marker, hash, reader_exe.is_file())
            && run_hidden_status(
                &hermes_venv_python(&paths.install_dir),
                &["-I", "-c", r#"import json, subprocess, sys
r = json.loads(subprocess.check_output([sys.argv[1], 'doctor', '--json'], timeout=20))
assert isinstance(r, dict) and type(r.get('protocol_version')) is int and r['protocol_version'] == 1
assert all(r.get(k) is True for k in ('read_only', 'secure_key_store', 'ephemeral_plaintext_cache'))
assert r.get('modifies_wechat_app') is False
"#, &reader_exe.to_string_lossy()],
                "微信读取器安全协议",
            )
    });
    if !reader_current {
        match artifacts.wechat_reader_tar.as_ref() {
            Some(archive) if script.is_file() => {
                let args = vec![
                    OsString::from("-DistributionPath"),
                    archive.clone().into_os_string(),
                ];
                let installed = run_hidden_powershell(
                    &script, &args, paths, "安装/校验 catfish-wechat-reader", None,
                ).and_then(|()| {
                    let hash = expected.as_ref().context("无法读取微信读取器资源指纹")?;
                    write_bytes_atomic(&marker, hash.as_bytes())
                });
                if let Err(error) = installed {
                    failures.push(format!("catfish-wechat-reader: {error:#}"));
                }
            }
            Some(_) => failures.push("缺少 install-wechat-reader.ps1".to_owned()),
            None => failures.push("缺少 catfish-wechat-reader-dist.tar.gz".to_owned()),
        }
    }

    failures
}

/// 只有邮件组件也升级完成后，Windows 后台服务才允许启动。
///
/// 核心 Hermes 的完成标记不能代表附加组件已经更新：旧机器可能保留着
/// 没有 `discover` 子命令的 catfish-email，若此处只看核心标记，邮件扫描器
/// 会过早启动并继续使用旧入口。
pub(crate) fn optional_components_ready(paths: &BootstrapPaths) -> bool {
    let Some(scripts) = crate::services::email_runtime::scripts(&paths.hermes_home) else {
        return false;
    };
    run_hidden_status(&scripts.join("catfish-email.exe"), &["discover", "--help"], "邮件发现 CLI")
        && run_hidden_status(&scripts.join("python.exe"),
            &["-c", "import catfish_email.discovery, win32api, pythoncom"], "隔离邮件运行环境")

}

pub(crate) fn ensure_optional_components(resource_dir: &Path, paths: &BootstrapPaths) -> Result<()> {
    let mut log = open_bootstrap_log(paths)?;
    writeln!(log, "Component check: executable={:?}, resources={}, runtime={}",
        std::env::current_exe(), resource_dir.display(), paths.install_dir.display())?;
    let failures = install_optional_components(resource_dir, paths);
    writeln!(log, "Component check result: {:?}", failures)?;
    for failure in &failures {
        log::warn!("[windows-bootstrap] 附加组件未就绪: {failure}");
    }
    anyhow::ensure!(failures.is_empty(), "附加组件准备失败: {}", failures.join("; "));
    Ok(())
}
