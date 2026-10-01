use super::*;

#[test]
fn attendees_are_required() {
    let d = tempfile::tempdir().unwrap();
    assert!(create(d.path(), "周会", 0, &[]).unwrap_err().contains("参会人数"));
    assert!(create(d.path(), "周会", MAX_ATTENDEES + 1, &[]).is_err());
    assert!(create(d.path(), "周会", 4, &[]).is_ok());
}

#[test]
fn create_load_list_roundtrip() {
    let d = tempfile::tempdir().unwrap();
    let a = create(d.path(), "  资质集采二期 ", 4, &["中电福富，达华".into(), "达华".into(), " ".into()]).unwrap();
    assert_eq!(a.title, "资质集采二期");
    assert_eq!(a.hotwords, vec!["中电福富", "达华"]);
    assert_eq!(a.status, MeetingStatus::Created);
    assert!(audio_dir(d.path(), &a.id).unwrap().is_dir());
    assert_eq!(load(d.path(), &a.id).unwrap(), a);

    std::thread::sleep(std::time::Duration::from_millis(1100)); // created_at 精确到秒
    let b = create(d.path(), "", 2, &[]).unwrap();
    assert!(b.title.starts_with("会议 "));
    let ids: Vec<String> = list(d.path()).into_iter().map(|m| m.id).collect();
    assert_eq!(ids, vec![b.id.clone(), a.id.clone()], "新的在前");

    let mut b2 = b.clone();
    b2.status = MeetingStatus::Recorded;
    b2.duration_secs = 12.5;
    save(d.path(), &b2).unwrap();
    assert_eq!(load(d.path(), &b.id).unwrap().status, MeetingStatus::Recorded);
}

#[test]
fn broken_meta_is_skipped_not_fatal() {
    let d = tempfile::tempdir().unwrap();
    let ok = create(d.path(), "ok", 3, &[]).unwrap();
    let bad = d.path().join("mtg_20260101-000000_dead");
    std::fs::create_dir_all(&bad).unwrap();
    std::fs::write(bad.join("meta.json"), "{not json").unwrap();
    std::fs::create_dir_all(d.path().join("random-dir")).unwrap();
    let ids: Vec<String> = list(d.path()).into_iter().map(|m| m.id).collect();
    assert_eq!(ids, vec![ok.id]);
}

#[test]
fn ids_cannot_escape_the_meetings_dir() {
    let d = tempfile::tempdir().unwrap();
    for bad in ["../x", "mtg_../../etc", "mtg_a/b", "x_123", "mtg_a\\b"] {
        assert!(meeting_dir(d.path(), bad).is_err(), "{bad}");
    }
    assert!(meeting_dir(d.path(), "mtg_20261001-101500_ab12").is_ok());
}

#[test]
fn hotwords_are_deduped_trimmed_and_capped() {
    let many: Vec<String> = (0..300).map(|i| format!("词{i}")).collect();
    assert_eq!(normalize_hotwords(&many).len(), 100);
    assert_eq!(normalize_hotwords(&["a、b\nc,a".into()]), vec!["a", "b", "c"]);
    assert!(normalize_hotwords(&["这是一个特别特别特别特别特别特别长的不像热词的句子".into()]).is_empty());
}
