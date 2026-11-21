//! 11/21 扫码直传 (公钥加密的包) + 误删保护快照, 端到端 (临时目录里两台 / 一台假电脑)。

use std::collections::HashSet;
use std::fs;
use std::str::FromStr;

use rusqlite::Connection;

use super::package::{Lock, RoomsDb, Unlock};
use super::tests::{machine, me, pw, seed_old};
use super::*;

const OLD: &str = "aaaa1111-0000-4000-8000-000000000001";
const NEW: &str = "bbbb2222-0000-4000-8000-000000000002";

#[test]
fn 直传的包_只有新电脑那把私钥解得开() {
    let t = tempfile::tempdir().unwrap();
    let (old, new) = (machine(t.path(), "old"), machine(t.path(), "new"));
    seed_old(&old);
    fs::create_dir_all(&new.catfish).unwrap();
    let key = age::x25519::Identity::generate();
    let pkg = t.path().join("direct.catfishmove");
    export_with(&old, &me("linxiao@x.cn", OLD), &pkg, &Lock::Recipient(key.to_public()), &[], &RoomsDb::default(), true).unwrap();

    let who = me("linxiao@x.cn", NEW);
    let other = age::x25519::Identity::generate();
    let e = inspect_at(&new, &who, &pkg, &Unlock::Identity(other)).unwrap_err();
    assert!(e.contains("不是给这台电脑的"), "{e}");
    assert!(inspect_at(&new, &who, &pkg, &pw("correct horse")).is_err());

    let info = inspect_at(&new, &who, &pkg, &Unlock::Identity(key.clone())).unwrap();
    assert!(info.same_person && info.version_ok && !info.target_has_data, "{info:?}");
    import_at(&new, &who, &pkg, &Unlock::Identity(key), false).unwrap();
    assert!(fs::read_to_string(new.catfish.join("employee_journal.md")).unwrap().contains("巡检表"));
    assert!(!new.hermes.join(".env").exists()); // 凭证照样不带
}

fn egress_rows(r: &snapshot::Roots) -> i64 {
    Connection::open(r.catfish.join("outbound_log.db")).unwrap().query_row("SELECT COUNT(*) FROM outbound_log", [], |x| x.get(0)).unwrap()
}

#[test]
fn 快照_做_列_留几份_只还原挑的那几项() {
    let t = tempfile::tempdir().unwrap();
    let pc = machine(t.path(), "pc");
    seed_old(&pc);
    let who = me("linxiao@x.cn", OLD);
    let key = age::x25519::Identity::generate();
    let rows_before = egress_rows(&pc);

    for _ in 0..3 {
        snapshots::make(&pc, &who, &key, &RoomsDb::default(), 2).unwrap();
    }
    let list = snapshots::list(&pc);
    assert_eq!(list.len(), 2, "只留最新 2 份");
    assert!(list[0].created_at >= list[1].created_at);
    // 不记「导出迁移包」那行外发记录 (没离开这台电脑)
    assert_eq!(egress_rows(&pc), rows_before);
    // 快照是密文; 明文摘要里只有清单, 没有内容
    let raw = fs::read(snapshots::dir(&pc).join(&list[0].name)).unwrap();
    assert!(!raw.windows(9).any(|w| w == "巡检表".as_bytes()));
    let names: HashSet<String> = list[0].entries.iter().map(|e| format!("{}/{}", e.root.dir_name(), e.name)).collect();
    assert!(names.contains("catfish/wiki") && names.contains("catfish/employee_journal.md") && names.contains("hermes/state.db"));
    assert!(!names.contains("catfish/uploads") && !names.contains("catfish/oauth"), "{names:?}");

    // 员工误删了知识库条目, 之后日志又写了新的, 外发记录也多了一行
    fs::remove_file(pc.catfish.join("wiki/entities/滨江花园.md")).unwrap();
    fs::write(pc.catfish.join("employee_journal.md"), "## 11/21\n新写的\n").unwrap();
    let mut db = crate::commands::transparent_log::open_db_at(&pc.catfish.join("outbound_log.db")).unwrap();
    let row = crate::commands::transparent_log_store::ChainRow {
        ts_request: "2026-11-21T00:00:00Z".into(),
        method: "POST".into(),
        url: "https://ai.example/v1/chat/completions".into(),
        ..Default::default()
    };
    crate::commands::transparent_log_store::insert(&mut db, row, Some(b"{}"), None, None).unwrap();
    drop(db);
    let rows_now = egress_rows(&pc);

    // 只挑知识库 (外发记录也勾上了 —— 不该还原, 自动去掉)
    let only = vec!["catfish/wiki".to_string(), "catfish/outbound_log.db".to_string()];
    let rep = snapshots::restore(&pc, &who, &key, &list[0].name, &only).unwrap();
    assert!(pc.catfish.join("wiki/entities/滨江花园.md").exists(), "知识库回来了");
    assert!(fs::read_to_string(pc.catfish.join("employee_journal.md")).unwrap().contains("新写的"), "没挑的不动");
    assert_eq!(egress_rows(&pc), rows_now, "外发记录不还原");
    let backup = rep.backup_dir.expect("还原前当前数据挪进了备份");
    assert!(std::path::Path::new(&backup).join("catfish/wiki").exists());

    assert!(snapshots::restore(&pc, &who, &key, &list[0].name, &["catfish/outbound_log.db".into()]).is_err(), "只挑了不可还原的 = 没挑");
    assert!(snapshots::restore(&pc, &who, &key, "鲶鱼快照-不存在.catfishsnap", &only).is_err());
    let wrong = age::x25519::Identity::generate();
    assert!(snapshots::restore(&pc, &who, &wrong, &list[0].name, &only).is_err(), "别的密钥解不开");
}

