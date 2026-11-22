use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use super::wiki_read::{build_file_info, catfish_home, collect_all_wiki_md, RelatedRef, WikiFileInfo};

const GRAPH_DB_NAME: &str = "wiki_graph.db";
const GRAPH_SCORE_WEIGHT: f64 = 0.75;
const GRAPH_RRF_K: f64 = 60.0;
const GRAPH_MAX_NEIGHBORS_PER_SEED: usize = 24;
/// 10/23: 连着这么多条关系的条目 (公司本身 192 条) 不往外扩 —— 「跟公司有关」等于什么都没说,
/// 搜什么都会顺着它拉进几十张证书。下一档的条目只有 38 条, 不受影响。
const GRAPH_HUB_DEGREE: i64 = 60;
const GRAPH_STATUS_SAMPLE_LIMIT: i64 = 10;

#[derive(Debug, Clone)]
pub(crate) struct GraphCandidate {
    pub(crate) rel_path: String,
    pub(crate) title: String,
    pub(crate) kind: String,
    pub(crate) score: f64,
    pub(crate) seed_title: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WikiGraphUnresolved {
    pub(crate) source_path: String,
    pub(crate) source_name: String,
    pub(crate) reason: String,
    pub(crate) candidates: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct WikiGraphStatus {
    pub(crate) node_count: i64,
    pub(crate) edge_count: i64,
    pub(crate) unresolved_count: i64,
    pub(crate) last_sync_at: Option<String>,
    pub(crate) changed_files: usize,
    pub(crate) sync_mode: String,
    pub(crate) unresolved_samples: Vec<WikiGraphUnresolved>,
}

#[derive(Debug, Clone)]
struct GraphFile {
    info: WikiFileInfo,
    /// 这一页从哪来: 自己的 = "self"; 装的部门知识 = "pub:<发布人>" (同目录 meta.json 的 published_by),
    /// 读不到发布人就 "dept:<部门>"。解析关系时先在同一来源里找 (见 resolve_target)。
    origin: String,
    content_hash: String,
    relation_hash: String,
    aliases_json: String,
}

#[derive(Debug, Clone)]
struct StoredNode {
    title: String,
    slug: String,
    aliases_json: String,
    content_hash: String,
    relation_hash: String,
}

fn graph_db_path(home: &Path) -> PathBuf {
    home.join(GRAPH_DB_NAME)
}

fn digest_bytes(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn digest_json<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_vec(value)
        .map(|bytes| digest_bytes(&bytes))
        .unwrap_or_default()
}

fn load_graph_files(home: &Path) -> Result<Vec<GraphFile>, String> {
    let mut files = Vec::new();
    for path in collect_all_wiki_md(home) {
        let Ok(bytes) = fs::read(&path) else { continue };
        let Some(info) = build_file_info(home, &path) else {
            continue;
        };
        if info.ontology_status.as_deref() == Some("pending") {
            continue;
        }
        let aliases_json = serde_json::to_string(&info.aliases).map_err(|error| error.to_string())?;
        files.push(GraphFile {
            origin: origin_of(&path, &info.rel_path),
            relation_hash: relation_hash_of(&info.related),
            content_hash: digest_bytes(&bytes),
            info,
            aliases_json,
        });
    }
    Ok(files)
}

/// 11/23 鸿波「知识库在分享关系有同时分享出去吗」: 分享出去的页带着关系 (只有名字), 同事那边按名字在
/// 「他自己的 + 装的部门知识」里一起找 —— 两边各有一页同名的就算「重名」整条丢掉。所以记下每页的来源,
/// 解析时先在同一来源里找: 装的页先找同一发布人的, 自己的页先找自己的。
fn origin_of(abs_path: &Path, rel_path: &str) -> String {
    let Some(rest) = rel_path.strip_prefix("wiki-shared/dept/") else {
        return "self".to_string();
    };
    let dept = rest.split('/').next().unwrap_or("");
    let meta = abs_path.with_extension("meta.json");
    let publisher = fs::read(&meta)
        .ok()
        .and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok())
        .and_then(|v| v.get("published_by").and_then(|x| x.as_str()).map(|x| x.trim().to_lowercase()))
        .filter(|x| !x.is_empty());
    match publisher {
        Some(p) => format!("pub:{p}"),
        None => format!("dept:{dept}"),
    }
}

fn has_column(conn: &Connection, table: &str, column: &str) -> Result<bool, String> {
    let mut statement = conn
        .prepare(&format!("PRAGMA table_info({table})"))
        .map_err(|error| error.to_string())?;
    let columns = statement
        .query_map([], |row| row.get::<_, String>(1))
        .map_err(|error| error.to_string())?;
    for value in columns {
        if value.map_err(|error| error.to_string())? == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn ensure_schema(conn: &Connection) -> Result<(), String> {
    conn.execute_batch(
        "PRAGMA foreign_keys = ON;
         CREATE TABLE IF NOT EXISTS wiki_graph_node (
             rel_path TEXT PRIMARY KEY,
             title TEXT NOT NULL,
             slug TEXT NOT NULL,
             kind TEXT NOT NULL,
             aliases TEXT NOT NULL DEFAULT '[]',
             mtime REAL NOT NULL DEFAULT 0,
             content_hash TEXT NOT NULL DEFAULT '',
             relation_hash TEXT NOT NULL DEFAULT ''
         );
         CREATE TABLE IF NOT EXISTS wiki_graph_edge (
             source_path TEXT NOT NULL,
             target_path TEXT NOT NULL,
             relation_type TEXT NOT NULL,
             source_name TEXT NOT NULL,
             PRIMARY KEY(source_path, target_path, relation_type, source_name),
             FOREIGN KEY(source_path) REFERENCES wiki_graph_node(rel_path) ON DELETE CASCADE,
             FOREIGN KEY(target_path) REFERENCES wiki_graph_node(rel_path) ON DELETE CASCADE
         );
         CREATE TABLE IF NOT EXISTS wiki_graph_unresolved (
             source_path TEXT NOT NULL,
             source_name TEXT NOT NULL,
             reason TEXT NOT NULL,
             candidates TEXT NOT NULL DEFAULT '[]',
             PRIMARY KEY(source_path, source_name),
             FOREIGN KEY(source_path) REFERENCES wiki_graph_node(rel_path) ON DELETE CASCADE
         );
         CREATE TABLE IF NOT EXISTS wiki_graph_meta (
             key TEXT PRIMARY KEY,
             value TEXT NOT NULL
         );
         CREATE INDEX IF NOT EXISTS idx_wiki_graph_edge_source ON wiki_graph_edge(source_path);
         CREATE INDEX IF NOT EXISTS idx_wiki_graph_edge_target ON wiki_graph_edge(target_path);
         CREATE INDEX IF NOT EXISTS idx_wiki_graph_unresolved_source ON wiki_graph_unresolved(source_path);",
    )
    .map_err(|error| error.to_string())?;

    if !has_column(conn, "wiki_graph_node", "content_hash")? {
        conn.execute(
            "ALTER TABLE wiki_graph_node ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
            [],
        )
        .map_err(|error| error.to_string())?;
    }
    if !has_column(conn, "wiki_graph_node", "relation_hash")? {
        conn.execute(
            "ALTER TABLE wiki_graph_node ADD COLUMN relation_hash TEXT NOT NULL DEFAULT ''",
            [],
        )
        .map_err(|error| error.to_string())?;
    }
    conn.pragma_update(None, "user_version", 2)
        .map_err(|error| error.to_string())?;
    Ok(())
}

fn normalized_name(value: &str) -> String {
    value
        .chars()
        .filter(|character| !character.is_whitespace() && !character.is_ascii_punctuation())
        .flat_map(char::to_lowercase)
        .collect()
}

/// 名字 → 有这个名字的页, 外加每页的来源 (解析时同来源优先)。
struct NameIndex {
    names: HashMap<String, Vec<String>>,
    origin: HashMap<String, String>,
}

fn build_name_index(files: &[GraphFile]) -> NameIndex {
    let mut index: HashMap<String, Vec<String>> = HashMap::new();
    for file in files {
        let names = std::iter::once(file.info.rel_path.as_str())
            .chain(std::iter::once(file.info.slug.as_str()))
            .chain(std::iter::once(file.info.title.as_str()))
            .chain(file.info.aliases.iter().map(String::as_str));
        for name in names {
            let key = normalized_name(name);
            if key.is_empty() {
                continue;
            }
            let values = index.entry(key).or_default();
            if !values.contains(&file.info.rel_path) {
                values.push(file.info.rel_path.clone());
            }
        }
    }
    let origin = files.iter().map(|f| (f.info.rel_path.clone(), f.origin.clone())).collect();
    NameIndex { names: index, origin }
}

/// 名字只对上一页 → 就是它; 对上好几页 → 只看跟 `from` (发出关系的那一页的来源) 同来源的, 剩一页就是它。
/// 例: 同事自己有「中电福富」, 也装了你分享的「中电福富」—— 你分享的那几页的关系连到你的那页, 他自己的页连他自己的。
/// 同来源里没有 (相关页没一起分享) 而同事那边正好只有一页同名的, 照旧连上 (同一单位里同名多半是同一个东西)。
fn resolve_target(name: &str, index: &NameIndex, from: &str) -> Option<String> {
    let matches = index.names.get(&normalized_name(name))?;
    if matches.len() == 1 {
        return Some(matches[0].clone());
    }
    let same: Vec<&String> = matches.iter().filter(|p| index.origin.get(*p).map(String::as_str) == Some(from)).collect();
    (same.len() == 1).then(|| same[0].clone())
}

fn resolve_diagnostic(name: &str, index: &NameIndex, from: &str) -> (String, Vec<String>) {
    match index.names.get(&normalized_name(name)) {
        None => ("not_found".to_string(), Vec::new()),
        Some(matches) if resolve_target(name, index, from).is_some() => ("resolved".to_string(), matches.clone()),
        Some(matches) => ("ambiguous".to_string(), matches.clone()),
    }
}

fn load_existing_nodes(conn: &Connection) -> Result<HashMap<String, StoredNode>, String> {
    let mut statement = conn
        .prepare(
            "SELECT rel_path, title, slug, aliases, content_hash, relation_hash
             FROM wiki_graph_node",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                StoredNode {
                    title: row.get(1)?,
                    slug: row.get(2)?,
                    aliases_json: row.get(3)?,
                    content_hash: row.get(4)?,
                    relation_hash: row.get(5)?,
                },
            ))
        })
        .map_err(|error| error.to_string())?;
    let mut nodes = HashMap::new();
    for row in rows {
        let (path, node) = row.map_err(|error| error.to_string())?;
        nodes.insert(path, node);
    }
    Ok(nodes)
}

