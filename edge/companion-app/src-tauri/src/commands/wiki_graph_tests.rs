//! wiki_graph.rs 的测试 (11/23 拆出来: 主文件守 800 行)。

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
        conflicts: Vec::new(),
        updated: None,
        tracks_status: false,
        evidence: Vec::new(),
        unverified: Vec::new(),
    };
    GraphFile {
        origin: if path.starts_with("wiki-shared/") { "pub:a@x".to_string() } else { "self".to_string() },
        aliases_json: serde_json::to_string(&info.aliases).unwrap(),
        content_hash: digest_bytes(title.as_bytes()),
        relation_hash: relation_hash_of(&info.related),
        info,
    }
}

#[test]
fn 规则升级后旧的正文链接边重建成提及() {
    let mut conn = Connection::open_in_memory().unwrap();
    ensure_schema(&conn).unwrap();
    let home = tempdir().unwrap();
    fs::create_dir_all(home.path().join("wiki/entities")).unwrap();
    fs::write(home.path().join("wiki/entities/a.md"), "# A\n\nRelated: [[B]]").unwrap();
    fs::write(home.path().join("wiki/entities/b.md"), "# B").unwrap();
    sync_graph_in(&mut conn, home.path()).unwrap();
    // 模拟老版本留下的库: 边是「关联」, hash 是旧算法算的
    conn.execute("UPDATE wiki_graph_edge SET relation_type = '关联'", []).unwrap();
    conn.execute("UPDATE wiki_graph_node SET relation_hash = 'old'", []).unwrap();
    sync_graph_in(&mut conn, home.path()).unwrap();
    let relation: String = conn
        .query_row("SELECT relation_type FROM wiki_graph_edge", [], |row| row.get(0))
        .unwrap();
    assert_eq!(relation, BODY_MENTION);
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
    // 10/23: 正文里的 [[B]] 是「提及」, 不是关系
    assert_eq!(relation, BODY_MENTION);
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
fn 同事装了分享页_关系连到发布人的那页_自己的连自己的() {
    // 同事自己的知识库有「中电福富」(他写的) 和「市场部」(指向中电福富);
    // 又装了林晓分享的「市场部」「中电福富」(林晓那页的「市场部 隶属 中电福富」) 和王工分享的「中电福富」
    let mut conn = Connection::open_in_memory().unwrap();
    ensure_schema(&conn).unwrap();
    let home = tempdir().unwrap();
    let own = home.path().join("wiki/entities");
    let dept = home.path().join("wiki-shared/dept/市场部");
    fs::create_dir_all(&own).unwrap();
    fs::create_dir_all(&dept).unwrap();
    fs::write(own.join("中电福富.md"), "---\ntitle: 中电福富\n---\n# 中电福富\n同事自己写的").unwrap();
    fs::write(own.join("市场部.md"), "---\ntitle: 市场部\nrelated: [{name: \"中电福富\", rel: \"隶属\"}]\n---\n# 市场部").unwrap();
    let page = |id: &str, body: &str, by: &str| {
        fs::write(dept.join(format!("{id}.md")), body).unwrap();
        fs::write(dept.join(format!("{id}.meta.json")), format!(r#"{{"published_by": "{by}"}}"#)).unwrap();
    };
    page("lx1", "---\ntype: entity\ntitle: 市场部\nrelated: [{name: \"中电福富\", rel: \"隶属\"}]\n---\n# 市场部", "linxiao@x.cn");
    page("lx2", "---\ntype: entity\ntitle: 中电福富\n---\n# 中电福富\n林晓写的", "linxiao@x.cn");
    page("wg1", "---\ntype: entity\ntitle: 中电福富\n---\n# 中电福富\n王工写的", "wang@x.cn");
    sync_graph_in(&mut conn, home.path()).unwrap();
    let target = |src: &str| -> Option<String> {
        conn.query_row("SELECT target_path FROM wiki_graph_edge WHERE source_path = ?1 AND relation_type = '隶属'", [src], |r| r.get(0))
            .optional()
            .unwrap()
    };
    assert_eq!(target("wiki-shared/dept/市场部/lx1.md").as_deref(), Some("wiki-shared/dept/市场部/lx2.md"), "林晓的页连林晓的");
    assert_eq!(target("wiki/entities/市场部.md").as_deref(), Some("wiki/entities/中电福富.md"), "同事自己的连自己的");
    // 原来: 三页同名 → 两条关系都算「重名」丢掉
    let unresolved: i64 = conn.query_row("SELECT COUNT(*) FROM wiki_graph_unresolved", [], |r| r.get(0)).unwrap();
    assert_eq!(unresolved, 0);
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
fn hub_seed_is_not_expanded() {
    let conn = Connection::open_in_memory().unwrap();
    ensure_schema(&conn).unwrap();
    conn.execute("INSERT INTO wiki_graph_node(rel_path,title,slug,kind,aliases) VALUES ('hub','公司','hub','实体','[]')", []).unwrap();
    for i in 0..=GRAPH_HUB_DEGREE {
        conn.execute(
            "INSERT INTO wiki_graph_node(rel_path,title,slug,kind,aliases) VALUES (?1,?1,?1,'实体','[]')",
            params![format!("n{i}")],
        ).unwrap();
        conn.execute(
            "INSERT INTO wiki_graph_edge(source_path,target_path,relation_type,source_name) VALUES ('hub',?1,'持有',?1)",
            params![format!("n{i}")],
        ).unwrap();
    }
    assert!(graph_neighbors(&conn, &[("hub".to_string(), 0)]).unwrap().is_empty());
    // 普通条目照样扩 (n0 只连着公司这一条)
    assert_eq!(graph_neighbors(&conn, &[("n0".to_string(), 0)]).unwrap().len(), 1);
}

#[test]
fn duplicate_alias_is_not_resolved() {
    let files = vec![
        graph_file("a.md", "A", &["Shared"], Vec::new()),
        graph_file("b.md", "B", &["Shared"], Vec::new()),
    ];
    let index = build_name_index(&files);
    assert!(resolve_target("Shared", &index, "self").is_none());
    assert_eq!(resolve_diagnostic("Shared", &index, "self").0, "ambiguous");
}

#[test]
fn 同名时先找同一来源_装的连发布人的_自己的连自己的() {
    let files = vec![
        graph_file("wiki/entities/mine.md", "中电福富", &[], Vec::new()),
        graph_file("wiki-shared/dept/市场部/f1.md", "中电福富", &[], Vec::new()),
        graph_file("wiki-shared/dept/市场部/f2.md", "ITSS一级", &[], Vec::new()),
    ];
    let index = build_name_index(&files);
    assert_eq!(resolve_target("中电福富", &index, "pub:a@x").as_deref(), Some("wiki-shared/dept/市场部/f1.md"));
    assert_eq!(resolve_target("中电福富", &index, "self").as_deref(), Some("wiki/entities/mine.md"));
    assert_eq!(resolve_diagnostic("中电福富", &index, "self").0, "resolved");
    // 别的发布人的页 (来源对不上) 遇到重名照旧算重名
    assert!(resolve_target("中电福富", &index, "pub:b@x").is_none());
    // 只对上一页: 不管来源
    assert_eq!(resolve_target("ITSS一级", &index, "self").as_deref(), Some("wiki-shared/dept/市场部/f2.md"));
}

#[test]
fn 装的页来源读_meta_里的发布人() {
    let t = tempfile::tempdir().unwrap();
    let dir = t.path().join("wiki-shared/dept/市场部");
    fs::create_dir_all(&dir).unwrap();
    let page = dir.join("f1.md");
    fs::write(&page, "---\ntitle: x\n---\n").unwrap();
    assert_eq!(origin_of(&page, "wiki-shared/dept/市场部/f1.md"), "dept:市场部");
    fs::write(dir.join("f1.meta.json"), r#"{"published_by": "LinXiao@x.cn"}"#).unwrap();
    assert_eq!(origin_of(&page, "wiki-shared/dept/市场部/f1.md"), "pub:linxiao@x.cn");
    assert_eq!(origin_of(&t.path().join("wiki/entities/a.md"), "wiki/entities/a.md"), "self");
}
