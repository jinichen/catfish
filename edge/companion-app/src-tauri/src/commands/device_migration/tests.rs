//! 换机迁移端到端: 两台「电脑」= 两组临时目录。

use std::fs;
use std::path::Path;

use rusqlite::Connection;

use super::snapshot::Roots;
use super::*;

fn machine(dir: &Path, name: &str) -> Roots {
    let home = dir.join(name);
    Roots { catfish: home.join(".catfish"), hermes: home.join(".hermes"), home } // windows-parity: 测试里两台假电脑的目录, 不碰真 Hermes 目录
}

fn me(email: &str, dev: &str) -> Me {
    Me { email: email.into(), device_id: dev.into() }
}

/// 旧机上造一套像样的数据
fn seed_old(r: &Roots) {
    fs::create_dir_all(r.catfish.join("wiki/entities")).unwrap();
    fs::create_dir_all(r.catfish.join("task_chat")).unwrap();
    fs::create_dir_all(r.catfish.join("oauth")).unwrap();
    fs::create_dir_all(r.catfish.join("uploads")).unwrap();
    fs::create_dir_all(r.hermes.join("memories")).unwrap();
    fs::create_dir_all(r.hermes.join("skills/mine")).unwrap();
    fs::write(r.catfish.join("employee_journal.md"), "## 10/30\n跟周工对了巡检表\n").unwrap();
    fs::write(r.catfish.join("wiki/entities/滨江花园.md"), "# 滨江花园三期\n").unwrap();
    let up = r.catfish.join("uploads/a.pdf");
    fs::write(&up, b"%PDF").unwrap();
    // 记着绝对路径的文件: 导入后要改成新机的
    fs::write(r.catfish.join("task_chat/t1.jsonl"), format!("{{\"kept_path\":\"{}\"}}\n{{\"half", up.display())).unwrap();
    fs::write(r.catfish.join("oauth/user_info"), "SECRET-TOKEN").unwrap();
    fs::write(r.catfish.join("search.db"), b"index").unwrap();
    fs::write(r.catfish.join("brand_new_thing.json"), "{}").unwrap();
    fs::write(r.hermes.join(".env"), "API_KEY=sk-secret").unwrap();
    fs::write(r.hermes.join("USER.md"), "林晓, 工程管理部").unwrap();
    fs::write(r.hermes.join("memories/MEMORY.md"), "爱用表格").unwrap();
    fs::write(r.hermes.join("skills/mine/SKILL.md"), "我的技能").unwrap();
    let c = Connection::open(r.hermes.join("state.db")).unwrap();
    c.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE sessions(id TEXT); INSERT INTO sessions VALUES ('s1'),('s2');").unwrap();
    // 外发记录: 两条真记录进链
    let mut db = crate::commands::transparent_log::open_db_at(&r.catfish.join("outbound_log.db")).unwrap();
    for i in 0..2 {
        let row = crate::commands::transparent_log_store::ChainRow {
            ts_request: format!("2026-10-30T0{i}:00:00Z"),
            method: "POST".into(),
            url: "https://ai.example/v1/chat/completions".into(),
            origin: Some("companion".into()),
            dest_kind: Some("central".into()),
            ..Default::default()
        };
        crate::commands::transparent_log_store::insert(&mut db, row, Some(b"{}"), None, None).unwrap();
    }
}

fn seed_new(r: &Roots) {
    // 新装好的机器: Hermes 自带一个内置技能 + 空的对话库
    fs::create_dir_all(r.hermes.join("skills/mine")).unwrap();
    fs::create_dir_all(&r.catfish).unwrap();
    fs::write(r.hermes.join("skills/mine/SKILL.md"), "新机内置版本").unwrap();
    fs::write(r.hermes.join("skills/builtin.md"), "内置").unwrap();
    let c = Connection::open(r.hermes.join("state.db")).unwrap();
    c.execute_batch("CREATE TABLE sessions(id TEXT);").unwrap();
}

fn export(dir: &Path, old: &Roots, optional: &[&str]) -> std::path::PathBuf {
    let pkg = dir.join("迁移包.catfishmove");
    let opt: Vec<String> = optional.iter().map(|s| s.to_string()).collect();
    let rep = export_at(old, &me("linxiao@x.cn", "aaaa1111-0000-4000-8000-000000000001"), &pkg, "correct horse", &opt).unwrap();
    assert!(rep.unclassified.contains(&"catfish/brand_new_thing.json".to_string()));
    pkg
}

#[test]
fn 导出导入_积累都到了_凭证和缓存没带() {
    let t = tempfile::tempdir().unwrap();
    let (old, new) = (machine(t.path(), "old"), machine(t.path(), "new"));
    seed_old(&old);
    seed_new(&new);
    let pkg = export(t.path(), &old, &["catfish/uploads"]);
    // 包是密文: 里面看不到明文
    let raw = fs::read(&pkg).unwrap();
    assert!(!raw.windows(12).any(|w| w == "巡检表".as_bytes()));

    let who = me("LinXiao@x.cn", "bbbb2222-0000-4000-8000-000000000002");
    let info = inspect_at(&new, &who, &pkg, "correct horse").unwrap();
    assert!(info.same_person && info.version_ok && !info.target_has_data, "{info:?}");

    let rep = import_at(&new, &who, &pkg, "correct horse", false).unwrap();
    assert!(rep.paths_rewritten >= 1, "{rep:?}");
    assert!(fs::read_to_string(new.catfish.join("employee_journal.md")).unwrap().contains("巡检表"));
    assert!(new.catfish.join("wiki/entities/滨江花园.md").exists());
    assert_eq!(fs::read_to_string(new.hermes.join("USER.md")).unwrap(), "林晓, 工程管理部");
    assert!(new.catfish.join("uploads/a.pdf").exists());
    // 凭证 / 缓存 / 未归类都没带
    assert!(!new.catfish.join("oauth").exists());
    assert!(!new.hermes.join(".env").exists());
    assert!(!new.catfish.join("search.db").exists());
    assert!(!new.catfish.join("brand_new_thing.json").exists());
    // 对话历史在线写进了新机的库
    let n: i64 = Connection::open(new.hermes.join("state.db")).unwrap().query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0)).unwrap();
    assert_eq!(n, 2);
    // 技能: 新机已有的不覆盖
    assert_eq!(fs::read_to_string(new.hermes.join("skills/mine/SKILL.md")).unwrap(), "新机内置版本");
    assert!(new.hermes.join("skills/builtin.md").exists());
    assert_eq!(rep.kept_existing, 1);
    // 绝对路径改成了新机的; 写到一半的那行没带
    let chat = fs::read_to_string(new.catfish.join("task_chat/t1.jsonl")).unwrap();
    assert!(chat.contains(&new.catfish.join("uploads/a.pdf").display().to_string()), "{chat}");
    assert!(!chat.contains("half"));
}

