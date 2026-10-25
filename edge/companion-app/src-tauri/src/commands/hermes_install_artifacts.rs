//! 离线安装包 (runtime / deps / catfish-email) 的定位与完整性判断。
//!
//! 2026-08-15 从 hermes_install.rs 切出来。纯搬迁, 逻辑一行未改。

use anyhow::Context;
use anyhow::Result;
use std::path::{Path, PathBuf};

/// 10/9: 去掉了 node-embed.tar.gz —— 员工机运行时不需要 Node (hermes 只跑 Python
/// 网关; 鲶鱼浏览器走 Python Playwright + chromium-embed), 见
/// scripts/build-mac-resources.sh 文件头的实测记录。
/// 10/14 (hermes v0.22.0): Python 3.11.15 → 3.14.7 (上游依赖只在 3.14 上装); hermes 归档里
/// 带 wheelhouse/ + catfish-requirements.txt + install-stamp.json, Companion 自己装
/// (hermes_install_native.rs), 不再有 install.sh。
pub(crate) const RUNTIME_ARCHIVES: [&str; 3] = [
    "cpython-3.14.7-embed.tar.gz",
    "hermes-agent-bundle.tar.gz",
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
/// 员工选择的聊天导出文件读取器，零依赖 wheel，不含微信解密能力。
pub(crate) const CATFISH_WECHAT_READER_ARCHIVE: &str = "catfish-wechat-reader-dist.tar.gz";

/// hermes venv 里要装的额外包 —— **唯一**清单在 `src-tauri/hermes-extra-packages.txt`,
/// 安装自检和打包脚本 (scripts/build_hermes_wheelhouse.py, mac / Windows 共用) 都从那里读。
///
/// 9/23 加 watchdog: local-search 的文件监听要它 (catfish_search/watcher.py),
/// 客户机器上 local-search 跑在 hermes venv 里 (没有源码树, 也就没有它自己的
/// venv), 不装就是"本机文件搜索"起来了但从不更新索引。
/// 9/28 加文件解析那几个 (pypdfium2 / openpyxl / python-docx / python-pptx / xlrd):
/// 聊天上传文件原来只在开发机的网关 venv 里解析得了。Rust 这边 9/23 收成了一处,
/// 打包脚本那边还各写一遍 —— 这次连打包一起收进那个文件。
const HERMES_EXTRA_LIST: &str = include_str!("../../hermes-extra-packages.txt");

/// (pip 需求, import 判据) —— 跳过空行和 # 注释。
fn hermes_extra_entries() -> impl Iterator<Item = (&'static str, &'static str)> {
    HERMES_EXTRA_LIST
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .filter_map(|line| {
            let mut cols = line.split_whitespace();
            Some((cols.next()?, cols.next()?))
        })
}

/// 额外包的 pip 包名 (去掉版本条件), 小写、`_` `.` 换成 `-` —— 跟依赖清单里的写法对得上。
pub(crate) fn hermes_extra_package_names() -> Vec<String> {
    hermes_extra_entries()
        .map(|(req, _)| {
            let name = req.split(|c: char| "<>=!~;[ ".contains(c)).next().unwrap_or(req);
            name.to_ascii_lowercase().replace(['_', '.'], "-")
        })
        .collect()
}

/// 装没装的判据: import 得了才算。playwright 要 sync_api (装了一半时顶层能 import)。
pub(crate) fn hermes_extra_import_check() -> String {
    let modules: Vec<&str> = hermes_extra_entries().map(|(_, module)| module).collect();
    format!("import {}", modules.join(", "))
}

#[derive(Clone, Debug)]
pub(crate) struct RuntimeArtifacts {
    pub(crate) dir: PathBuf,
    pub(crate) uv: PathBuf,
    pub(crate) python_tar: Option<PathBuf>,
    pub(crate) hermes_tar: Option<PathBuf>,
    pub(crate) chromium_tar: Option<PathBuf>,
    /// catfish-email 源码包 (7/30)。
    ///
    /// **不计入 archive_count / is_complete_bundle** —— 那两个判的是
    /// "离线运行时齐不齐"(决定要不要联网装 hermes), 而邮件是 catfish 自己的
    /// 附加功能, 缺了不该让整个 hermes 走联网路径。
    pub(crate) email_tar: Option<PathBuf>,
    /// 安全聊天导出读取器 wheel 分发包；不计入 Hermes 核心运行时完整度。
    pub(crate) wechat_reader_tar: Option<PathBuf>,
}

impl RuntimeArtifacts {
    pub(crate) fn from_dir(dir: PathBuf) -> Self {
        let uv_name = if cfg!(target_os = "windows") {
            "uv.exe"
        } else {
            "uv"
        };
        Self {
            uv: dir.join(uv_name),
            python_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[0])),
            hermes_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[1])),
            chromium_tar: usable_artifact(&dir.join(RUNTIME_ARCHIVES[2])),
            email_tar: usable_artifact(&dir.join(CATFISH_EMAIL_ARCHIVE)),
            wechat_reader_tar: usable_artifact(&dir.join(CATFISH_WECHAT_READER_ARCHIVE)),
            dir,
        }
    }

    pub(crate) fn archive_count(&self) -> usize {
        [
            &self.python_tar,
            &self.hermes_tar,
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
        for path in [&self.uv] {
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
/// 旧逻辑看到 bundle 内 `install.sh + uv` 就立即返回，即使它是 0/N；结果会
/// 永远忽略 `~/.catfish/runtime` 中用户已经准备好的完整离线包。
pub(crate) fn resolve_runtime_dir_for_home(
    resource_dir: &Path,
    home: Option<&Path>,
) -> Result<PathBuf> {
    let platform = if cfg!(windows) { "windows" } else { "mac" };
    let bundle = resource_dir.join("resources").join(platform);
    let external = home.map(|h| h.join(".catfish").join("runtime"));
    let mut candidates: Vec<(usize, usize, PathBuf)> = Vec::new();
    let mut tried = Vec::new();

    // 第二个排序字段是 tie-break：完整度相同时沿用 bundle 优先，保持兼容。
    for (bundle_preference, candidate) in [(1usize, Some(bundle)), (0usize, external)] {
        let Some(candidate) = candidate else {
            continue;
        };
        let artifacts = RuntimeArtifacts::from_dir(candidate.clone());
        let valid = usable_artifact(&artifacts.uv).is_some();
        tried.push(format!(
            "{} (tools={}, archives={}/{})",
            candidate.display(),
            if valid { "ok" } else { "missing" },
            artifacts.archive_count(),
            RUNTIME_ARCHIVES.len()
        ));
        if valid {
            candidates.push((artifacts.archive_count(), bundle_preference, candidate));
        }
    }

    candidates.sort_by(|a, b| (b.0, b.1).cmp(&(a.0, a.1)));
    if let Some((archive_count, _, selected)) = candidates.into_iter().next() {
        log::info!(
            "[runtime] 选择 {} · 离线归档 {archive_count}/{}",
            selected.display(),
            RUNTIME_ARCHIVES.len()
        );
        return Ok(selected);
    }

    anyhow::bail!(
        "找不到有效运行时目录 (需要 uv > 1KB)。已尝试:\n  {}",
        tried.join("\n  ")
    )
}

pub(crate) fn resolve_runtime_dir(resource_dir: &Path) -> Result<PathBuf> {
    let home = crate::util::paths::home_env().ok().map(PathBuf::from);
    resolve_runtime_dir_for_home(resource_dir, home.as_deref())
}

/// 找附加组件的资源目录。
///
/// Windows 的 MSI 由 WiX 直接安装 Hermes 核心，资源目录只有 `install.ps1`
/// 和 `uv.exe`，没有 Unix 的 `install.sh` / `uv`。旧的补装路径复用
/// `resolve_runtime_dir`，因此 Windows 即使有邮件归档也会被误判成“没有有效
/// 运行时”，重启 Companion 无法补装邮件。附加组件只需要自己的归档，不能用
/// Hermes 核心安装工具作为判据。
#[cfg(any(target_os = "windows", test))]
// 9/17: 唯一调用方是 hermes_install_windows::install_optional_components, 在 mac
// 的 test 构建里那条链是死的 (见 hermes_install_windows.rs 顶部说明)。
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub(crate) fn resolve_addon_runtime_dir(resource_dir: &Path) -> Result<PathBuf> {
    let home = crate::util::paths::home_env().ok().map(PathBuf::from);
    let bundle = resource_dir.join("resources").join("windows");
    let external = home.map(|h| h.join(".catfish").join("runtime"));
    let mut candidates: Vec<(usize, usize, PathBuf)> = Vec::new();
    let mut tried = Vec::new();

    for (bundle_preference, candidate) in [(1usize, Some(bundle)), (0usize, external)] {
        let Some(candidate) = candidate else {
            continue;
        };
        let artifacts = RuntimeArtifacts::from_dir(candidate.clone());
        let addon_count = [
            &artifacts.email_tar,
            &artifacts.wechat_reader_tar,
        ]
        .into_iter()
        .filter(|path| path.is_some())
        .count();
        tried.push(format!("{} (附加归档={addon_count}/2)", candidate.display()));
        if addon_count > 0 {
            candidates.push((addon_count, bundle_preference, candidate));
        }
    }

    candidates.sort_by(|a, b| (b.0, b.1).cmp(&(a.0, a.1)));
    if let Some((addon_count, _, selected)) = candidates.into_iter().next() {
        log::info!(
            "[runtime] 选择 {} · 附加归档 {addon_count}/2",
            selected.display()
        );
        return Ok(selected);
    }

    anyhow::bail!(
        "找不到附加组件资源目录。已尝试:\n  {}",
        tried.join("\n  ")
    )
}

#[cfg(test)]
mod extra_packages_tests {
    use super::*;

    #[test]
    fn 清单每行都是_需求_判据_可选sdist() {
        for line in HERMES_EXTRA_LIST.lines().map(str::trim) {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let cols: Vec<&str> = line.split_whitespace().collect();
            assert!(
                cols.len() == 2 || (cols.len() == 3 && cols[2] == "sdist"),
                "格式不对: {line}"
            );
        }
    }

    #[test]
    fn 原有的和文件解析要的都在() {
        let packages: Vec<&str> = hermes_extra_entries()
            .map(|(r, _)| r.split(['=', '<', '>']).next().unwrap_or(r))
            .collect();
        for want in ["jieba", "playwright", "watchdog", "PyYAML", "pypdfium2", "openpyxl", "python-docx"] {
            assert!(packages.contains(&want), "{packages:?} 缺 {want}");
        }
        assert_eq!(
            hermes_extra_import_check(),
            "import jieba, playwright.sync_api, watchdog.observers, yaml, pypdfium2, openpyxl, docx, pptx, xlrd, tzdata"
        );
    }
}
