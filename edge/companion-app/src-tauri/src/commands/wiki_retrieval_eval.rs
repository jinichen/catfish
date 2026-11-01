//! 知识库检索打分 (11/02, 借 DeepTutor 的 `kb eval`)。
//!
//! 员工 / 管理员写一组「问题 + 应该找到的东西」, 同一组题跑几种检索方式, 看谁找得准:
//!
//!   text      关键词 (知识页里的搜索框, 不开语义)
//!   hybrid    关键词 + 语义向量 + 关系图谱 (知识页默认)
//!   semantic  只用语义向量 (本机 BGE-M3)
//!   xiaonian  小鲶聊天时调的 catfish_wiki_search (tool-bridge, 关键词 BM25)
//!
//! 后两条走的是两套不同的实现 —— 聊天里的小鲶和知识页找东西本来就不是一个法子,
//! 哪个更准以前没量过, 这个就是量它的尺子。改检索 (调权重、换模型、加图谱) 前后各跑一遍。
//!
//! 题目格式 (一行一题的 JSON, 也接受一个 JSON 数组):
//!   {"query": "季度预算谁批?", "gold": ["财务部王芳"]}
//!   {"query": "立项会几点开", "gold": ["wiki/concepts/立项会.md"]}
//! gold 里任一条命中就算找到: 写路径 (含 `/` 或以 .md 结尾) 比路径, 跟标题一样算, 否则看条目正文里有没有这段话。
//!
//! 打分: 命中率@K = 前 K 条里有正确答案的题占比; MRR = 第一条正确答案排名的倒数取平均 (排第一 = 1, 第二 = 0.5 …)。

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::Duration;

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct EvalItem {
    pub query: String,
    #[serde(default)]
    pub gold: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineScore {
    pub engine: String,
    pub label: String,
    /// 跑通没有 (语义模型没装 / tool-bridge 没起 → false, 不算分)
    pub available: bool,
    pub note: Option<String>,
    pub found: usize,
    pub total: usize,
    pub recall: f64,
    pub mrr: f64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueryRow {
    pub query: String,
    /// 每种方式: 正确答案排第几 (1 起; 没找到为 None)、前几条的标题
    pub ranks: HashMap<String, Option<usize>>,
    pub tops: HashMap<String, Vec<String>>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EvalReport {
    pub top_k: usize,
    pub engines: Vec<EngineScore>,
    pub queries: Vec<QueryRow>,
}

/// 一次检索返回的一条: 路径 + 标题
#[derive(Debug, Clone)]
pub struct Hit {
    pub rel_path: String,
    pub title: String,
}

pub const ENGINES: [(&str, &str); 4] = [
    ("text", "关键词"),
    ("hybrid", "关键词 + 语义 + 图谱 (知识页默认)"),
    ("semantic", "只用语义"),
    ("xiaonian", "小鲶聊天时用的"),
];

pub fn parse_items(text: &str) -> Result<Vec<EvalItem>, String> {
    let t = text.trim();
    if t.is_empty() {
        return Err("题目是空的".into());
    }
    let items: Vec<EvalItem> = if t.starts_with('[') {
        serde_json::from_str(t).map_err(|e| format!("JSON 格式不对: {e}"))?
    } else {
        let mut v = vec![];
        for (n, line) in t.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() || line.starts_with("//") || line.starts_with('#') {
                continue;
            }
            v.push(serde_json::from_str(line).map_err(|e| format!("第 {} 行格式不对: {e}", n + 1))?);
        }
        v
    };
    let items: Vec<EvalItem> = items.into_iter().filter(|i| !i.query.trim().is_empty() && !i.gold.is_empty()).collect();
    if items.is_empty() {
        return Err("没有可用的题 (每题要有 query 和至少一条 gold)".into());
    }
    if items.len() > 500 {
        return Err("一次最多 500 题".into());
    }
    Ok(items)
}

fn squash(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect::<String>().to_lowercase()
}

/// 这一条算不算找到了正确答案。`content` 是条目全文 (惰性读, 只在要比正文时读)
pub fn is_gold(hit: &Hit, gold: &[String], content: &mut dyn FnMut(&str) -> String) -> bool {
    for g in gold {
        let g = g.trim();
        if g.is_empty() {
            continue;
        }
        if g.contains('/') || g.ends_with(".md") {
            let want = g.trim_start_matches("~/.catfish/").trim_start_matches('/');
            if hit.rel_path == want || hit.rel_path.ends_with(want) {
                return true;
            }
            continue;
        }
        if squash(&hit.title) == squash(g) {
            return true;
        }
        if squash(&content(&hit.rel_path)).contains(&squash(g)) {
            return true;
        }
    }
    false
}

/// 打分 (纯函数, 好测): 每题每种方式的命中列表 → 报告
pub fn score(
    items: &[EvalItem],
    results: &HashMap<String, Result<Vec<Vec<Hit>>, String>>,
    top_k: usize,
    content: &mut dyn FnMut(&str) -> String,
) -> EvalReport {
    let mut engines = vec![];
    let mut rows: Vec<QueryRow> = items
        .iter()
        .map(|i| QueryRow { query: i.query.clone(), ranks: HashMap::new(), tops: HashMap::new() })
        .collect();
    for (key, label) in ENGINES {
        let Some(res) = results.get(key) else { continue };
        match res {
            Err(e) => engines.push(EngineScore {
                engine: key.into(), label: label.into(), available: false, note: Some(e.clone()),
                found: 0, total: items.len(), recall: 0.0, mrr: 0.0,
            }),
            Ok(per_query) => {
                let (mut found, mut rr) = (0usize, 0f64);
                for (n, item) in items.iter().enumerate() {
                    let hits = per_query.get(n).cloned().unwrap_or_default();
                    let rank = hits.iter().take(top_k).position(|h| is_gold(h, &item.gold, content)).map(|p| p + 1);
                    if let Some(r) = rank {
                        found += 1;
                        rr += 1.0 / r as f64;
                    }
                    rows[n].ranks.insert(key.into(), rank);
                    rows[n].tops.insert(key.into(), hits.iter().take(3).map(|h| h.title.clone()).collect());
                }
                let total = items.len().max(1) as f64;
                engines.push(EngineScore {
                    engine: key.into(), label: label.into(), available: true, note: None,
                    found, total: items.len(),
                    recall: (found as f64 / total * 1000.0).round() / 1000.0,
                    mrr: (rr / total * 1000.0).round() / 1000.0,
                });
            }
        }
    }
    EvalReport { top_k, engines, queries: rows }
}

// ── 跑真检索 ────────────────────────────────────────────

async fn run_text(q: &str) -> Result<Vec<Hit>, String> {
    let hits = crate::commands::wiki_search::wiki_search_text(q.to_string()).await?;
    Ok(hits.into_iter().map(|h| Hit { rel_path: h.rel_path, title: h.title }).collect())
}

async fn run_hybrid(q: &str, k: usize) -> Result<Vec<Hit>, String> {
    let hits = crate::commands::wiki_search::wiki_search_hybrid(q.to_string(), Some(k)).await?;
    Ok(hits.into_iter().map(|h| Hit { rel_path: h.rel_path, title: h.title }).collect())
}

async fn run_semantic(q: &str, k: usize) -> Result<Vec<Hit>, String> {
    let r = crate::commands::wiki_embed::wiki_search_semantic(q.to_string(), Some(k)).await?;
    if !r.model_loaded {
        return Err(format!("语义模型没就绪: {}", r.message));
    }
    Ok(r.hits.into_iter().map(|h| Hit { rel_path: h.rel_path, title: h.title }).collect())
}

/// tool-bridge 回的可能是对象, 也可能包了一层 content 文本 —— 两种都认
fn xiaonian_hits(v: &Value) -> Vec<Hit> {
    let obj = if v.get("matches").is_some() {
        v.clone()
    } else if let Some(t) = v.pointer("/content/0/text").and_then(Value::as_str) {
        serde_json::from_str(t).unwrap_or(Value::Null)
    } else if let Some(t) = v.as_str() {
        serde_json::from_str(t).unwrap_or(Value::Null)
    } else {
        Value::Null
    };
    obj["matches"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|m| Hit {
                    rel_path: m["rel_path"].as_str().unwrap_or("").to_string(),
                    title: m["title"].as_str().or(m["name"].as_str()).unwrap_or("").to_string(),
                })
                .collect()
        })
        .unwrap_or_default()
}

