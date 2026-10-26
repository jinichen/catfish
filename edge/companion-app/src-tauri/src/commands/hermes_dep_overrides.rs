//! hermes 依赖的安全覆盖 (10/25): 不升 hermes 本体, 单独把有漏洞的依赖换新。
//! 同一条路也管额外包清单后加的包 (10/25 第二次): 见下面「做法」第 1 条。
//!
//! # 为什么要这一层
//!
//! 版本清单在 `src-tauri/hermes-dep-overrides.txt` (打包脚本用它盖过上游 uv.lock, 理由见那个文件)。
//! 但新安装包到了老机器上, hermes 提交号没变 —— 不会整个重装, 装好的 `~/.hermes/hermes-agent/`
//! 里还是旧的依赖清单和旧 wheel。hermes_runtime_deps 原来的补装读的正是那两样,
//! 照原样补装只会把旧版本再装一遍。
//!
//! # 做法
//!
//! 1. 已装目录里的依赖清单低于覆盖版本, 或者缺了额外包清单 (hermes-extra-packages.txt) 里的包
//!    → 从安装包的 hermes 归档里**只解**依赖清单和 wheel 目录, 换掉已装的那两样
//!    ([`refresh_offline_deps_if_outdated`])。缺包这条是 10/25 本机撞出来的: 额外包清单后来加了 tzdata,
//!    已装的旧清单里没有, 补装照旧清单装还是没有 —— 每次启动都补装、每次都失败, 一直到安全换新顺带
//!    把新清单带进来才停。以后额外包清单再加包, 老机器也走这一步拿到。
//! 2. 运行环境里实际装的版本低于覆盖版本 → 跟缺包一样处理, 用 (已换新的) 清单离线补装
//!    ([`version_floor_check`] 拼进 hermes_runtime_deps 的 import 检查)。

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};

use super::hermes_install_artifacts::{hermes_extra_package_names, RuntimeArtifacts};
use super::hermes_install_native::{REQUIREMENTS_FILE, WHEELHOUSE_DIR};
use super::hermes_install_state::BootstrapPaths;

const OVERRIDES: &str = include_str!("../../hermes-dep-overrides.txt");

/// 解归档用的临时目录 (在 hermes_home 下, 跟安装目录同盘, 换进去是 rename)
const REFRESH_DIR: &str = ".catfish-deps-refresh";

fn norm(name: &str) -> String {
    name.trim().to_ascii_lowercase().replace(['_', '.'], "-")
}

/// [(规范化包名, 最低版本)]
pub(crate) fn overrides() -> Vec<(String, String)> {
    OVERRIDES
        .lines()
        .filter_map(|raw| {
            let line = raw.split('#').next()?.trim();
            let (name, version) = line.split_once("==")?;
            Some((norm(name), version.trim().to_owned()))
        })
        .collect()
}

/// 版本号里的数字段, 按段比大小 (2.15.1 > 2.13.0; 2.8.0 > 2.7.0)。
fn version_key(version: &str) -> Vec<u64> {
    version
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .map(|s| s.parse().unwrap_or(0))
        .collect()
}

fn below(have: &str, floor: &str) -> bool {
    version_key(have) < version_key(floor)
}

/// 依赖清单 (`包名==版本 \` 加 `--hash` 续行) 里低于覆盖版本的包, 格式 `包名 已钉版本 < 要求版本`。
/// 清单里没有的包不算 (有的覆盖只是预防, 见覆盖清单末尾)。
fn stale_pins(requirements: &str) -> Vec<String> {
    let floors = overrides();
    let mut out = Vec::new();
    for line in requirements.lines() {
        let Some((name, rest)) = line.split_once("==") else { continue };
        if line.starts_with([' ', '\t', '#', '-']) {
            continue;
        }
        let version = rest.split(|c: char| c.is_whitespace() || c == ';' || c == '\\').next().unwrap_or("");
        let name = norm(name);
        if let Some((_, floor)) = floors.iter().find(|(n, _)| *n == name) {
            if below(version, floor) {
                out.push(format!("{name} {version} < {floor}"));
            }
        }
    }
    out
}

/// 依赖清单里钉了版本的包名 (规范化)。
fn pinned_names(requirements: &str) -> Vec<String> {
    requirements
        .lines()
        .filter(|line| !line.starts_with([' ', '\t', '#', '-']))
        .filter_map(|line| line.split_once("==").map(|(name, _)| norm(name)))
        .collect()
}

/// 额外包清单里有、依赖清单里没有的包, 格式 `缺 <包名>`。
fn missing_extras(requirements: &str) -> Vec<String> {
    let pinned = pinned_names(requirements);
    hermes_extra_package_names()
        .into_iter()
        .filter(|name| !pinned.contains(name))
        .map(|name| format!("缺 {name}"))
        .collect()
}

