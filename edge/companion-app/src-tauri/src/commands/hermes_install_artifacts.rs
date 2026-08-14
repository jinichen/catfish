//! 离线安装包 (runtime / deps / catfish-email) 的定位与完整性判断。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};

pub(crate) const RUNTIME_ARCHIVES: [&str; 4] = [
    "cpython-3.11.15-embed.tar.gz",
    "hermes-agent-bundle.tar.gz",
    "node-embed.tar.gz",
    "chromium-embed.tar.gz",
];

/// catfish-email 分发包 (build-mac-resources.sh 产出)。
///
/// 里面是**构建好的 wheel** + hermes-skill, 不是源码 —— 见
/// `install_catfish_email` 的说明。
///
/// 单独一个常量、**不进 RUNTIME_ARCHIVES** —— 那个数组是"离线运行时齐不齐"的
/// 判据 (决定 hermes 走离线还是联网装), 邮件是附加功能, 不该影响那个决策。
pub(crate) const CATFISH_EMAIL_ARCHIVE: &str = "catfish-email-dist.tar.gz";
/// hermes venv 的额外 Python 依赖 (jieba / playwright + 它们的依赖)。
/// 8/5: 装机流程里这两个的安装语句一直是 0 处 —— 见 install_hermes_deps。
pub(crate) const HERMES_DEPS_ARCHIVE: &str = "hermes-deps-dist.tar.gz";

#[derive(Clone, Debug)]
pub(crate) struct RuntimeArtifacts {
    pub(crate) dir: PathBuf,
    pub(crate) install_sh: PathBuf,
    pub(crate) uv: PathBuf,
    pub(crate) python_tar: Option<PathBuf>,
    pub(crate) hermes_tar: Option<PathBuf>,
    pub(crate) node_tar: Option<PathBuf>,
    pub(crate) chromium_tar: Option<PathBuf>,
    /// hermes venv 额外依赖包 (8/5)。
    pub(crate) deps_tar: Option<PathBuf>,
    /// catfish-email 源码包 (7/30)。
    ///
    /// **不计入 archive_count / is_complete_bundle** —— 那两个判的是
    /// "离线运行时齐不齐"(决定要不要联网装 hermes), 而邮件是 catfish 自己的
    /// 附加功能, 缺了不该让整个 hermes 走联网路径。
    pub(crate) email_tar: Option<PathBuf>,
}

impl RuntimeArtifacts {
    pub(crate) fn from_dir(dir: PathBuf) -> Self {
        Self {
            install_sh: dir.join("install.sh"),
            uv: dir.join("uv"),
            python_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[0])),
            hermes_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[1])),
            node_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[2])),
            chromium_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[3])),
            email_tar: usable_artifact(&dir.join(CATFISH_EMAIL_ARCHIVE)),
            deps_tar: usable_artifact(&dir.join(HERMES_DEPS_ARCHIVE)),
            dir,
        }
    }

    pub(crate) fn archive_count(&self) -> usize {
        [
            &self.python_tar,
            &self.hermes_tar,
            &self.node_tar,
            &self.chromium_tar,
        ]
        .into_iter()
        .filter(|p| p.is_some())
        .count()
    }

    pub(crate) fn is_complete_bundle(&self) -> bool {
        self.archive_count() == RUNTIME_ARCHIVES.len()
    }

    pub(crate) fn validate_bootstrap_tools(&self) -> Result<()> {
        for path in [&self.install_sh, &self.uv] {
            let meta = std::fs::metadata(path)
                .with_context(|| format!("运行时缺文件: {}", path.display()))?;
            if meta.len() <= 1024 {
                anyhow::bail!(
                    "运行时文件过小 (<= 1KB，可能仍是 placeholder): {}",
                    path.display()
                );
            }
        }
        Ok(())
    }
}

fn usable_artifact(path: &Path) -> Option<PathBuf> {
    std::fs::metadata(path)
        .ok()
        .filter(|m| m.is_file() && m.len() > 1024)
        .map(|_| path.to_path_buf())
}

/// 选择运行时时按“归档完整度”排序，而不是无条件偏爱 App bundle。
///
/// 旧逻辑看到 bundle 内 `install.sh + uv` 就立即返回，即使它是 0/4；结果会
/// 永远忽略 `~/.catfish/runtime` 中用户已经准备好的 4/4 离线包。
pub(crate) fn resolve_runtime_dir_for_home(resource_dir: &Path, home: Option<&Path>) -> Result<PathBuf> {
    let bundle = resource_dir.join("resources").join("mac");
    let external = home.map(|h| h.join(".catfish").join("runtime"));
    let mut candidates: Vec<(usize, usize, PathBuf)> = Vec::new();
    let mut tried = Vec::new();

    // 第二个排序字段是 tie-break：完整度相同时沿用 bundle 优先，保持兼容。
    for (bundle_preference, candidate) in [(1usize, Some(bundle)), (0usize, external)] {
        let Some(candidate) = candidate else {
            continue;
        };
        let artifacts = RuntimeArtifacts::from_dir(candidate.clone());
        let valid = usable_artifact(&artifacts.install_sh).is_some()
            && usable_artifact(&artifacts.uv).is_some();
        tried.push(format!(
            "{} (tools={}, archives={}/4)",
            candidate.display(),
            if valid { "ok" } else { "missing" },
            artifacts.archive_count()
        ));
        if valid {
            candidates.push((artifacts.archive_count(), bundle_preference, candidate));
        }
    }

    candidates.sort_by(|a, b| (b.0, b.1).cmp(&(a.0, a.1)));
    if let Some((archive_count, _, selected)) = candidates.into_iter().next() {
        log::info!(
            "[runtime] 选择 {} · 离线归档 {archive_count}/4",
            selected.display()
        );
        return Ok(selected);
    }

    anyhow::bail!(
        "找不到有效运行时目录 (需要 install.sh + uv > 1KB)。已尝试:\n  {}",
        tried.join("\n  ")
    )
}

pub(crate) fn resolve_runtime_dir(resource_dir: &Path) -> Result<PathBuf> {
    let home = crate::util::paths::home_env().ok().map(PathBuf::from);
    resolve_runtime_dir_for_home(resource_dir, home.as_deref())
}
