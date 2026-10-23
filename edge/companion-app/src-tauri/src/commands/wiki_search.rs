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

/// 字母和数字连写的词 (「iso27001」) 再拆出字母段和数字段, 跟写成「ISO 27001」的对得上。
/// 文档这边连写的整词和拆开的段都留; 问题这边只留拆开的段 (见 `query_terms`), 否则
/// 一个「ISO27001」算成三个必须含的词, 写成「ISO 27001」的条目反而不够数。
fn push_ascii(word: String, tokens: &mut Vec<String>) {
    push_ascii_parts(word, tokens, true);
}

fn push_ascii_parts(word: String, tokens: &mut Vec<String>, keep_whole: bool) {
    let has_alpha = word.chars().any(|c| c.is_ascii_alphabetic());
    let has_digit = word.chars().any(|c| c.is_ascii_digit());
    if has_alpha && has_digit && word.chars().all(|c| c.is_ascii_alphanumeric()) {
        let mut part = String::new();
        let mut parts = Vec::new();
        for c in word.chars() {
            if !part.is_empty() && part.chars().last().unwrap().is_ascii_digit() != c.is_ascii_digit() {
                parts.push(std::mem::take(&mut part));
            }
            part.push(c);
        }
        parts.push(part);
        if keep_whole {
            tokens.push(word);
        }
        tokens.extend(parts);
    } else {
        tokens.push(word);
    }
}

/// 中文按 unigram/bigram，英文、数字和下划线按连续词处理。
pub(crate) fn tokenize(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.to_lowercase().chars().collect();
    let mut tokens = Vec::new();
    let mut ascii = String::new();
    let flush_ascii = |ascii: &mut String, tokens: &mut Vec<String>| {
        if !ascii.is_empty() {
            push_ascii(std::mem::take(ascii), tokens);
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

/// 问题这边的检索词 (10/23): 中文只取相邻两字, 不取单字和三字。
///
/// 文档那边照旧切单字 / 两字 / 三字 (`tokenize`)。问题也切单字的话, 「未来产业发展规划」
/// 里的「发 / 业 / 展」几乎篇篇都有 —— 10/23 实测 231 篇里 227 篇「命中」, 结果全是噪音。
/// 问句里的功能词 (「哪些 / 需要 / 什么时候」) 先切掉 —— 条目是陈述句, 这些词在里面少见,
/// 留着就成了「必须含」的词, 「招投标需要哪些资质」一条都搜不到。跟鲶鱼记忆检索的
/// `_QUERY_STOP` 同一个道理。整个问题只剩一个字时才留单字。
const QUERY_STOP: &[&str] = &[
    "什么时候", "为什么", "是不是", "能不能", "有没有", "怎么样", "什么", "怎么", "怎样", "如何", "哪些", "哪个",
    "哪里", "多少", "是谁", "需要", "应该", "可以", "一下", "帮我", "看看", "现在", "请问",
];

pub(crate) fn query_terms(text: &str) -> Vec<String> {
    let mut text = text.to_lowercase();
    for stop in QUERY_STOP {
        text = text.replace(stop, " ");
    }
    let terms = cjk_and_ascii_terms(&text, false);
    if terms.is_empty() {
        return cjk_and_ascii_terms(&text, true);
    }
    terms
}

fn cjk_and_ascii_terms(text: &str, single_chars: bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut ascii = String::new();
    let mut run: Vec<char> = Vec::new();
    let flush_run = |run: &mut Vec<char>, out: &mut Vec<String>| {
        if single_chars && run.len() == 1 {
            out.push(run[0].to_string());
        }
        for pair in run.windows(2) {
            out.push(pair.iter().collect());
        }
        run.clear();
    };
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
            flush_run(&mut run, &mut out);
            ascii.push(ch);
        } else {
            if !ascii.is_empty() {
                push_ascii_parts(std::mem::take(&mut ascii), &mut out, false);
            }
            if is_cjk(ch) {
                run.push(ch);
            } else {
                flush_run(&mut run, &mut out);
            }
        }
    }
    if !ascii.is_empty() {
        push_ascii_parts(ascii, &mut out, false);
    }
    flush_run(&mut run, &mut out);
    let mut seen = HashSet::new();
    out.retain(|t| seen.insert(t.clone()));
    out
}

/// 一篇条目要含问题里多少检索词才算命中: 按词的稀有程度 (IDF) 加权, 至少占 60%。
///
/// 只数知识库里出现过的词 —— 跨词的两字 (「未来产业」里的「来产」) 知识库里没有, 不进分母。
/// 加权是因为「公司年会节目安排」里「公司 / 安排」篇篇都有, 不加权的话含这两个就够数了;
/// 真正说明问的是什么的「年会」很少见, 权重大, 没有它就不够。
const MIN_COVERAGE: f64 = 0.6;

pub(crate) fn covers(matched_weight: f64, total_weight: f64) -> bool {
    total_weight > 0.0 && matched_weight >= total_weight * MIN_COVERAGE - 1e-9
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
    let query_tokens = query_terms(&q);
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

    // 每篇条目 (跨所有分块 + 标题 / 小标题 / 标签) 含哪些检索词
    let mut file_terms: HashMap<&str, HashSet<&String>> = HashMap::new();
    for doc in &docs {
        let set = file_terms.entry(doc.info.rel_path.as_str()).or_default();
        for t in doc.tokens.iter().chain(&doc.title_tokens).chain(&doc.heading_tokens).chain(&doc.tag_tokens) {
            if query_tokens.contains(t) {
                set.insert(t);
            }
        }
    }
    let present: HashSet<&String> = file_terms.values().flatten().copied().collect();
    let files = file_terms.len().max(1) as f64;
    let mut file_df: HashMap<&String, usize> = HashMap::new();
    for set in file_terms.values() {
        for t in set {
            *file_df.entry(*t).or_default() += 1;
        }
    }
    let idf = |t: &String| ((files + 1.0) / (*file_df.get(t).unwrap_or(&0) as f64 + 0.5)).ln();
    let total_weight: f64 = present.iter().map(|t| idf(t)).sum();

    let mut ranked: Vec<(f64, &LexicalChunk)> = docs
        .iter()
        .filter(|doc| {
            // 整句原样出现在标题 / 正文里的照样算; 否则要含够检索词
            doc.info.title.to_lowercase().contains(&q)
                || doc.chunk.text.to_lowercase().contains(&q)
                || file_terms
                    .get(doc.info.rel_path.as_str())
                    .is_some_and(|set| covers(set.iter().map(|t| idf(t)).sum(), total_weight))
        })
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
    Ok(apply_graph_expansion(lexical_candidates(&query, 50), 50, |_| true))
}

#[tauri::command(rename_all = "camelCase")]
pub async fn wiki_search_hybrid(query: String, top_k: Option<usize>) -> Result<Vec<WikiSearchHit>, String> {
    let lexical = lexical_candidates(&query, 50);
    let mut semantic_ok = true;
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
            semantic_ok = false;
            Vec::new()
        }
    };
    let limit = top_k.unwrap_or(20).min(50);
    // 10/23: 只从「按意思也相关」的结果往外扩关联条目。只有几个字面巧合的弱结果时
    // (「未来产业发展规划」撞上「企业发展与风控部」), 不再顺着关系图再拉进一串。
    // 向量用不了时没得比, 照旧从全部结果扩。
    let seed_filter: fn(&WikiSearchHit) -> bool = if semantic_ok {
        |hit| hit.matched_in.iter().any(|m| m == "semantic")
    } else {
        |_| true
    };
    Ok(apply_graph_expansion(rrf_fuse(lexical, semantic, 50), limit, seed_filter))
}

