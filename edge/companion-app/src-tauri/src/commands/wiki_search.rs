//! Wiki 词法、分块和混合检索。
//!
//! 第一轮检索升级的边界：
//! - 词法检索使用真正的 BM25，而不是关键词出现次数加分；
//! - 中英文统一做轻量、确定性的 token 化，避免新增 native 依赖；
//! - 语义结果和词法结果用 RRF 按排名融合，两个分数空间不直接相加；
//! - BGE-M3 的向量缓存仍由 `wiki_embed` 管理。

use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::fs;

use super::wiki_read::{build_file_info, catfish_home, collect_all_wiki_md, WikiFileInfo};

const BM25_K1: f64 = 1.2;
const BM25_B: f64 = 0.75;
const RRF_K: f64 = 60.0;
const CHUNK_SIZE: usize = 480;
const CHUNK_OVERLAP: usize = 64;

#[derive(Debug, Clone, Serialize)]
pub struct WikiSearchHit {
    pub rel_path: String,
    pub title: String,
    pub kind: String,
    pub score: f64,
    pub snippet: String,
    pub matched_in: Vec<String>,
}

/// 文档分块时保留标题，使标题语义进入向量，也能在结果中定位上下文。
#[derive(Debug, Clone)]
pub(crate) struct WikiChunk {
    pub(crate) heading: String,
    pub(crate) text: String,
    pub(crate) embedding_text: String,
    pub(crate) snippet: String,
}

/// 将 Markdown 按标题分区、再按字符数切块。
///
/// 使用字符而不是字节作为边界，保证中文不会被截断；重叠只用于相邻块，
/// 让跨段落的语义不会因为硬边界完全丢失。
pub(crate) fn split_markdown_chunks(content: &str, title: &str) -> Vec<WikiChunk> {
    let body = markdown_body(content);
    let mut sections: Vec<(String, String)> = Vec::new();
    let mut heading = String::new();
    let mut section = String::new();

    for line in body.lines() {
        if line.trim_start().starts_with('#') {
            if !section.trim().is_empty() {
                sections.push((heading.clone(), std::mem::take(&mut section)));
            }
            heading = line.trim().trim_start_matches('#').trim().to_string();
            continue;
        }
        section.push_str(line);
        section.push('\n');
    }
    if !section.trim().is_empty() {
        sections.push((heading, section));
    }
    if sections.is_empty() && !body.trim().is_empty() {
        sections.push((String::new(), body.to_string()));
    }

    let mut chunks = Vec::new();
    for (section_heading, text) in sections {
        for part in bounded_chunks(&text, CHUNK_SIZE, CHUNK_OVERLAP) {
            let embedding_text = if section_heading.is_empty() {
                format!("{title}\n{part}")
            } else {
                format!("{title}\n{section_heading}\n{part}")
            };
            chunks.push(WikiChunk {
                heading: section_heading.clone(),
                snippet: part.chars().take(160).collect::<String>().replace('\n', " "),
                text: part,
                embedding_text,
            });
        }
    }
    chunks
}

fn markdown_body(content: &str) -> &str {
    let trimmed = content.trim_start();
    if !trimmed.starts_with("---") {
        return content;
    }
    trimmed
        .find("\n---")
        .map(|end| &trimmed[end + 4..])
        .unwrap_or(content)
}

fn bounded_chunks(text: &str, max_chars: usize, overlap: usize) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0usize;
    while start < chars.len() {
        let hard_end = (start + max_chars).min(chars.len());
        let mut end = hard_end;
        if hard_end < chars.len() {
            let soft_start = start + max_chars / 2;
            if let Some(pos) = chars[soft_start..hard_end]
                .iter()
                .rposition(|c| c.is_whitespace())
            {
                end = soft_start + pos + 1;
            }
        }
        let part: String = chars[start..end].iter().collect();
        if !part.trim().is_empty() {
            out.push(part);
        }
        if end == chars.len() {
            break;
        }
        start = end.saturating_sub(overlap.min(end));
        if start == end {
            start += 1;
        }
    }
    out
}