fn node_identity_changed(file: &GraphFile, stored: Option<&StoredNode>) -> bool {
    let Some(stored) = stored else { return true };
    stored.title != file.info.title
        || stored.slug != file.info.slug
        || stored.aliases_json != file.aliases_json
}

fn node_content_changed(file: &GraphFile, stored: Option<&StoredNode>) -> bool {
    stored.is_none_or(|node| node.content_hash != file.content_hash)
}

fn node_relation_changed(file: &GraphFile, stored: Option<&StoredNode>) -> bool {
    stored.is_none_or(|node| node.relation_hash != file.relation_hash)
}

/// 边的生成规则版本。10/23: 正文里的 [[链接]] 记成「提及」, 不再记成「关联」——
/// 知识库页本来就写着正文引用「只用于跳转，不算关系」, 图库却把 278 条正文链接当成没标类型的关系,
/// 关系图一片灰线, 主聊天也拿到一堆「相关」。frontmatter 里真写了「关联」的照旧是「关联」。
/// 规则版本算进 relation_hash: 规则一改, 升级后第一次同步把全部边重建一遍 (增量同步只重建 hash 变了的)。
const EDGE_RULES: &str = "v2-body-link-is-mention";
pub(crate) const BODY_MENTION: &str = "提及";

fn relation_hash_of(related: &[RelatedRef]) -> String {
    digest_json(&(EDGE_RULES, related))
}

