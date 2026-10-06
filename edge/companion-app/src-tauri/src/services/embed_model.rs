//! 向量模型组件包 (BGE-M3 ONNX + tokenizer, ~560MB) 的本机安装 —— 10/6 鸿波
//! 「按照语义检索, 是不是可以仿会议一样, 检查 embedding 模型是否存在, 不存在就
//! 支持从中央端下载?」
//!
//! 跟会议组件包 (services::meeting_asr) 同一条链: 中央 /components/ 发布
//! `embed-model-<x.y.z>-any.tar.gz` → services::components 下载 + sha256 校验到
//! ~/.catfish/runtime/ → 这里解包, 把两个文件放到 embedding.yaml 配置的路径
//! (默认 ~/.catfish/models/bge-m3.onnx + tokenizer.json)。
//!
//! 平台是 `any`: 纯模型文件, 哪个平台都一样。但**本机能不能用它**取决于架构 ——
//! ort 只在 aarch64 编 (见 embedding_local.rs 文件头), Intel Mac / Windows 没有本地
//! 向量 provider, 装了也没人加载。commands/embed_model_cmd.rs 的 status 把这点
//! 报给界面, 界面据此不给 x86 用户看"下载"按钮。
//!
//! 包里:
//!   bge-m3.onnx      Xenova/bge-m3 model_quantized.onnx (INT8)
//!   tokenizer.json   同仓库 tokenizer.json
//!   pack.json        { "version": "1.0.0", "model": "bge-m3", "files": [...] }
//!
//! 装好后不用重启: embedding_local.rs 的 session/tokenizer 不再把"文件不在"
//! 缓存成永久 None (10/6 同批改), 下一次 embed_text 自己加载。
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::services::embedding_config::{LocalConfig, expand_home};

pub const COMPONENT_NAME: &str = "embed-model";
/// 包里两个文件的固定名 (打包脚本 scripts/build-embed-model-pack.sh 同名)。
pub const ONNX_IN_PACK: &str = "bge-m3.onnx";
pub const TOKENIZER_IN_PACK: &str = "tokenizer.json";
const STAMP_FILE: &str = "embed-model.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Installed {
    /// 包版本; 员工自己 curl 下来的文件没有版本 → None (照样算装了)
    pub version: Option<String>,
    pub model_path: PathBuf,
    pub tokenizer_path: PathBuf,
    pub model_bytes: u64,
}

#[derive(Debug, Deserialize)]
struct PackJson {
    version: String,
}

/// 两个文件在 yaml 配的位置上都在 → Some。版本从 models 目录里的 embed-model.json 读。
pub fn installed(local: &LocalConfig) -> Option<Installed> {
    let model_path = PathBuf::from(expand_home(&local.model_path));
    let tokenizer_path = PathBuf::from(expand_home(&local.tokenizer_path));
    let meta = std::fs::metadata(&model_path).ok()?;
    if !tokenizer_path.is_file() {
        return None;
    }
    let version = model_path
        .parent()
        .and_then(|d| std::fs::read_to_string(d.join(STAMP_FILE)).ok())
        .and_then(|s| serde_json::from_str::<PackJson>(&s).ok())
        .map(|p| p.version);
    Some(Installed { version, model_path, tokenizer_path, model_bytes: meta.len() })
}