/// 依赖清单要换新的理由 (空 = 不用换): 安全覆盖的包版本低 + 额外包缺。
fn outdated_reasons(requirements: &str) -> Vec<String> {
    let mut out = stale_pins(requirements);
    out.extend(missing_extras(requirements));
    out
}

/// 拼进运行环境检查的 Python 片段: 装了且低于覆盖版本就退出非零 (跟 import 失败同样处理)。
pub(crate) fn version_floor_check() -> String {
    let pairs: Vec<String> = overrides().iter().map(|(n, v)| format!("({n:?}, {v:?})")).collect();
    format!(
        "import importlib.metadata as _m, re as _re\n\
         _k = lambda v: [int(x) for x in _re.findall(r'\\d+', v)]\n\
         for _n, _v in [{}]:\n\
         \x20   try: _have = _m.version(_n)\n\
         \x20   except _m.PackageNotFoundError: continue\n\
         \x20   if _have is None: raise SystemExit(f'{{_n}} 安装记录坏了 (dist-info 没有 METADATA)')\n\
         \x20   if _k(_have) < _k(_v): raise SystemExit(f'{{_n}} {{_have}} < {{_v}}')",
        pairs.join(", ")
    )
}

/// 已装目录的依赖清单旧了 (见 [`outdated_reasons`]) 时, 换成安装包里那份 (连同 wheel 目录)。
/// 返回是否换过。安装包里那份也一样旧 (打包出了问题) 时报错, 不换 —— 换了也白换, 还会每次启动都解一遍。
pub(crate) fn refresh_offline_deps_if_outdated(artifacts: &RuntimeArtifacts, paths: &BootstrapPaths) -> Result<bool> {
    let installed = paths.install_dir.join(REQUIREMENTS_FILE);
    let Ok(text) = std::fs::read_to_string(&installed) else {
        return Ok(false); // 没有清单 = 不是离线装的那套, hermes_install 自己会处理
    };
    let stale = outdated_reasons(&text);
    if stale.is_empty() {
        return Ok(false);
    }
    let archive = artifacts.hermes_tar.as_ref().context("安装包里没有 hermes-agent-bundle.tar.gz")?;
    log::warn!("[hermes-deps] 已装的离线依赖清单旧了 ({}) —— 从安装包换新", stale.join(", "));
    let tmp = paths.hermes_home.join(REFRESH_DIR);
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp).with_context(|| format!("创建 {}", tmp.display()))?;
    let result = (|| -> Result<bool> {
        extract_deps(archive, &tmp)?;
        let (requirements, wheelhouse) = find_deps(&tmp)?;
        let still = outdated_reasons(&std::fs::read_to_string(&requirements)?);
        anyhow::ensure!(still.is_empty(), "安装包里的依赖清单也没换新 ({}), 检查打包", still.join(", "));
        swap_in(&requirements, &wheelhouse, &paths.install_dir)?;
        Ok(true)
    })();
    let _ = std::fs::remove_dir_all(&tmp);
    result
}

/// 只解依赖清单和 wheel 目录 (整包解开要多解出整份 hermes 源码)。Mac / Windows 自带的 tar 都是 bsdtar。
fn extract_deps(archive: &Path, dest: &Path) -> Result<()> {
    let mut untar = crate::services::process::background_command("tar");
    untar
        .arg("-xzf")
        .arg(archive)
        .arg("-C")
        .arg(dest)
        .arg("--include")
        .arg(format!("*{REQUIREMENTS_FILE}"))
        .arg("--include")
        .arg(format!("*{WHEELHOUSE_DIR}/*"));
    let status = untar.status().context("启动 tar")?;
    anyhow::ensure!(status.success(), "从 {} 解依赖失败: {status}", archive.display());
    Ok(())
}

/// 归档顶层有没有一层目录 (hermes-agent-src/) 都认: 找清单所在的目录, wheel 目录在它旁边。
fn find_deps(root: &Path) -> Result<(PathBuf, PathBuf)> {
    let mut dirs = vec![root.to_path_buf()];
    for _ in 0..3 {
        let mut next = Vec::new();
        for dir in dirs {
            if dir.join(REQUIREMENTS_FILE).is_file() && dir.join(WHEELHOUSE_DIR).is_dir() {
                return Ok((dir.join(REQUIREMENTS_FILE), dir.join(WHEELHOUSE_DIR)));
            }
            if let Ok(entries) = std::fs::read_dir(&dir) {
                next.extend(entries.flatten().map(|e| e.path()).filter(|p| p.is_dir()));
            }
        }
        dirs = next;
    }
    anyhow::bail!("安装包的 hermes 归档里没找到 {REQUIREMENTS_FILE} + {WHEELHOUSE_DIR}/")
}