#[test]
fn 外发记录链接着写_旧机导出后的锚点不算这条链() {
    use crate::commands::transparent_log_store::{verify_for_device, Anchor};
    let t = tempfile::tempdir().unwrap();
    let (old, new) = (machine(t.path(), "old"), machine(t.path(), "new"));
    seed_old(&old);
    seed_new(&new);
    let pkg = export(t.path(), &old, &[]);
    import_at(&new, &me("linxiao@x.cn", "bbbb2222-0000-4000-8000-000000000002"), &pkg, "correct horse", false).unwrap();

    let conn = crate::commands::transparent_log::open_db_at(&new.catfish.join("outbound_log.db")).unwrap();
    let purposes: Vec<String> = conn
        .prepare("SELECT purpose FROM outbound_log ORDER BY id").unwrap()
        .query_map([], |r| r.get::<_, Option<String>>(0)).unwrap()
        .map(|x| x.unwrap().unwrap_or_default()).collect();
    assert_eq!(purposes[2..], ["导出迁移包".to_string(), "设备迁移".to_string()]);
    // 旧机导出时的链头 (第 3 条) 的锚点还对得上; 旧机导出后又报的第 4 条是另一条链, 跳过
    let h3: String = conn.query_row("SELECT row_hash FROM outbound_log WHERE id = 3", [], |r| r.get(0)).unwrap();
    let a = |id: i64, h: &str, dev: &str| Anchor { last_id: id, last_hash: h.into(), received_at: chrono::Utc::now().to_rfc3339(), device_id: Some(dev.into()) };
    let old_dev = "aaaa1111-0000-4000-8000-000000000001";
    let rep = verify_for_device(&conn, &[a(3, &h3, old_dev), a(4, &"f".repeat(64), old_dev)], Some("bbbb2222-0000-4000-8000-000000000002")).unwrap();
    assert!(rep.ok, "{:?}", rep.problems);
    assert_eq!((rep.anchors_checked, rep.anchors_other_device), (1, 1));
}

#[test]
fn 口令错_包被改_别人的包_新机已有数据() {
    let t = tempfile::tempdir().unwrap();
    let (old, new) = (machine(t.path(), "old"), machine(t.path(), "new"));
    seed_old(&old);
    seed_new(&new);
    let pkg = export(t.path(), &old, &[]);
    let who = me("linxiao@x.cn", "bbbb2222-0000-4000-8000-000000000002");

    assert_eq!(inspect_at(&new, &who, &pkg, "wrong horse!").unwrap_err(), "迁移口令不对");

    let mut bad = fs::read(&pkg).unwrap();
    let n = bad.len();
    bad[n - 40] ^= 0xff;
    let bad_pkg = t.path().join("bad.catfishmove");
    fs::write(&bad_pkg, bad).unwrap();
    assert!(import_at(&new, &who, &bad_pkg, "correct horse", false).is_err());

    let other = me("zhougong@x.cn", "cccc3333-0000-4000-8000-000000000003");
    let e = import_at(&new, &other, &pkg, "correct horse", false).unwrap_err();
    assert!(e.contains("只能导给本人"), "{e}");
    assert!(!new.catfish.join("employee_journal.md").exists(), "拒绝时什么都没动");

    fs::write(new.catfish.join("employee_journal.md"), "新机上已经写了点东西").unwrap();
    let e = import_at(&new, &who, &pkg, "correct horse", false).unwrap_err();
    assert!(e.contains("已经有鲶鱼数据"), "{e}");
    // 勾了「先备份再覆盖」: 原来的挪进备份目录
    let rep = import_at(&new, &who, &pkg, "correct horse", true).unwrap();
    let backup = Path::new(rep.backup_dir.as_deref().unwrap()).join("catfish/employee_journal.md");
    assert_eq!(fs::read_to_string(backup).unwrap(), "新机上已经写了点东西");
    assert!(fs::read_to_string(new.catfish.join("employee_journal.md")).unwrap().contains("巡检表"));
    // 临时目录都清掉了
    assert!(!fs::read_dir(&new.catfish).unwrap().flatten().any(|e| e.file_name().to_string_lossy().starts_with(".migration-tmp")));
}

#[test]
fn 口令太短不导出() {
    let t = tempfile::tempdir().unwrap();
    let old = machine(t.path(), "old");
    seed_old(&old);
    let e = export_at(&old, &me("a@x.cn", "aaaa1111-0000-4000-8000-000000000001"), &t.path().join("p"), "1234567", &[]).unwrap_err();
    assert!(e.contains("至少 8 位"));
}