const GRAPH_BONUS_FOR_DIRECT_HITS: f64 = 0.25;

fn apply_graph_expansion(
    mut hits: Vec<WikiSearchHit>,
    top_k: usize,
    is_seed: fn(&WikiSearchHit) -> bool,
) -> Vec<WikiSearchHit> {
    let seeds: Vec<(String, usize)> = hits
        .iter()
        .enumerate()
        .filter(|(_, hit)| is_seed(hit))
        .map(|(rank, hit)| (hit.rel_path.clone(), rank))
        .collect();
    if seeds.is_empty() {
        hits.truncate(top_k);
        return hits;
    }
    let candidates = match super::wiki_graph::expand_one_hop(&seeds) {
        Ok(candidates) => candidates,
        Err(error) => {
            log::warn!("[wiki_search] graph expansion skipped: {error}");
            hits.truncate(top_k);
            return hits;
        }
    };
    for candidate in candidates {
        if let Some(existing) = hits.iter_mut().find(|hit| hit.rel_path == candidate.rel_path) {
            // 10/23: 已经直接命中的, 关联只当加分项, 不能盖过直接命中 —— 原来全额加,
            // 「ISO 27001」搜出来张颖 (维护着好几张 ISO 证书, 每张都给她加一份) 排在 ISO 27001 前面。
            existing.score += candidate.score * GRAPH_BONUS_FOR_DIRECT_HITS;
            merge_sources(&mut existing.matched_in, &["graph-1hop".into()]);
            continue;
        }
        hits.push(WikiSearchHit {
            rel_path: candidate.rel_path,
            title: candidate.title,
            kind: candidate.kind,
            score: candidate.score,
            snippet: format!("由关联知识「{}」扩展", candidate.seed_title),
            matched_in: vec!["graph-1hop".into()],
        });
    }
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.rel_path.cmp(&b.rel_path))
    });
    hits.truncate(top_k);
    hits
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
    fn query_terms_drop_single_chinese_chars() {
        let t = query_terms("未来产业发展规划");
        assert!(t.contains(&"产业".to_string()) && t.contains(&"规划".to_string()));
        assert!(!t.contains(&"发".to_string()), "单字不当检索词: {t:?}");
        assert_eq!(query_terms("颖"), vec!["颖".to_string()], "单字查询留单字");
        assert_eq!(query_terms("CMMI-5 证书"), vec!["cmmi-5".to_string(), "证书".to_string()]);
    }

    #[test]
    fn question_words_are_not_required_terms() {
        assert_eq!(query_terms("招投标需要哪些资质"), vec!["招投", "投标", "资质"]);
        assert_eq!(query_terms("ISO27001 什么时候到期"), vec!["iso", "27001", "到期"]);
        // 切掉「怎么」后剩下的单字「样」不当检索词
        assert_eq!(query_terms("今天天气怎么样"), vec!["今天", "天天", "天气"]);
    }

    #[test]
    fn coverage_is_weighted() {
        assert!(covers(3.0, 5.0));
        assert!(!covers(2.9, 5.0));
        assert!(!covers(0.0, 0.0));
    }

    #[test]
    fn snippet_handles_unicode_query() {
        assert!(snippet_around("前缀 知识库 内容", "知识库").contains("知识库"));
    }
}
