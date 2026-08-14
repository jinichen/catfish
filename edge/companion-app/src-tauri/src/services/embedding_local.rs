//! 本机 ONNX 向量 provider (BGE-M3).
//!
//! 8/14 从 services/embedding.rs 拆出来 —— 那个文件加了"观测身份"和"远程失败
//! 退回本地"两块之后到了 890 行, 越过仓里的 800 行红线。
//!
//! 按 provider 拆是自然的切法: embedding.rs 留"选谁 + 公共 API",
//! 两个 provider 各自一个文件。
//!
//! ⚠ 整个模块只在 aarch64 编 (services/mod.rs 上有 cfg)。
//! ort 官方不发 x86_64-apple-darwin prebuilt, Intel Mac dmg / Windows msi
//! 连这个文件都不参与编译 —— 那边**没有本地向量**, 远程挂了就是没有向量。

use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use ort::session::{Session, builder::GraphOptimizationLevel};
use ort::value::Value;
use tokenizers::Tokenizer;

use crate::services::embed_cache_meta::{EmbedIdentity, record_identity};
use crate::services::embedding_config::{LocalConfig, expand_home};

// ─── Local ONNX provider — 老 P3.5.4 路径重构进来 ────────────
// 7/16 BL-INTEL-DMG: LocalProvider 用 ort::Session + tokenizers::Tokenizer, 只 aarch64
// 编. Intel Mac dmg / Windows msi 上整个 struct + impl 不存在, 前面 Provider enum 里
// Local variant 也 cfg-guard, 保证 x86_64 build 通.

#[cfg(target_arch = "aarch64")]
pub(crate) struct LocalProvider {
    config: LocalConfig,
    session: OnceLock<Option<Mutex<Session>>>,
    tokenizer: OnceLock<Option<Tokenizer>>,
}

#[cfg(target_arch = "aarch64")]
impl LocalProvider {
    pub(crate) fn new(config: LocalConfig) -> Self {
        Self {
            config,
            session: OnceLock::new(),
            tokenizer: OnceLock::new(),
        }
    }

    fn init_session(&self) -> Option<&Mutex<Session>> {
        self.session
            .get_or_init(|| {
                let path = PathBuf::from(expand_home(&self.config.model_path));
                if !path.exists() {
                    log::warn!(
                        "[embedding/local] ONNX 文件不存在 {:?}, embed_text 将返 None",
                        path
                    );
                    return None;
                }
                log::info!(
                    "[embedding/local] 加载 ONNX {:?} (threads={})",
                    path,
                    self.config.intra_threads
                );
                let session = Session::builder()
                    .ok()?
                    .with_optimization_level(GraphOptimizationLevel::Level3)
                    .ok()?
                    .with_intra_threads(self.config.intra_threads)
                    .ok()?
                    .commit_from_file(&path)
                    .ok()?;
                Some(Mutex::new(session))
            })
            .as_ref()
    }

    fn init_tokenizer(&self) -> Option<&Tokenizer> {
        self.tokenizer
            .get_or_init(|| {
                let path = PathBuf::from(expand_home(&self.config.tokenizer_path));
                if !path.exists() {
                    log::warn!(
                        "[embedding/local] tokenizer.json 缺 {:?}, embed_text 将返 None",
                        path
                    );
                    return None;
                }
                Tokenizer::from_file(&path).ok()
            })
            .as_ref()
    }

    pub(crate) fn is_ready(&self) -> bool {
        self.init_session().is_some() && self.init_tokenizer().is_some()
    }