/// 中文按 unigram/bigram，英文、数字和下划线按连续词处理。
pub(crate) fn tokenize(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.to_lowercase().chars().collect();
    let mut tokens = Vec::new();
    let mut ascii = String::new();
    let flush_ascii = |ascii: &mut String, tokens: &mut Vec<String>| {
        if !ascii.is_empty() {
            tokens.push(std::mem::take(ascii));
        }
    };
    let mut cjk_run: Vec<char> = Vec::new();
    let flush_cjk = |run: &mut Vec<char>, tokens: &mut Vec<String>| {
        if run.is_empty() {
            return;
        }
        for c in run.iter() {
            tokens.push(c.to_string());
        }
        for pair in run.windows(2) {
            tokens.push(pair.iter().collect());
        }
        for triple in run.windows(3) {
            tokens.push(triple.iter().collect());
        }
        run.clear();
    };

    for ch in chars {
        if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
            flush_cjk(&mut cjk_run, &mut tokens);
            ascii.push(ch);
        } else if is_cjk(ch) {
            flush_ascii(&mut ascii, &mut tokens);
            cjk_run.push(ch);
        } else {
            flush_ascii(&mut ascii, &mut tokens);
            flush_cjk(&mut cjk_run, &mut tokens);
        }
    }
    flush_ascii(&mut ascii, &mut tokens);
    flush_cjk(&mut cjk_run, &mut tokens);
    tokens
}

fn is_cjk(ch: char) -> bool {
    matches!(ch, '\u{3400}'..='\u{4dbf}' | '\u{4e00}'..='\u{9fff}' | '\u{f900}'..='\u{faff}')
}

#[derive(Debug)]
struct LexicalChunk {
    info: WikiFileInfo,
    chunk: WikiChunk,
    tokens: Vec<String>,
    title_tokens: Vec<String>,
    heading_tokens: Vec<String>,
    tag_tokens: Vec<String>,
}

fn load_lexical_chunks(home: &std::path::Path) -> Vec<LexicalChunk> {
    let mut out = Vec::new();
    for path in collect_all_wiki_md(home) {
        let Some(info) = build_file_info(home, &path) else { continue };
        let Ok(content) = fs::read_to_string(&path) else { continue };
        for chunk in split_markdown_chunks(&content, &info.title) {
            out.push(LexicalChunk {
                tokens: tokenize(&chunk.text),
                title_tokens: tokenize(&info.title),
                heading_tokens: tokenize(&chunk.heading),
                tag_tokens: tokenize(&info.tags.join(" ")),
                info: info.clone(),
                chunk,
            });
        }
    }
    out
}

fn bm25(tf: usize, doc_len: usize, avg_len: f64, df: usize, total_docs: usize) -> f64 {
    if tf == 0 || df == 0 || total_docs == 0 {
        return 0.0;
    }
    let idf = (((total_docs as f64 - df as f64 + 0.5) / (df as f64 + 0.5)) + 1.0).ln();
    let norm = 1.0 - BM25_B + BM25_B * (doc_len as f64 / avg_len.max(1.0));
    idf * (tf as f64 * (BM25_K1 + 1.0)) / (tf as f64 + BM25_K1 * norm)
}

fn field_score(tokens: &[String], query: &[String], df: &HashMap<String, usize>, n: usize, avg: f64) -> f64 {
    query
        .iter()
        .map(|term| {
            let tf = tokens.iter().filter(|t| *t == term).count();
            bm25(tf, tokens.len(), avg, *df.get(term).unwrap_or(&0), n)
        })
        .sum()
}