/// 先换 wheel 目录、最后换清单: 中途断了, 清单还是旧的, 下次启动会重来。
fn swap_in(requirements: &Path, wheelhouse: &Path, install_dir: &Path) -> Result<()> {
    let target = install_dir.join(WHEELHOUSE_DIR);
    let old = install_dir.join(format!("{WHEELHOUSE_DIR}.old"));
    let _ = std::fs::remove_dir_all(&old);
    if target.exists() {
        std::fs::rename(&target, &old).with_context(|| format!("挪开旧的 {}", target.display()))?;
    }
    std::fs::rename(wheelhouse, &target).with_context(|| format!("换进新的 {}", target.display()))?;
    let _ = std::fs::remove_dir_all(&old);
    std::fs::rename(requirements, install_dir.join(REQUIREMENTS_FILE)).context("换进新的依赖清单")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 覆盖清单读得出来_含严重的_pyjwt() {
        let o = overrides();
        assert!(o.iter().any(|(n, v)| n == "pyjwt" && v == "2.15.1"), "{o:?}");
        assert!(o.iter().any(|(n, _)| n == "mcp-types"), "mcp 和 mcp-types 必须一起换: {o:?}");
        assert!(o.iter().all(|(n, v)| !n.is_empty() && !v.is_empty() && !n.starts_with('#')));
    }

    #[test]
    fn 版本按数字段比() {
        assert!(below("2.13.0", "2.15.1"));
        assert!(below("2.7.0", "2.8.0"));
        assert!(below("6.7.1", "6.9.1"));
        assert!(!below("2.15.1", "2.15.1"));
        assert!(!below("2.16.0", "2.15.1"));
        assert!(!below("2.10.0", "2.9.9"), "不能按字符串比");
    }

    #[test]
    fn 清单里认出旧版本_不管大小写下划线和续行() {
        let old = "PyJWT==2.13.0 \\\n    --hash=sha256:aa\nurllib3==2.7.0 ; sys_platform == 'darwin'\nmcp_types==2.0.0\njieba==0.42.1\n";
        assert_eq!(stale_pins(old), vec!["pyjwt 2.13.0 < 2.15.1", "urllib3 2.7.0 < 2.8.0", "mcp-types 2.0.0 < 2.3.0"]);
        let new = "pyjwt==2.15.1 \\\n    --hash=sha256:bb\nurllib3==2.8.0\nmcp-types==2.3.0\n";
        assert!(stale_pins(new).is_empty());
    }

    /// 一份齐全的依赖清单 (额外包都在), pyjwt 用给定版本
    fn reqs(pyjwt: &str) -> String {
        let mut out = format!("pyjwt=={pyjwt} \\\n    --hash=sha256:aa\n");
        for name in hermes_extra_package_names() {
            out.push_str(&format!("{name}==1.0 \\\n    --hash=sha256:bb\n"));
        }
        out
    }

    #[test]
    fn 额外包清单里有_依赖清单里没有的算缺() {
        let tzdata_missing = reqs("2.15.1").replace("tzdata==", "not-tzdata==");
        assert_eq!(missing_extras(&tzdata_missing), vec!["缺 tzdata"]);
        assert!(missing_extras(&reqs("2.15.1")).is_empty());
        // 清单里写法不一样也认: PyYAML → pyyaml, xlrd<2 → xlrd
        assert!(hermes_extra_package_names().contains(&"pyyaml".to_owned()));
        assert!(hermes_extra_package_names().contains(&"xlrd".to_owned()));
        assert!(outdated_reasons(&reqs("2.15.1")).is_empty());
        assert_eq!(outdated_reasons(&reqs("2.13.0")), vec!["pyjwt 2.13.0 < 2.15.1"]);
    }

    #[test]
    fn 检查片段带上所有覆盖包() {
        let check = version_floor_check();
        for (name, version) in overrides() {
            assert!(check.contains(&format!("({name:?}, {version:?})")), "{check}");
        }
    }

    /// 真的跑一遍 Python 片段: 没装的跳过, 装了且够新的放行 (pip 自己总装着, 版本远高于 0.0.1)。
    #[test]
    fn 检查片段_python_能跑() {
        let Ok(out) = std::process::Command::new("python3").arg("-c").arg(version_floor_check()).output() else {
            return; // 构建机没有 python3
        };
        assert!(out.status.success() || String::from_utf8_lossy(&out.stderr).contains(" < "),
            "片段本身有语法错误: {}", String::from_utf8_lossy(&out.stderr));
    }

    fn make_bundle(root: &Path, requirements: &str) -> PathBuf {
        let src = root.join("bundle").join("hermes-agent-src");
        std::fs::create_dir_all(src.join(WHEELHOUSE_DIR)).unwrap();
        std::fs::write(src.join(REQUIREMENTS_FILE), requirements).unwrap();
        std::fs::write(src.join(WHEELHOUSE_DIR).join("pyjwt-2.15.1-py3-none-any.whl"), "new").unwrap();
        std::fs::write(src.join("run_agent.py"), "# 不该被解出来").unwrap();
        let archive = root.join("hermes-agent-bundle.tar.gz");
        let ok = std::process::Command::new("tar")
            .arg("-czf").arg(&archive).arg("-C").arg(root.join("bundle")).arg("hermes-agent-src")
            .status().unwrap().success();
        assert!(ok);
        archive
    }

    fn installed(root: &Path, requirements: &str) -> BootstrapPaths {
        let home = root.join("home");
        let paths = BootstrapPaths::new(home.clone());
        std::fs::create_dir_all(paths.install_dir.join(WHEELHOUSE_DIR)).unwrap();
        std::fs::write(paths.install_dir.join(REQUIREMENTS_FILE), requirements).unwrap();
        std::fs::write(paths.install_dir.join(WHEELHOUSE_DIR).join("pyjwt-2.13.0-py3-none-any.whl"), "old").unwrap();
        paths
    }

    fn artifacts(root: &Path, archive: PathBuf) -> RuntimeArtifacts {
        RuntimeArtifacts {
            dir: root.to_path_buf(),
            uv: root.join("uv"),
            python_tar: None,
            hermes_tar: Some(archive),
            chromium_tar: None,
            email_tar: None,
            wechat_reader_tar: None,
        }
    }

    #[test]
    fn 老机器的离线依赖从安装包换新() {
        let tmp = tempfile::tempdir().unwrap();
        let _env = crate::util::test_env::env_lock();
        let archive = make_bundle(tmp.path(), &reqs("2.15.1"));
        let paths = installed(tmp.path(), &reqs("2.13.0"));

        assert!(refresh_offline_deps_if_outdated(&artifacts(tmp.path(), archive.clone()), &paths).unwrap());
        let reqs = std::fs::read_to_string(paths.install_dir.join(REQUIREMENTS_FILE)).unwrap();
        assert!(reqs.starts_with("pyjwt==2.15.1") && reqs.contains("tzdata=="), "{reqs}");
        let wheels: Vec<String> = std::fs::read_dir(paths.install_dir.join(WHEELHOUSE_DIR)).unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
        assert_eq!(wheels, vec!["pyjwt-2.15.1-py3-none-any.whl"], "旧 wheel 要清掉");
        assert!(!paths.install_dir.join("run_agent.py").exists(), "只解依赖, 不碰 hermes 源码");
        assert!(!paths.hermes_home.join(REFRESH_DIR).exists() && !paths.install_dir.join("wheelhouse.old").exists());

        // 换过之后不再换 (每次启动都解一遍归档不行)
        assert!(!refresh_offline_deps_if_outdated(&artifacts(tmp.path(), archive), &paths).unwrap());
    }

    /// 10/25 本机那种: 安全版本都够, 只是旧清单里没有后加的 tzdata
    #[test]
    fn 只缺额外包也从安装包换新() {
        let tmp = tempfile::tempdir().unwrap();
        let _env = crate::util::test_env::env_lock();
        let archive = make_bundle(tmp.path(), &reqs("2.15.1"));
        let paths = installed(tmp.path(), &reqs("2.15.1").replace("tzdata==", "not-tzdata=="));
        assert!(refresh_offline_deps_if_outdated(&artifacts(tmp.path(), archive), &paths).unwrap());
        let reqs = std::fs::read_to_string(paths.install_dir.join(REQUIREMENTS_FILE)).unwrap();
        assert!(missing_extras(&reqs).is_empty(), "{reqs}");
    }

    #[test]
    fn 安装包里也是旧的就报错_不动已装的() {
        let tmp = tempfile::tempdir().unwrap();
        let _env = crate::util::test_env::env_lock();
        let archive = make_bundle(tmp.path(), &reqs("2.13.0"));
        let paths = installed(tmp.path(), &reqs("2.13.0"));
        let err = refresh_offline_deps_if_outdated(&artifacts(tmp.path(), archive), &paths).unwrap_err();
        assert!(format!("{err:#}").contains("也没换新"), "{err:#}");
        assert!(paths.install_dir.join(WHEELHOUSE_DIR).join("pyjwt-2.13.0-py3-none-any.whl").exists());
    }
}