    /// 卡在哪一步. **先报文件不在**, 因为那是绝大多数情况且一句话能修好.
    pub(crate) fn not_ready_reason(&self) -> String {
        let onnx = PathBuf::from(expand_home(&self.config.model_path));
        let tok = PathBuf::from(expand_home(&self.config.tokenizer_path));
        let missing: Vec<String> = [(&onnx, "ONNX 模型"), (&tok, "tokenizer.json")]
            .iter()
            .filter(|(p, _)| !p.exists())
            .map(|(p, what)| format!("{what} 不在: {}", p.display()))
            .collect();
        if !missing.is_empty() {
            return format!(
                "本机向量模型没装齐 —— {}\n\n下载:\n  \
                 mkdir -p ~/.catfish/models\n  \
                 curl -L -o ~/.catfish/models/bge-m3.onnx \
                 https://huggingface.co/Xenova/bge-m3/resolve/main/onnx/model_quantized.onnx\n  \
                 curl -L -o ~/.catfish/models/tokenizer.json \
                 https://huggingface.co/Xenova/bge-m3/resolve/main/tokenizer.json",
                missing.join("; ")
            );
        }
        format!(
            "本机向量模型文件都在 ({}), 但加载失败 —— 多半是文件损坏或架构不符, \
             详情看 Companion 日志里 [embedding/local] 那几行。",
            onnx.display()
        )
    }

    pub(crate) async fn embed_text(&self, text: &str) -> Option<Vec<f32>> {
        // 注: ONNX 推理 sync 长跑 (10-50ms). 主线程跑可接受 (P3.5.4 实测 OK).
        //     真 spawn_blocking 跨线程要 Arc<Session>, 复杂度 / 收益不平衡, 保持当前.
        let session_mutex = self.init_session()?;
        let tokenizer = self.init_tokenizer()?;
        // dim 不再从 config 拿 —— 见下面 ONNX 输出 shape 那段
        let max_tokens = self.config.max_tokens;

        let encoding = tokenizer
            .encode(text, true)
            .map_err(|e| log::warn!("[embedding/local] tokenize 失败: {e}"))
            .ok()?;
        let ids = encoding.get_ids();
        let mask = encoding.get_attention_mask();

        let n = ids.len().min(max_tokens);
        let input_ids: Vec<i64> = ids[..n].iter().map(|&x| x as i64).collect();
        let attention_mask: Vec<i64> = mask[..n].iter().map(|&x| x as i64).collect();

        let shape = vec![1_i64, n as i64];
        let inputs = ort::inputs![
            "input_ids" => Value::from_array((shape.clone(), input_ids)).ok()?,
            "attention_mask" => Value::from_array((shape, attention_mask)).ok()?,
        ];

        let mut session = session_mutex.lock().ok()?;
        let outputs = session.run(inputs).ok()?;
        // 8/14: shape 原来被 `let (_, ...)` 丢掉了, 维度改从 yaml 读 —— 而
        // hidden size 就明明白白在 shape 的最后一维上。
        // ONNX last_hidden_state 是 [batch, seq, hidden], 数出来比配置准, 也不用配。
        let (shape, output_data) = outputs[0].try_extract_tensor::<f32>().ok()?;
        let dim = match shape.last().copied().filter(|d| *d > 0) {
            Some(d) => d as usize,
            None => {
                log::warn!(
                    "[embedding/local] ONNX 输出 shape={shape:?} 取不出 hidden 维, 放弃这条"
                );
                return None;
            }
        };
        if output_data.len() < dim || output_data.len() % dim != 0 {
            log::warn!(
                "[embedding/local] ONNX 输出长度 {} 跟 shape {shape:?} 对不上 (hidden={dim})",
                output_data.len()
            );
            return None;
        }
        record_identity(EmbedIdentity {
            dim,
            // 本机这条路的"模型身份"就是那个文件 —— 换了文件就该重建缓存
            model: Some(format!("local:{}", self.config.model_path)),
        });
        let seq_len = output_data.len() / dim;
        let mut pooled = vec![0.0f32; dim];
        for s in 0..seq_len {
            for i in 0..dim {
                pooled[i] += output_data[s * dim + i];
            }
        }
        for v in pooled.iter_mut() {
            *v /= seq_len as f32;
        }
        let norm = (pooled.iter().map(|v| v * v).sum::<f32>()).sqrt().max(1e-8);
        for v in pooled.iter_mut() {
            *v /= norm;
        }
        Some(pooled)
    }
}