#[test]
fn 快照设置_默认开_留三份_越界收回() {
    let t = tempfile::tempdir().unwrap();
    let pc = machine(t.path(), "pc");
    let s = snapshots::load_settings(&pc);
    assert!(s.enabled && s.keep == snapshots::KEEP_DEFAULT);
    snapshots::save_settings(&pc, &snapshots::Settings { enabled: false, keep: 99, last_error: None, last_try_at: None }).unwrap();
    let s = snapshots::load_settings(&pc);
    assert!(!s.enabled && s.keep == snapshots::KEEP_MAX);
}

/// 真中央端到端 (默认不跑): 本机起一个临时中央 (真 PG + dev 账号), 指过来跑。
/// `CATFISH_TEST_GATEWAY=http://127.0.0.1:18999 CATFISH_TEST_TOKEN=… cargo test --lib direct_e2e -- --ignored`
/// 中央用 CATFISH_DEVICE_TRANSFER_WINDOW_MB=2 起, 6MB 的随机文件 (压不小) 才会真把窗口塞满、来回等。
#[tokio::test]
#[ignore]
async fn direct_e2e_经真中央_边传边收_新电脑导进去() {
    use super::direct_net::{download, upload, Conn};
    use std::sync::atomic::AtomicBool;
    let (Ok(base), Ok(token)) = (std::env::var("CATFISH_TEST_GATEWAY"), std::env::var("CATFISH_TEST_TOKEN")) else {
        panic!("要设 CATFISH_TEST_GATEWAY / CATFISH_TEST_TOKEN");
    };
    let t = tempfile::tempdir().unwrap();
    let (old, new) = (machine(t.path(), "old"), machine(t.path(), "new"));
    seed_old(&old);
    let mut big = vec![0u8; 6 * 1024 * 1024];
    rand::RngCore::fill_bytes(&mut rand::rngs::OsRng, &mut big); // 真随机, 压不小
    fs::write(old.catfish.join("wiki/entities/大附件.bin"), &big).unwrap();
    fs::create_dir_all(&new.catfish).unwrap();

    // 新电脑: 现场生成密钥, 开一次直传
    let conn = Conn::fixed(&base, &token);
    let key = age::x25519::Identity::generate();
    let recipient = key.to_public().to_string();
    let v = conn.api(reqwest::Method::POST, "/api/device-transfer", Some(serde_json::json!({ "recipient": recipient, "to_device": "新电脑" }))).await.unwrap();
    let (id, code) = (v["id"].as_str().unwrap().to_string(), v["code"].as_str().unwrap().to_string());
    // 旧电脑: 手输配对码找到它, 公钥跟新电脑的一样 (核对数字也一样)
    let found = conn.api(reqwest::Method::GET, &format!("/api/device-transfer/by-code/{}", code.to_lowercase().replace('-', " ")), None).await.unwrap();
    assert_eq!(found["recipient"].as_str().unwrap(), recipient);
    assert_eq!(direct::safety_number(found["recipient"].as_str().unwrap()), direct::safety_number(&recipient));

    // 旧电脑打包 (用新电脑的公钥加密)
    let pkg = t.path().join("out.catfishmove");
    let r = age::x25519::Recipient::from_str(found["recipient"].as_str().unwrap()).unwrap();
    export_with(&old, &me("linxiao@x.cn", OLD), &pkg, &Lock::Recipient(r), &[], &RoomsDb::default(), true).unwrap();
    let size = fs::metadata(&pkg).unwrap().len();
    assert!(size > 6 * 1024 * 1024, "大文件压不小, 包要 > 6MB 才能测到窗口: {size}");

    // 同时: 旧电脑传、新电脑收
    let stop = AtomicBool::new(false);
    let part = t.path().join("in.part");
    let (sent, got) = tokio::join!(
        upload(&conn, &id, &pkg, "旧电脑", &stop, |_| {}),
        download(&conn, &id, &part, &stop, |_| {}),
    );
    assert_eq!(sent.unwrap(), Some(size));
    assert_eq!(got.unwrap(), Some(size));
    assert_eq!(fs::read(&part).unwrap(), fs::read(&pkg).unwrap(), "收到的跟发的一字不差");
    let info = conn.info(&id).await.unwrap();
    assert_eq!(info["state"], "done");
    assert_eq!(info["buffered"], 0, "中央一块都不留");
    assert_eq!(info["relayed"].as_u64().unwrap(), size);

    // 新电脑用自己的私钥解开、导进去
    import_at(&new, &me("linxiao@x.cn", NEW), &part, &Unlock::Identity(key), false).unwrap();
    assert_eq!(fs::read(new.catfish.join("wiki/entities/大附件.bin")).unwrap(), big);
    assert!(fs::read_to_string(new.catfish.join("employee_journal.md")).unwrap().contains("巡检表"));
}