async fn run_xiaonian(q: &str, k: usize) -> Result<Vec<Hit>, String> {
    let v = crate::services::tool_bridge_rpc::call_with_timeout(
        "tools/dispatch",
        json!({"name": "catfish_wiki_search", "args": {"query": q, "top_k": k.min(15)}}),
        Duration::from_secs(20),
    )
    .await?;
    Ok(xiaonian_hits(&v))
}

/// 跑完一种方式的全部题; 第一题就失败 (没装 / 没起) 整种方式记为不可用
async fn run_engine(key: &str, items: &[EvalItem], k: usize) -> Result<Vec<Vec<Hit>>, String> {
    let mut out = vec![];
    for (n, it) in items.iter().enumerate() {
        let r = match key {
            "text" => run_text(&it.query).await,
            "hybrid" => run_hybrid(&it.query, k).await,
            "semantic" => run_semantic(&it.query, k).await,
            _ => run_xiaonian(&it.query, k).await,
        };
        match r {
            Ok(h) => out.push(h),
            Err(e) if n == 0 => return Err(e),
            Err(_) => out.push(vec![]),
        }
    }
    Ok(out)
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_retrieval_eval(questions: String, top_k: Option<usize>) -> Result<EvalReport, String> {
    let items = parse_items(&questions)?;
    let k = top_k.unwrap_or(5).clamp(1, 20);
    let mut results = HashMap::new();
    for (key, _) in ENGINES {
        results.insert(key.to_string(), run_engine(key, &items, k).await);
    }
    let home = crate::commands::wiki_read::catfish_home()?;
    let mut cache: HashMap<String, String> = HashMap::new();
    let mut content = |rel: &str| -> String {
        cache
            .entry(rel.to_string())
            .or_insert_with(|| std::fs::read_to_string(home.join(rel)).unwrap_or_default())
            .clone()
    };
    Ok(score(&items, &results, k, &mut content))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hit(p: &str, t: &str) -> Hit {
        Hit { rel_path: p.into(), title: t.into() }
    }

    #[test]
    fn 读题_一行一题或数组_空的和缺gold的跳过() {
        let t = "{\"query\":\"预算谁批\",\"gold\":[\"王芳\"]}\n\n# 注释\n{\"query\":\"没答案\",\"gold\":[]}";
        assert_eq!(parse_items(t).unwrap().len(), 1);
        assert_eq!(parse_items("[{\"query\":\"a\",\"gold\":[\"b\"]}]").unwrap().len(), 1);
        assert!(parse_items("{坏的").unwrap_err().contains("第 1 行"));
        assert!(parse_items("").is_err());
    }

    #[test]
    fn 正确答案认路径_标题_正文片段() {
        let h = hit("wiki/concepts/立项会.md", "立项会");
        let mut body = |_: &str| "每周三下午 三楼 会议室".to_string();
        assert!(is_gold(&h, &["wiki/concepts/立项会.md".into()], &mut body));
        assert!(is_gold(&h, &["立项会".into()], &mut body));
        assert!(is_gold(&h, &["每周三下午三楼".into()], &mut body)); // 空白不计
        assert!(!is_gold(&h, &["每周五".into(), "wiki/x.md".into()], &mut body));
    }

    #[test]
    fn 打分_命中率和平均排名分_不可用的方式单列() {
        let items = vec![
            EvalItem { query: "q1".into(), gold: vec!["A".into()] },
            EvalItem { query: "q2".into(), gold: vec!["B".into()] },
        ];
        let mut results = HashMap::new();
        // q1 排第 1, q2 排第 2
        results.insert("text".to_string(), Ok(vec![vec![hit("a.md", "A")], vec![hit("x.md", "X"), hit("b.md", "B")]]));
        // q1 找到排第 1, q2 没找到
        results.insert("hybrid".to_string(), Ok(vec![vec![hit("a.md", "A")], vec![hit("x.md", "X")]]));
        results.insert("semantic".to_string(), Err("语义模型没就绪".to_string()));
        let mut body = |_: &str| String::new();
        let r = score(&items, &results, 5, &mut body);
        let text = r.engines.iter().find(|e| e.engine == "text").unwrap();
        assert_eq!((text.found, text.recall, text.mrr), (2, 1.0, 0.75));
        let hy = r.engines.iter().find(|e| e.engine == "hybrid").unwrap();
        assert_eq!((hy.found, hy.recall, hy.mrr), (1, 0.5, 0.5));
        let se = r.engines.iter().find(|e| e.engine == "semantic").unwrap();
        assert!(!se.available);
        assert_eq!(r.queries[1].ranks["text"], Some(2));
        assert_eq!(r.queries[1].ranks["hybrid"], None);
        // 只看前 K 条: K=1 时 q2 在 text 里算没找到
        let r1 = score(&items, &results, 1, &mut body);
        assert_eq!(r1.engines.iter().find(|e| e.engine == "text").unwrap().found, 1);
    }

    #[test]
    fn 小鲶那路的返回两种形状都认() {
        let direct = json!({"matches": [{"rel_path": "wiki/a.md", "title": "A"}]});
        let wrapped = json!({"content": [{"type": "text", "text": "{\"matches\":[{\"rel_path\":\"wiki/b.md\",\"name\":\"B\"}]}"}]});
        assert_eq!(xiaonian_hits(&direct)[0].rel_path, "wiki/a.md");
        assert_eq!(xiaonian_hits(&wrapped)[0].title, "B");
    }
}
