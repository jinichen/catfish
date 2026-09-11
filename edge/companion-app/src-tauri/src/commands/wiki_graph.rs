use chrono::Utc;
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use super::wiki_read::{build_file_info, catfish_home, collect_all_wiki_md, WikiFileInfo};

const GRAPH_DB_NAME: &str = "wiki_graph.db";
const GRAPH_SCORE_WEIGHT: f64 = 0.75;
const GRAPH_RRF_K: f64 = 60.0;
const GRAPH_MAX_NEIGHBORS_PER_SEED: usize = 24;
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
            relation_hash: digest_json(&info.related),
            content_hash: digest_bytes(&bytes),
            info,
            aliases_json,
        });
    }
    Ok(files)
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

fn build_name_index(files: &[GraphFile]) -> HashMap<String, Vec<String>> {
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
    index
}

fn resolve_target(name: &str, index: &HashMap<String, Vec<String>>) -> Option<String> {
    let matches = index.get(&normalized_name(name))?;
    (matches.len() == 1).then(|| matches[0].clone())
}

fn resolve_diagnostic(name: &str, index: &HashMap<String, Vec<String>>) -> (String, Vec<String>) {
    match index.get(&normalized_name(name)) {
        None => ("not_found".to_string(), Vec::new()),
        Some(matches) if matches.len() == 1 => ("resolved".to_string(), matches.clone()),
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

fn sync_source_edges(
    transaction: &rusqlite::Transaction<'_>,
    file: &GraphFile,
    index: &HashMap<String, Vec<String>>,
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
        let Some(target_path) = resolve_target(&related.name, index) else {
            let (reason, candidates) = resolve_diagnostic(&related.name, index);
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
                    related.rel.as_deref().unwrap_or("关联"),
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
    for file in &files {
        if rebuild_edges.contains(&file.info.rel_path) {
            sync_source_edges(&transaction, file, &build_name_index(&files))?;
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

fn sync_graph() -> Result<usize, String> {
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
        let hub_penalty = 1.0
            / (1.0
                + ((seed_degree as f64 / 12.0).ln().max(0.0) * 0.25));
        let rows = statement
            .query_map(params![seed_path, GRAPH_MAX_NEIGHBORS_PER_SEED], |row| {
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
mod tests {
    use super::*;
    use super::super::wiki_read::RelatedRef;
    use std::time::Duration;
    use tempfile::tempdir;

    fn graph_file(path: &str, title: &str, aliases: &[&str], related: Vec<RelatedRef>) -> GraphFile {
        let info = WikiFileInfo {
            rel_path: path.to_string(),
            title: title.to_string(),
            slug: title.to_ascii_lowercase(),
            kind: "concept".to_string(),
            subtype: None,
            tags: Vec::new(),
            aliases: aliases.iter().map(|value| (*value).to_string()).collect(),
            related,
            sources: Vec::new(),
            size_bytes: title.len() as u64,
            mtime: 1.0,
            authored_by: None,
            ontology_status: None,
        };
        GraphFile {
            aliases_json: serde_json::to_string(&info.aliases).unwrap(),
            content_hash: digest_bytes(title.as_bytes()),
            relation_hash: digest_json(&info.related),
            info,
        }
    }

    #[test]
    fn resolves_alias_and_persists_typed_edge() {
        let mut conn = Connection::open_in_memory().unwrap();
        conn.busy_timeout(Duration::from_secs(1)).unwrap();
        ensure_schema(&conn).unwrap();
        let home = tempdir().unwrap();
        fs::create_dir_all(home.path().join("wiki/entities")).unwrap();
        fs::write(home.path().join("wiki/entities/a.md"), "# A\n\nRelated: [[B]]").unwrap();
        fs::write(home.path().join("wiki/entities/b.md"), "---\naliases: [B]\n---\n# B").unwrap();
        let changed = sync_graph_in(&mut conn, home.path()).unwrap();
        assert_eq!(changed, 2);
        let relation: String = conn
            .query_row(
                "SELECT relation_type FROM wiki_graph_edge WHERE source_path = 'wiki/entities/a.md'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(relation, "关联");
        let status = read_status(&conn).unwrap();
        assert_eq!(status.node_count, 2);
        assert_eq!(status.edge_count, 1);
        assert_eq!(status.unresolved_count, 0);
    }

    #[test]
    fn unchanged_sync_is_incremental_noop_and_delete_is_removed() {
        let mut conn = Connection::open_in_memory().unwrap();
        ensure_schema(&conn).unwrap();
        let home = tempdir().unwrap();
        fs::create_dir_all(home.path().join("wiki/entities")).unwrap();
        fs::write(home.path().join("wiki/entities/a.md"), "# A").unwrap();
        assert_eq!(sync_graph_in(&mut conn, home.path()).unwrap(), 1);
        assert_eq!(sync_graph_in(&mut conn, home.path()).unwrap(), 0);
        fs::remove_file(home.path().join("wiki/entities/a.md")).unwrap();
        assert_eq!(sync_graph_in(&mut conn, home.path()).unwrap(), 1);
        let status = read_status(&conn).unwrap();
        assert_eq!(status.node_count, 0);
        assert_eq!(status.edge_count, 0);
    }

    #[test]
    fn unresolved_and_ambiguous_relations_are_diagnosed() {
        let mut conn = Connection::open_in_memory().unwrap();
        ensure_schema(&conn).unwrap();
        let home = tempdir().unwrap();
        fs::create_dir_all(home.path().join("wiki/entities")).unwrap();
        fs::write(home.path().join("wiki/entities/a.md"), "# A\n\nRelated: [[Missing]]").unwrap();
        fs::write(home.path().join("wiki/entities/b.md"), "# B\n\nRelated: [[Shared]]").unwrap();
        fs::write(home.path().join("wiki/entities/c.md"), "---\naliases: [Shared]\n---\n# C").unwrap();
        fs::write(home.path().join("wiki/entities/d.md"), "---\naliases: [Shared]\n---\n# D").unwrap();
        sync_graph_in(&mut conn, home.path()).unwrap();
        let status = read_status(&conn).unwrap();
        assert_eq!(status.unresolved_count, 2);
        assert!(status.unresolved_samples.iter().any(|item| item.reason == "not_found"));
        assert!(status.unresolved_samples.iter().any(|item| item.reason == "ambiguous"));
    }

    #[test]
    fn graph_neighbors_are_bidirectional_weighted_and_bounded() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_schema(&conn).unwrap();
        conn.execute("INSERT INTO wiki_graph_node(rel_path,title,slug,kind,aliases) VALUES ('a','A','a','主题','[]'),('b','B','b','主题','[]')", []).unwrap();
        conn.execute("INSERT INTO wiki_graph_edge(source_path,target_path,relation_type,source_name) VALUES ('a','b','关联','B')", []).unwrap();
        let values = graph_neighbors(&conn, &[("b".to_string(), 0)]).unwrap();
        assert_eq!(values[0].rel_path, "a");
        assert!(values[0].score > 0.0 && values[0].score < 0.02);
    }

    #[test]
    fn duplicate_alias_is_not_resolved() {
        let files = vec![
            graph_file("a.md", "A", &["Shared"], Vec::new()),
            graph_file("b.md", "B", &["Shared"], Vec::new()),
        ];
        let index = build_name_index(&files);
        assert!(resolve_target("Shared", &index).is_none());
        assert_eq!(resolve_diagnostic("Shared", &index).0, "ambiguous");
    }
}