pub(crate) fn lexical_candidates(query: &str, limit: usize) -> Vec<WikiSearchHit> {
    let q = query.trim().to_lowercase();
    let query_tokens = tokenize(&q);
    if query_tokens.is_empty() {
        return Vec::new();
    }
    let home = match catfish_home() {
        Ok(h) => h,
        Err(_) => return Vec::new(),
    };
    let docs = load_lexical_chunks(&home);
    if docs.is_empty() {
        return Vec::new();
    }
    let n = docs.len();
    let avg_len = docs.iter().map(|d| d.tokens.len()).sum::<usize>() as f64 / n as f64;
    let mut df = HashMap::new();
    for doc in &docs {
        let unique: HashSet<&String> = doc.tokens.iter().collect();
        for term in unique {
            *df.entry(term.clone()).or_insert(0usize) += 1;
        }
    }

    let mut ranked: Vec<(f64, &LexicalChunk)> = docs
        .iter()
        .filter_map(|doc| {
            let body = field_score(&doc.tokens, &query_tokens, &df, n, avg_len);
            let title = field_score(&doc.title_tokens, &query_tokens, &df, n, avg_len);
            let heading = field_score(&doc.heading_tokens, &query_tokens, &df, n, avg_len);
            let tags = field_score(&doc.tag_tokens, &query_tokens, &df, n, avg_len);
            let phrase = if doc.info.title.to_lowercase().contains(&q) {
                3.0
            } else if doc.chunk.text.to_lowercase().contains(&q) {
                1.0
            } else {
                0.0
            };
            let score = body + title * 2.5 + heading * 1.5 + tags * 1.2 + phrase;
            (score > 0.0).then_some((score, doc))
        })
        .collect();
    ranked.sort_by(|(a_score, a), (b_score, b)| {
        b_score
            .partial_cmp(a_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.info.rel_path.cmp(&b.info.rel_path))
    });

    let mut hits = Vec::new();
    let mut seen = HashSet::new();
    for (score, doc) in ranked {
        if !seen.insert(doc.info.rel_path.clone()) {
            continue;
        }
        let mut matched_in = vec!["body".to_string()];
        if doc.info.title.to_lowercase().contains(&q) || !doc.title_tokens.is_empty() {
            if doc.title_tokens.iter().any(|t| query_tokens.contains(t)) {
                matched_in.push("title".into());
            }
        }
        if doc.tag_tokens.iter().any(|t| query_tokens.contains(t)) {
            matched_in.push("tags".into());
        }
        hits.push(WikiSearchHit {
            rel_path: doc.info.rel_path.clone(),
            title: doc.info.title.clone(),
            kind: doc.info.kind.clone(),
            score,
            snippet: snippet_around(&doc.chunk.text, &q),
            matched_in,
        });
        if hits.len() >= limit {
            break;
        }
    }
    hits
}

/// RRF 只比较排名，不把 BM25 和 cosine 的原始分数混在一起。
pub(crate) fn rrf_fuse(
    lexical: Vec<WikiSearchHit>,
    semantic: Vec<WikiSearchHit>,
    top_k: usize,
) -> Vec<WikiSearchHit> {
    let mut merged: HashMap<String, WikiSearchHit> = HashMap::new();
    for (rank, hit) in lexical.into_iter().enumerate() {
        let entry = merged.entry(hit.rel_path.clone()).or_insert_with(|| {
            let mut fused = hit.clone();
            fused.score = 0.0;
            fused
        });
        entry.score += 1.0 / (RRF_K + rank as f64 + 1.0);
        merge_sources(&mut entry.matched_in, &hit.matched_in);
    }
    for (rank, hit) in semantic.into_iter().enumerate() {
        let entry = merged.entry(hit.rel_path.clone()).or_insert_with(|| {
            let mut fused = hit.clone();
            fused.score = 0.0;
            fused
        });
        if entry.snippet.is_empty() {
            entry.snippet = hit.snippet.clone();
        }
        entry.score += 1.0 / (RRF_K + rank as f64 + 1.0);
        merge_sources(&mut entry.matched_in, &hit.matched_in);
    }
    let mut hits: Vec<_> = merged.into_values().collect();
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.rel_path.cmp(&b.rel_path))
    });
    hits.truncate(top_k);
    hits
}

