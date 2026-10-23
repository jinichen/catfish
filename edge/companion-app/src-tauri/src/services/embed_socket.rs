//! 本机向量通道 —— 鲶鱼的记忆插件问, Companion 答 (10/23)。
//!
//! # 为什么
//!
//! Companion 的知识库搜索用本机 BGE-M3 (`~/.catfish/models/bge-m3.onnx`, 见 embedding_local.rs);
//! 鲶鱼的长期记忆检索跑在 hermes 的 Python 里, 用不到 Companion 加载的模型, 10/23 写的时候
//! 直接调了网关的 bge-m3 —— 网关那个部署在公司内网。鸿波 10/23 晚上在家: 网关 502,
//! 长期记忆的向量那一路从 19:53 起悄悄退回只用词面。同一台机器上还是两套向量, 互相比不了。
//!
//! 治本: 向量只由 Companion 这一处给。插件有本机模型时只走这条通道, 用的是 Companion
//! 当前的 provider, 跟知识库搜索同一套:
//!   · Apple 芯片 Mac: 本机 ONNX (打包带 ONNX Runtime)
//!   · Windows: 本机 ONNX (装了「向量模型」组件包, onnxruntime.dll 随包下发) / 没装就是网关
//!   · Intel Mac: 只有网关 (ONNX Runtime 不发 x86_64-apple-darwin 预编译包)
//!
//! # 为什么是本机 TCP 不是 Unix socket
//!
//! Windows 也要用 (那边有本机模型), std 在 Windows 上没有 UnixListener。只听 127.0.0.1,
//! 端口随机, 写进 `~/.catfish/companion-embed.port` —— 跟 tool-bridge 在 Windows 上的
//! endpoint 文件同一个做法。
//!
//! # 协议
//!
//! 一条连接一次请求, 一行 JSON 进一行 JSON 出:
//!   → {"texts": ["…", "…"]}                      (≤ MAX_TEXTS 条)
//!   ← {"model": "local:…", "dim": 1024, "vectors": [[…], […]]}
//!   ← {"error": "…"}                              (任何一条算不出来就整体报错, 不给半截)
//!
//! 不鉴权: 只听回环地址, 放出去的只是"借 Companion 的模型把一段文字算成向量" ——
//! 没有任何数据从 Companion 流出, 向量本身也不是机密。
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::Duration;

use serde_json::{json, Value};

/// 一次最多算几条。插件建索引时一批 64 条。
const MAX_TEXTS: usize = 128;
/// 请求上限 (64 条记忆 × 几百字 远小于这个)。
const MAX_REQUEST_BYTES: u64 = 4 * 1024 * 1024;
const READ_TIMEOUT: Duration = Duration::from_secs(10);

/// 解析请求、逐条算、拼响应。不碰 socket, 测试直接调。
pub(crate) fn respond(line: &str, embed: impl Fn(&str) -> Option<Vec<f32>>, model: impl Fn() -> Option<String>) -> Value {
    let req: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => return json!({"error": format!("请求不是 JSON: {e}")}),
    };
    let Some(texts) = req.get("texts").and_then(|t| t.as_array()) else {
        return json!({"error": "缺 texts"});
    };
    if texts.is_empty() || texts.len() > MAX_TEXTS {
        return json!({"error": format!("texts 要 1-{MAX_TEXTS} 条, 收到 {}", texts.len())});
    }
    let mut vectors = Vec::with_capacity(texts.len());
    for t in texts {
        let Some(s) = t.as_str() else {
            return json!({"error": "texts 里有不是字符串的"});
        };
        match embed(s) {
            Some(v) => vectors.push(v),
            None => {
                return json!({"error": crate::services::embedding::provider_not_ready_reason()
                    .unwrap_or_else(|| "向量没算出来".to_string())})
            }
        }
    }
    let dim = vectors.first().map(|v| v.len()).unwrap_or(0);
    json!({"model": model().unwrap_or_default(), "dim": dim, "vectors": vectors})
}

pub fn spawn() {
    std::thread::spawn(|| {
        if let Err(e) = serve() {
            log::warn!("[embed-socket] 起不来 (鲶鱼的记忆检索会退回只用词面): {e}");
        }
    });
}

fn serve() -> Result<(), String> {
    let port_file = crate::services::catfish_paths::companion_embed_port_file()
        .ok_or_else(|| "找不到 home 目录".to_string())?;
    if let Some(dir) = port_file.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("建目录 {} 失败: {e}", dir.display()))?;
    }
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("bind 127.0.0.1 失败: {e}"))?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    std::fs::write(&port_file, port.to_string()).map_err(|e| format!("写 {} 失败: {e}", port_file.display()))?;
    log::info!("[embed-socket] listening on 127.0.0.1:{port} ({})", port_file.display());
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                std::thread::spawn(move || handle_one(s));
            }
            Err(e) => log::warn!("[embed-socket] accept 失败: {e}"),
        }
    }
    Ok(())
}

fn handle_one(mut stream: TcpStream) {
    let _ = stream.set_read_timeout(Some(READ_TIMEOUT));
    let reader = match stream.try_clone() {
        Ok(s) => BufReader::new(s),
        Err(_) => return,
    };
    let mut line = String::new();
    if reader.take(MAX_REQUEST_BYTES).read_line(&mut line).is_err() {
        return;
    }
    // embed_text 是 async (远程走 HTTP); 这里是普通线程, 不在 tokio runtime 里, 直接 block_on
    let resp = respond(
        line.trim(),
        |t| tauri::async_runtime::block_on(crate::services::embedding::embed_text(t)),
        || crate::services::embed_cache_meta::observed_identity().and_then(|i| i.model),
    );
    let mut out = resp.to_string();
    out.push('\n');
    let _ = stream.write_all(out.as_bytes());
    let _ = stream.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embeds_each_text_and_reports_model() {
        let r = respond(r#"{"texts":["甲","乙乙"]}"#, |t| Some(vec![t.chars().count() as f32, 0.0]), || Some("local:m".into()));
        assert_eq!(r["model"], "local:m");
        assert_eq!(r["dim"], 2);
        assert_eq!(r["vectors"][1][0], 2.0);
    }

    #[test]
    fn rejects_bad_requests_and_partial_failures() {
        assert!(respond("不是json", |_| None, || None)["error"].is_string());
        assert!(respond(r#"{"texts":[]}"#, |_| None, || None)["error"].is_string());
        let fails_second = respond(r#"{"texts":["a","b"]}"#, |t| (t == "a").then(|| vec![1.0]), || None);
        assert!(fails_second["error"].is_string(), "有一条算不出就整体报错, 不给半截");
    }
}
