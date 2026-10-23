//! 10/23: 知识体系搜索「语义分数门槛」的校准 —— 用本机 BGE-M3 (跟 Companion 真实搜索同一条路)
//! 给一批问题打分, 看相关 / 不相关问题的最高分各落在哪。
//!
//! 默认不跑 (要本机模型 + ~/.catfish/wiki_embeddings.db)。手动:
//!   CALIB_QUERIES='未来产业发展规划|高新技术企业认定条件' \
//!     cargo test --release --lib wiki_search_calibrate -- --ignored --nocapture

#[cfg(all(test, local_embedding))]
mod tests {
    use crate::services::embedding::{cosine, vector_from_blob};
    use crate::services::embedding_config::LocalConfig;
    use crate::services::embedding_local::LocalProvider;
    use std::collections::HashMap;

    /// 真实混合搜索 (跟知识体系搜索框同一个入口), 打印结果标题
    #[tokio::test]
    #[ignore]
    async fn wiki_search_end_to_end() {
        let queries = std::env::var("CALIB_QUERIES").unwrap_or_default();
        for q in queries.split('|').filter(|q| !q.trim().is_empty()) {
            let hits = crate::commands::wiki_search::wiki_search_hybrid(q.to_string(), Some(20)).await.unwrap();
            let titles: Vec<String> = hits.iter().take(8).map(|h| format!("{}[{}]", h.title.chars().take(12).collect::<String>(), h.matched_in.join(","))).collect();
            println!("E2E {q} → {} 条: {}", hits.len(), titles.join(" | "));
        }
    }

    #[tokio::test]
    #[ignore]
    async fn wiki_search_calibrate() {
        let provider = LocalProvider::new(LocalConfig::default());
        let home = std::env::var("HOME").unwrap();
        let conn = rusqlite::Connection::open(format!("{home}/.catfish/wiki_embeddings.db")).unwrap();
        let mut stmt = conn.prepare("SELECT title, vector FROM wiki_embed_chunk").unwrap();
        let rows: Vec<(String, Vec<f32>)> = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, Vec<u8>>(1)?)))
            .unwrap()
            .filter_map(|r| r.ok())
            .filter_map(|(t, b)| vector_from_blob(&b, 1024).map(|v| (t, v)))
            .collect();
        let queries = std::env::var("CALIB_QUERIES").unwrap_or_default();
        for q in queries.split('|').filter(|q| !q.trim().is_empty()) {
            let qv = provider.embed_text(q).await.expect("本机模型没就绪");
            let mut best: HashMap<&str, f32> = HashMap::new();
            for (t, v) in &rows {
                let s = cosine(&qv, v);
                let e = best.entry(t.as_str()).or_insert(-1.0);
                if s > *e {
                    *e = s;
                }
            }
            let mut top: Vec<_> = best.into_iter().collect();
            top.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
            let n = top.len() as f32;
            let mean = top.iter().map(|x| x.1).sum::<f32>() / n;
            let sd = (top.iter().map(|x| (x.1 - mean).powi(2)).sum::<f32>() / n).sqrt();
            let z: Vec<String> = top.iter().take(6).map(|x| format!("{:.1}", (x.1 - mean) / sd)).collect();
            println!("CALIBZ {q} | mean {mean:.3} sd {sd:.3} | z {}", z.join(" "));
            let line: Vec<String> = top.iter().take(6).map(|(t, s)| format!("{} {:.3}", t.chars().take(12).collect::<String>(), s)).collect();
            println!("CALIB {q} | {}", line.join(" | "));
        }
    }
}