/// 解包到 yaml 配置的两个路径。`on_step`: unpacking / placing / finishing。
pub fn install(pack: &Path, local: &LocalConfig, mut on_step: impl FnMut(&'static str)) -> Result<Installed, String> {
    let model_path = PathBuf::from(expand_home(&local.model_path));
    let tokenizer_path = PathBuf::from(expand_home(&local.tokenizer_path));
    let dir = model_path
        .parent()
        .ok_or_else(|| format!("模型路径没有父目录: {}", model_path.display()))?
        .to_path_buf();
    std::fs::create_dir_all(&dir).map_err(|e| format!("建 {} 失败: {e}", dir.display()))?;
    let staging = dir.join(format!(".embed-staging-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging).map_err(|e| format!("建临时目录失败: {e}"))?;
    let r = install_inner(pack, &staging, &dir, &model_path, &tokenizer_path, &mut on_step);
    let _ = std::fs::remove_dir_all(&staging);
    r
}

fn install_inner(
    pack: &Path,
    staging: &Path,
    dir: &Path,
    model_path: &Path,
    tokenizer_path: &Path,
    on_step: &mut impl FnMut(&'static str),
) -> Result<Installed, String> {
    on_step("unpacking");
    let out = crate::services::process::background_command("tar")
        .arg("-xzf")
        .arg(pack)
        .arg("-C")
        .arg(staging)
        .output()
        .map_err(|e| format!("解包: 起不来 tar: {e}"))?;
    if !out.status.success() {
        return Err(format!("解包失败: {}", String::from_utf8_lossy(&out.stderr).trim()));
    }
    let pj: PackJson = serde_json::from_str(
        &std::fs::read_to_string(staging.join("pack.json")).map_err(|e| format!("包里没有 pack.json: {e}"))?,
    )
    .map_err(|e| format!("pack.json 格式不对: {e}"))?;
    if pj.version.is_empty() || !pj.version.chars().all(|c| c.is_ascii_digit() || c == '.') {
        return Err(format!("包版本号不合法: {}", pj.version));
    }
    for f in [ONNX_IN_PACK, TOKENIZER_IN_PACK] {
        if !staging.join(f).is_file() {
            return Err(format!("包里缺 {f}"));
        }
    }
    on_step("placing");
    // 先放 tokenizer 再放 onnx: 两步都 rename (同一文件系统, 原子), 中途失败最多
    // 留下一个新 tokenizer + 老 onnx —— 两者都是 BGE-M3, 不会错配。
    place(&staging.join(TOKENIZER_IN_PACK), tokenizer_path)?;
    place(&staging.join(ONNX_IN_PACK), model_path)?;
    on_step("finishing");
    let stamp = dir.join(STAMP_FILE);
    let tmp = dir.join(format!("{STAMP_FILE}.tmp"));
    std::fs::write(&tmp, format!("{{\"version\": \"{}\"}}\n", pj.version)).map_err(|e| format!("写版本戳失败: {e}"))?;
    std::fs::rename(&tmp, &stamp).map_err(|e| format!("写版本戳失败: {e}"))?;
    let model_bytes = std::fs::metadata(model_path).map(|m| m.len()).unwrap_or(0);
    Ok(Installed {
        version: Some(pj.version),
        model_path: model_path.to_path_buf(),
        tokenizer_path: tokenizer_path.to_path_buf(),
        model_bytes,
    })
}

fn place(from: &Path, to: &Path) -> Result<(), String> {
    if let Some(p) = to.parent() {
        std::fs::create_dir_all(p).map_err(|e| format!("建 {} 失败: {e}", p.display()))?;
    }
    match std::fs::rename(from, to) {
        Ok(()) => Ok(()),
        // 配置的路径跟 models 目录不在同一文件系统 → rename 跨设备失败, 退回 copy
        Err(_) => {
            std::fs::copy(from, to).map_err(|e| format!("放 {} 失败: {e}", to.display()))?;
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn make_pack(dir: &Path, version: &str, with_tokenizer: bool) -> PathBuf {
        let src = dir.join("src");
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join(ONNX_IN_PACK), b"onnx-bytes").unwrap();
        if with_tokenizer {
            std::fs::write(src.join(TOKENIZER_IN_PACK), b"{}").unwrap();
        }
        std::fs::write(src.join("pack.json"), format!("{{\"version\": \"{version}\"}}")).unwrap();
        let pack = dir.join(format!("embed-model-{version}-any.tar.gz"));
        let st = std::process::Command::new("tar")
            .arg("-czf")
            .arg(&pack)
            .arg("-C")
            .arg(&src)
            .arg(".")
            .status()
            .unwrap();
        assert!(st.success());
        pack
    }

    fn cfg(models: &Path) -> LocalConfig {
        LocalConfig {
            model_path: models.join("bge-m3.onnx").to_string_lossy().to_string(),
            tokenizer_path: models.join("tokenizer.json").to_string_lossy().to_string(),
            ..LocalConfig::default()
        }
    }

    #[test]
    fn install_places_files_and_stamps_version() {
        let tmp = TempDir::new().unwrap();
        let models = tmp.path().join("models");
        let c = cfg(&models);
        assert!(installed(&c).is_none());
        let pack = make_pack(tmp.path(), "1.0.0", true);
        let mut steps = vec![];
        let inst = install(&pack, &c, |s| steps.push(s)).unwrap();
        assert_eq!(steps, vec!["unpacking", "placing", "finishing"]);
        assert_eq!(inst.version.as_deref(), Some("1.0.0"));
        assert_eq!(std::fs::read(&inst.model_path).unwrap(), b"onnx-bytes");
        assert!(inst.tokenizer_path.is_file());
        assert_eq!(installed(&c).unwrap().version.as_deref(), Some("1.0.0"));
        // 临时目录清掉了
        assert!(!std::fs::read_dir(&models).unwrap().flatten().any(|e| e.file_name().to_string_lossy().starts_with(".embed-staging")));
    }

    #[test]
    fn install_rejects_pack_missing_tokenizer_and_keeps_old_files() {
        let tmp = TempDir::new().unwrap();
        let models = tmp.path().join("models");
        std::fs::create_dir_all(&models).unwrap();
        std::fs::write(models.join("bge-m3.onnx"), b"old").unwrap();
        std::fs::write(models.join("tokenizer.json"), b"old").unwrap();
        let c = cfg(&models);
        let pack = make_pack(tmp.path(), "2.0.0", false);
        let err = install(&pack, &c, |_| {}).unwrap_err();
        assert!(err.contains("tokenizer.json"), "{err}");
        assert_eq!(std::fs::read(models.join("bge-m3.onnx")).unwrap(), b"old");
        // 手动放的文件没有版本戳 → 算装了, 版本 None
        let i = installed(&c).unwrap();
        assert!(i.version.is_none());
    }
}