fn merge_sources(target: &mut Vec<String>, values: &[String]) {
    for value in values {
        if !target.contains(value) {
            target.push(value.clone());
        }
    }
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_search_text(query: String) -> Result<Vec<WikiSearchHit>, String> {
    Ok(lexical_candidates(&query, 50))
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_search_hybrid(query: String, top_k: Option<usize>) -> Result<Vec<WikiSearchHit>, String> {
    let lexical = lexical_candidates(&query, 50);
    let semantic = match crate::commands::wiki_embed::wiki_search_semantic(query.clone(), Some(50)).await {
        Ok(result) => result
            .hits
            .into_iter()
            .map(|hit| WikiSearchHit {
                rel_path: hit.rel_path,
                title: hit.title,
                kind: hit.kind,
                score: hit.score,
                snippet: hit.snippet,
                matched_in: vec!["semantic".into()],
            })
            .collect(),
        Err(error) => {
            log::warn!("[wiki_search] semantic search failed, lexical fallback: {error}");
            Vec::new()
        }
    };
    Ok(rrf_fuse(lexical, semantic, top_k.unwrap_or(20).min(50)))
}

fn snippet_around(text: &str, query: &str) -> String {
    let lower = text.to_lowercase();
    let q = query.to_lowercase();
    let start_chars = lower
        .find(&q)
        .map(|byte| lower[..byte].chars().count().saturating_sub(60))
        .unwrap_or(0);
    let snippet: String = text.chars().skip(start_chars).take(160).collect();
    format!("…{}…", snippet.replace('\n', " "))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chinese_tokenizer_keeps_unigrams_and_bigrams() {
        let tokens = tokenize("知识库 BGE-M3");
        assert!(tokens.contains(&"知识".to_string()));
        assert!(tokens.contains(&"知识库".to_string()));
        assert!(tokens.contains(&"bge-m3".to_string()));
    }

    #[test]
    fn chunks_have_overlap_and_preserve_heading() {
        let text = "---\ntitle: 测试\n---\n## 运行\n".to_string() + &"甲".repeat(700);
        let chunks = split_markdown_chunks(&text, "测试");
        assert!(chunks.len() >= 2);
        assert_eq!(chunks[0].heading, "运行");
        assert!(chunks[0].embedding_text.contains("运行"));
    }

    #[test]
    fn rrf_puts_intersection_first() {
        let hit = |path: &str, source: &str| WikiSearchHit {
            rel_path: path.into(),
            title: path.into(),
            kind: "concept".into(),
            score: 99.0,
            snippet: String::new(),
            matched_in: vec![source.into()],
        };
        let out = rrf_fuse(
            vec![hit("both.md", "body"), hit("lex.md", "body")],
            vec![hit("both.md", "semantic"), hit("sem.md", "semantic")],
            3,
        );
        assert_eq!(out[0].rel_path, "both.md");
        assert_eq!(out[0].matched_in.len(), 2);
        assert!((out[0].score - (2.0 / 61.0)).abs() < f64::EPSILON);
        assert!(out.iter().all(|hit| hit.score < 0.1));
    }

    #[test]
    fn rrf_keeps_disjoint_results_and_truncates() {
        let hit = |path: &str| WikiSearchHit {
            rel_path: path.into(),
            title: path.into(),
            kind: "entity".into(),
            score: 0.0,
            snippet: String::new(),
            matched_in: vec![],
        };
        let out = rrf_fuse(
            vec![hit("a.md"), hit("b.md")],
            vec![hit("c.md"), hit("d.md")],
            3,
        );
        assert_eq!(out.len(), 3);
    }

    #[test]
    fn snippet_handles_unicode_query() {
        assert!(snippet_around("前缀 知识库 内容", "知识库").contains("知识库"));
    }
}