fn sync_source_edges(
    transaction: &rusqlite::Transaction<'_>,
    file: &GraphFile,
    index: &NameIndex,
) -> Result<(), String> {
    transaction
        .execute(
            "DELETE FROM wiki_graph_edge WHERE source_path = ?1",
            params![file.info.rel_path],
        )
        .map_err(|error| error.to_string())?;
    transaction
        .execute(
            "DELETE FROM wiki_graph_unresolved WHERE source_path = ?1",
            params![file.info.rel_path],
        )
        .map_err(|error| error.to_string())?;

    for related in &file.info.related {
        let Some(target_path) = resolve_target(&related.name, index, &file.origin) else {
            let (reason, candidates) = resolve_diagnostic(&related.name, index, &file.origin);
            let candidates_json = serde_json::to_string(&candidates).map_err(|error| error.to_string())?;
            transaction
                .execute(
                    "INSERT INTO wiki_graph_unresolved(source_path, source_name, reason, candidates)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![file.info.rel_path, related.name, reason, candidates_json],
                )
                .map_err(|error| error.to_string())?;
            continue;
        };
        if target_path == file.info.rel_path {
            continue;
        }
        transaction
            .execute(
                "INSERT OR IGNORE INTO wiki_graph_edge(source_path, target_path, relation_type, source_name)
                 VALUES (?1, ?2, ?3, ?4)",
                params![
                    file.info.rel_path,
                    target_path,
                    if related.source.as_deref() == Some("body") {
                        BODY_MENTION
                    } else {
                        related.rel.as_deref().unwrap_or("关联")
                    },
                    related.name
                ],
            )
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn sync_graph_in(conn: &mut Connection, home: &Path) -> Result<usize, String> {
    let files = load_graph_files(home)?;
    let current_paths: HashSet<String> = files.iter().map(|file| file.info.rel_path.clone()).collect();
    let existing = load_existing_nodes(conn)?;
    let deleted_paths: Vec<String> = existing
        .keys()
        .filter(|path| !current_paths.contains(*path))
        .cloned()
        .collect();
    let identity_changed = files.iter().any(|file| {
        node_identity_changed(file, existing.get(&file.info.rel_path))
    }) || !deleted_paths.is_empty();
    let changed_files = files
        .iter()
        .filter(|file| {
            node_identity_changed(file, existing.get(&file.info.rel_path))
                || node_content_changed(file, existing.get(&file.info.rel_path))
                || node_relation_changed(file, existing.get(&file.info.rel_path))
        })
        .count()
        + deleted_paths.len();
    let rebuild_edges: HashSet<String> = if identity_changed {
        current_paths.clone()
    } else {
        files
            .iter()
            .filter(|file| node_relation_changed(file, existing.get(&file.info.rel_path)))
            .map(|file| file.info.rel_path.clone())
            .collect()
    };

    let transaction = conn.transaction().map_err(|error| error.to_string())?;
    for path in &deleted_paths {
        transaction
            .execute("DELETE FROM wiki_graph_node WHERE rel_path = ?1", params![path])
            .map_err(|error| error.to_string())?;
    }
    for file in &files {
        if !node_identity_changed(file, existing.get(&file.info.rel_path))
            && !node_content_changed(file, existing.get(&file.info.rel_path))
            && !node_relation_changed(file, existing.get(&file.info.rel_path))
        {
            continue;
        }
        transaction
            .execute(
                "INSERT INTO wiki_graph_node(rel_path, title, slug, kind, aliases, mtime, content_hash, relation_hash)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)
                 ON CONFLICT(rel_path) DO UPDATE SET
                   title = excluded.title,
                   slug = excluded.slug,
                   kind = excluded.kind,
                   aliases = excluded.aliases,
                   mtime = excluded.mtime,
                   content_hash = excluded.content_hash,
                   relation_hash = excluded.relation_hash",
                params![
                    file.info.rel_path,
                    file.info.title,
                    file.info.slug,
                    file.info.kind,
                    file.aliases_json,
                    file.info.mtime,
                    file.content_hash,
                    file.relation_hash
                ],
            )
            .map_err(|error| error.to_string())?;
    }
    let index = build_name_index(&files);
    for file in &files {
        if rebuild_edges.contains(&file.info.rel_path) {
            sync_source_edges(&transaction, file, &index)?;
        }
    }
    transaction
        .execute(
            "INSERT INTO wiki_graph_meta(key, value) VALUES ('last_sync_at', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![Utc::now().to_rfc3339()],
        )
        .map_err(|error| error.to_string())?;
    transaction
        .execute(
            "INSERT INTO wiki_graph_meta(key, value) VALUES ('changed_files', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![changed_files.to_string()],
        )
        .map_err(|error| error.to_string())?;
    let sync_mode = if changed_files == 0 {
        "incremental-noop"
    } else if identity_changed {
        "incremental-with-edge-rebuild"
    } else {
        "incremental"
    };
    transaction
        .execute(
            "INSERT INTO wiki_graph_meta(key, value) VALUES ('sync_mode', ?1)
             ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            params![sync_mode],
        )
        .map_err(|error| error.to_string())?;
    transaction.commit().map_err(|error| error.to_string())?;
    Ok(changed_files)
}

fn open_graph_db(home: &Path) -> Result<Connection, String> {
    fs::create_dir_all(home).map_err(|error| error.to_string())?;
    let conn = Connection::open(graph_db_path(home)).map_err(|error| error.to_string())?;
    conn.busy_timeout(std::time::Duration::from_secs(5))
        .map_err(|error| error.to_string())?;
    ensure_schema(&conn)?;
    Ok(conn)
}

/// 10/23: lib.rs 起的后台线程每 2 分钟调一次 (聊天查关系直接读 wiki_graph.db)
pub(crate) fn sync_graph() -> Result<usize, String> {
    let home = catfish_home()?;
    let mut conn = open_graph_db(&home)?;
    sync_graph_in(&mut conn, &home)
}

fn meta_value(conn: &Connection, key: &str) -> Result<Option<String>, String> {
    conn.query_row(
        "SELECT value FROM wiki_graph_meta WHERE key = ?1",
        params![key],
        |row| row.get(0),
    )
    .optional()
    .map_err(|error| error.to_string())
}

fn read_status(conn: &Connection) -> Result<WikiGraphStatus, String> {
    let node_count = conn
        .query_row("SELECT COUNT(*) FROM wiki_graph_node", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let edge_count = conn
        .query_row("SELECT COUNT(*) FROM wiki_graph_edge", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let unresolved_count = conn
        .query_row("SELECT COUNT(*) FROM wiki_graph_unresolved", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let mut statement = conn
        .prepare(
            "SELECT source_path, source_name, reason, candidates
             FROM wiki_graph_unresolved ORDER BY source_path, source_name LIMIT ?1",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map(params![GRAPH_STATUS_SAMPLE_LIMIT], |row| {
            let candidates_json: String = row.get(3)?;
            Ok(WikiGraphUnresolved {
                source_path: row.get(0)?,
                source_name: row.get(1)?,
                reason: row.get(2)?,
                candidates: serde_json::from_str(&candidates_json).unwrap_or_default(),
            })
        })
        .map_err(|error| error.to_string())?;
    let mut unresolved_samples = Vec::new();
    for row in rows {
        unresolved_samples.push(row.map_err(|error| error.to_string())?);
    }
    Ok(WikiGraphStatus {
        node_count,
        edge_count,
        unresolved_count,
        last_sync_at: meta_value(conn, "last_sync_at")?,
        changed_files: meta_value(conn, "changed_files")?
            .and_then(|value| value.parse().ok())
            .unwrap_or_default(),
        sync_mode: meta_value(conn, "sync_mode")?.unwrap_or_else(|| "unknown".to_string()),
        unresolved_samples,
    })
}

fn relation_weight(relation_type: &str) -> f64 {
    match relation_type.trim().to_ascii_lowercase().as_str() {
        "关联" | "related" => 0.8,
        "隶属" | "属于" | "负责" | "持有" | "owner" => 1.15,
        "依赖" | "支持" | "合作" | "配套" | "depends_on" => 1.1,
        "来源" | "引用" | "参考" | "source" => 0.9,
        _ => 1.0,
    }
}

fn graph_neighbors(
    conn: &Connection,
    seeds: &[(String, usize)],
) -> Result<Vec<GraphCandidate>, String> {
    let mut best: HashMap<String, GraphCandidate> = HashMap::new();
    let mut statement = conn
        .prepare(
            "SELECT n.rel_path, n.title, n.kind,
                    COUNT(*) AS edge_count,
                    GROUP_CONCAT(DISTINCT e.relation_type) AS relation_types
             FROM wiki_graph_edge e
             JOIN wiki_graph_node n ON n.rel_path =
               CASE WHEN e.source_path = ?1 THEN e.target_path ELSE e.source_path END
             WHERE (e.source_path = ?1 OR e.target_path = ?1)
             GROUP BY n.rel_path, n.title, n.kind
             ORDER BY edge_count DESC, n.title
             LIMIT ?2",
        )
        .map_err(|error| error.to_string())?;
    for (seed_index, (seed_path, seed_rank)) in seeds.iter().enumerate() {
        let seed_title = conn
            .query_row(
                "SELECT title FROM wiki_graph_node WHERE rel_path = ?1",
                params![seed_path],
                |row| row.get::<_, String>(0),
            )
            .optional()
            .map_err(|error| error.to_string())?
            .unwrap_or_else(|| seed_path.clone());
        let seed_degree: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM wiki_graph_edge WHERE source_path = ?1 OR target_path = ?1",
                params![seed_path],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if seed_degree > GRAPH_HUB_DEGREE {
            continue;
        }
        let hub_penalty = 1.0
            / (1.0
                + ((seed_degree as f64 / 12.0).ln().max(0.0) * 0.25));
        let rows = statement
            .query_map(params![seed_path, GRAPH_MAX_NEIGHBORS_PER_SEED as i64], |row| {
                let relation_types: String = row.get(4)?;
                let type_weight = relation_types
                    .split(',')
                    .map(relation_weight)
                    .fold(0.0, f64::max);
                let edge_count: i64 = row.get(3)?;
                let density_bonus = 1.0 + (edge_count.min(3) as f64 - 1.0).max(0.0) * 0.1;
                Ok(GraphCandidate {
                    rel_path: row.get(0)?,
                    title: row.get(1)?,
                    kind: row.get(2)?,
                    score: GRAPH_SCORE_WEIGHT * type_weight * density_bonus * hub_penalty
                        / (GRAPH_RRF_K + *seed_rank as f64 + 1.0),
                    seed_title: seed_title.clone(),
                })
            })
            .map_err(|error| error.to_string())?;
        for row in rows {
            let candidate = row.map_err(|error| error.to_string())?;
            if candidate.rel_path == *seed_path {
                continue;
            }
            match best.get_mut(&candidate.rel_path) {
                Some(existing) if candidate.score > existing.score => *existing = candidate,
                None => {
                    best.insert(candidate.rel_path.clone(), candidate);
                }
                _ => {}
            }
        }
        if seed_index + 1 >= seeds.len() {
            break;
        }
    }
    let mut values: Vec<_> = best.into_values().collect();
    values.sort_by(|left, right| {
        right
            .score
            .partial_cmp(&left.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.title.cmp(&right.title))
    });
    Ok(values)
}

pub(crate) fn expand_one_hop(seeds: &[(String, usize)]) -> Result<Vec<GraphCandidate>, String> {
    if seeds.is_empty() {
        return Ok(Vec::new());
    }
    sync_graph()?;
    let home = catfish_home()?;
    let conn = open_graph_db(&home)?;
    graph_neighbors(&conn, seeds)
}

pub(crate) fn semantic_graph_bonus(score: f64) -> f64 {
    score * 8.0
}

#[tauri::command(rename_all = "camelCase")]
pub(crate) async fn wiki_graph_status() -> Result<WikiGraphStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let home = catfish_home()?;
        let mut conn = open_graph_db(&home)?;
        sync_graph_in(&mut conn, &home)?;
        read_status(&conn)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
#[path = "wiki_graph_tests.rs"]
mod tests;
