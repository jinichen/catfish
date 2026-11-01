//! 手机通道 (docs/MOBILE-CLIENT-DESIGN.md) —— 员工在手机上跟**自己电脑上的小鲶**说话。
//!
//! 电脑主动连中央的中转站 (`/relay/ws`, 地址由已配好的中央地址推出, 不另配), 不用对外开端口。
//! 手机发来的都是端到端密文, 中转站看不到内容; 解开后只放行 ops.rs 的白名单。
//!
//! 流程:
//!   配对   手机发 pair_request(公钥) → 电脑回 pair_offer(公钥) → 两边各算验证码 →
//!          员工在电脑上核对、点允许 → pair_result ok, 手机记进 phones.json
//!   握手   手机每次连上发 hello(临时公钥, 长期钥匙加密) → 电脑回 hello_ack(临时公钥) →
//!          之后的消息都用临时钥匙 (断开后旧消息解不开)
//!   消息   见 crypto.rs 的分段格式; 解出的 JSON 交给 ops::dispatch
//!
//! 默认关, 员工在「设置 → 手机」里打开才连。

pub mod crypto;
pub mod ops;
pub mod store;

use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tauri::Emitter;
use tokio::sync::{mpsc, Notify};
use tokio_tungstenite::tungstenite::Message;

/// 中央地址 → 中转站地址: http → ws, https → wss, 路径 /relay/ws
pub fn relay_url(gateway_base: &str) -> String {
    let base = gateway_base.trim_end_matches('/');
    let ws = if let Some(rest) = base.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = base.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        format!("ws://{base}")
    };
    format!("{ws}/relay/ws")
}

struct PhoneSession {
    boxx: crypto_box::SalsaBox,
    send_seq: u32,
    next_mid: u32,
    recv: crypto::Reassembler,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PendingPair {
    pub device: String,
    pub name: String,
    pub code: String,
    #[serde(skip)]
    public_key: String,
}

#[derive(Default)]
struct State {
    connected: bool,
    error: Option<String>,
    max_phones: Option<u32>,
    tx: Option<mpsc::UnboundedSender<String>>,
    sessions: HashMap<String, PhoneSession>,
    pending: HashMap<String, PendingPair>,
    /// 刚点了「允许」、等中转站确认的那台 (超上限时中转站回 phone_limit, 要撤回)
    last_accepted: Option<(String, Instant)>,
}

struct Bridge {
    state: Mutex<State>,
    wake: Notify,
    app: OnceLock<tauri::AppHandle>,
}

fn bridge() -> &'static Bridge {
    static B: OnceLock<Bridge> = OnceLock::new();
    B.get_or_init(|| Bridge { state: Mutex::default(), wake: Notify::new(), app: OnceLock::new() })
}

/// 测试用: 换到临时目录, 不碰本机真实的 ~/.catfish/mobile
#[cfg(test)]
static TEST_DIR: Mutex<Option<std::path::PathBuf>> = Mutex::new(None);

fn dir() -> Result<std::path::PathBuf, String> {
    #[cfg(test)]
    if let Some(d) = TEST_DIR.lock().unwrap().clone() {
        return Ok(d);
    }
    store::dir().ok_or_else(|| "找不到用户目录".to_string())
}

fn emit(event: &str, payload: Value) {
    if let Some(app) = bridge().app.get() {
        let _ = app.emit(event, payload);
    }
}

fn send_raw(frame: Value) {
    if let Some(tx) = &bridge().state.lock().unwrap().tx {
        let _ = tx.send(frame.to_string());
    }
}

/// 加密后发给某台手机; 返回明文字节数 (记外发用)。没握手就发不了 (手机会重新 hello)。
fn send_to_phone(phone: &str, m: &Value) -> usize {
    let body = m.to_string().into_bytes();
    let mut st = bridge().state.lock().unwrap();
    let Some(tx) = st.tx.clone() else { return 0 };
    let Some(sess) = st.sessions.get_mut(phone) else { return 0 };
    sess.next_mid = sess.next_mid.wrapping_add(1);
    let mid = sess.next_mid;
    for piece in crypto::split(&body, mid, &mut sess.send_seq) {
        let d = crypto::seal(&sess.boxx, crypto::KIND_SESSION, &piece);
        let _ = tx.send(json!({"t": "msg", "to": phone, "d": d}).to_string());
    }
    body.len()
}

fn send_long(phone_pk: &[u8; 32], phone: &str, m: Value) -> Result<(), String> {
    let sk = store::keys_in(&dir()?)?;
    let bx = crypto::make_box(phone_pk, &sk);
    let d = crypto::seal(&bx, crypto::KIND_LONG, m.to_string().as_bytes());
    send_raw(json!({"t": "msg", "to": phone, "d": d}));
    Ok(())
}

fn device_name() -> String {
    let host = hostname_guess();
    if host.is_empty() { "我的电脑".into() } else { host }
}

fn hostname_guess() -> String {
    for k in ["COMPUTERNAME", "HOSTNAME"] {
        if let Ok(v) = std::env::var(k) {
            if !v.trim().is_empty() {
                return v.trim().chars().take(40).collect();
            }
        }
    }
    std::process::Command::new("hostname")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().trim_end_matches(".local").chars().take(40).collect())
        .unwrap_or_default()
}

// ── 中转站帧 ────────────────────────────────────────────────

fn on_relay_frame(text: &str) {
    let Ok(f) = serde_json::from_str::<Value>(text) else { return };
    let from = f.get("from").and_then(Value::as_str).unwrap_or("").to_string();
    match f.get("t").and_then(Value::as_str).unwrap_or("") {
        "ready" => {
            let mut st = bridge().state.lock().unwrap();
            st.max_phones = f.get("max_phones").and_then(Value::as_u64).map(|n| n as u32);
        }
        "pair_request" => on_pair_request(&from, &f),
        "msg" => on_msg(&from, f.get("d").and_then(Value::as_str).unwrap_or("")),
        "error" => on_error(&f),
        _ => {}
    }
}

fn on_pair_request(phone: &str, f: &Value) {
    let pk_s = f.get("pk").and_then(Value::as_str).unwrap_or("");
    let Some(phone_pk) = crypto::key32(pk_s) else { return };
    let Ok(sk) = dir().and_then(|d| store::keys_in(&d)) else { return };
    let my_pk = crypto::public_of(&sk);
    let name = f.get("name").and_then(Value::as_str).unwrap_or("手机").chars().take(40).collect::<String>();
    let pending = PendingPair {
        device: phone.to_string(),
        name: if name.trim().is_empty() { "手机".into() } else { name },
        code: crypto::sas(&phone_pk, &my_pk),
        public_key: pk_s.to_string(),
    };
    bridge().state.lock().unwrap().pending.insert(phone.to_string(), pending.clone());
    send_raw(json!({"t": "pair_offer", "to": phone, "pk": crypto::b64(&my_pk)}));
    emit("mobile://pair-request", serde_json::to_value(&pending).unwrap_or(Value::Null));
    // 窗口可能收在托盘里: 系统通知提醒一下 (osascript / PowerShell 要几百毫秒, 不卡连接循环)
    let body = format!("{} · 验证码 {}，请在电脑上核对后允许", pending.name, pending.code);
    if !cfg!(test) {
        std::thread::spawn(move || {
            let _ = crate::services::desktop_notify::show("有一台手机请求连接小鲶", &body);
        });
    }
}

fn on_error(f: &Value) {
    let code = f.get("code").and_then(Value::as_str).unwrap_or("");
    if code == "phone_limit" {
        let undo = {
            let mut st = bridge().state.lock().unwrap();
            st.last_accepted.take().filter(|(_, at)| at.elapsed() < Duration::from_secs(30))
        };
        if let Some((phone, _)) = undo {
            if let Ok(d) = dir() {
                let _ = store::remove_phone_in(&d, &phone);
            }
            let max = f.get("max_phones").and_then(Value::as_u64).unwrap_or(0);
            emit("mobile://pair-failed", json!({"device": phone, "reason": format!(
                "已经配了 {max} 台手机, 到了单位设置的上限。先在这里撤销一台再配新的。")}));
            emit("mobile://changed", Value::Null);
        }
    } else if !code.is_empty() {
        log::info!("[mobile] 中转站回错: {code}");
    }
}

fn on_msg(phone: &str, d: &str) {
    let Some((kind, raw)) = crypto::kind_of(d) else { return };
    let Ok(dir) = dir() else { return };
    let Some(paired) = store::phones_in(&dir).into_iter().find(|p| p.device == phone) else {
        // 这台已经撤销了 (或电脑重装过): 让中转站也删掉, 手机那边会显示「已解除」
        send_raw(json!({"t": "revoke", "device": phone}));
        return;
    };
    let Some(phone_pk) = crypto::key32(&paired.public_key) else { return };
    match kind {
        crypto::KIND_LONG => on_hello(phone, &phone_pk, &raw),
        crypto::KIND_SESSION => {
            let done = {
                let mut st = bridge().state.lock().unwrap();
                match st.sessions.get_mut(phone) {
                    None => None,
                    Some(sess) => match crypto::open(&sess.boxx, &raw) {
                        None => None,
                        Some(plain) => Some(sess.recv.push(&plain)),
                    },
                }
            };
            match done {
                // 解不开 (电脑重启过 / 会话换了): 请手机重新握手
                None => {
                    let _ = send_long(&phone_pk, phone, json!({"op": "rehello"}));
                }
                Some(crypto::Piece::Done(body)) => {
                    let Ok(m) = serde_json::from_slice::<Value>(&body) else { return };
                    let phone_s = phone.to_string();
                    let out: ops::Outbox = Arc::new(move |v| send_to_phone(&phone_s, &v));
                    tauri::async_runtime::spawn(ops::dispatch(phone.to_string(), m, out));
                }
                Some(_) => {}
            }
        }
        _ => {}
    }
}

fn on_hello(phone: &str, phone_pk: &[u8; 32], raw: &[u8]) {
    let Ok(sk) = dir().and_then(|d| store::keys_in(&d)) else { return };
    let long = crypto::make_box(phone_pk, &sk);
    let Some(plain) = crypto::open(&long, raw) else { return };
    let Ok(m) = serde_json::from_slice::<Value>(&plain) else { return };
    if m.get("op").and_then(Value::as_str) != Some("hello") {
        return;
    }
    let Some(peer_eph) = m.get("eph").and_then(Value::as_str).and_then(crypto::key32) else { return };
    let sid = m.get("sid").and_then(Value::as_str).unwrap_or("").to_string();
    let eph = crypto::new_secret();
    let eph_pk = crypto::public_of(&eph);
    bridge().state.lock().unwrap().sessions.insert(
        phone.to_string(),
        PhoneSession { boxx: crypto::make_box(&peer_eph, &eph), send_seq: 0, next_mid: 0, recv: Default::default() },
    );
    let _ = send_long(phone_pk, phone, json!({"op": "hello_ack", "eph": crypto::b64(&eph_pk), "sid": sid,
                                             "name": device_name()}));
}

// ── 连接循环 ────────────────────────────────────────────────

fn set_status(connected: bool, error: Option<String>) {
    {
        let mut st = bridge().state.lock().unwrap();
        st.connected = connected;
        st.error = error;
        if !connected {
            st.tx = None;
            st.sessions.clear();
        }
    }
    emit("mobile://changed", Value::Null);
}

async fn run_once() -> Result<(), String> {
    let token = crate::services::oauth::ensure_fresh_access_token()
        .await
        .ok_or("还没登录单位账号")?;
    let device = crate::services::device_id::current().ok_or("找不到本机设备号")?;
    let url = relay_url(&crate::services::endpoints::endpoints().gateway_base());
    let (ws, _) = tokio_tungstenite::connect_async(&url)
        .await
        .map_err(|e| format!("连不上中央中转站 ({url}): {e}"))?;
    let (mut sink, mut stream) = ws.split();
    let auth = json!({"t": "auth", "token": token, "role": "pc", "device": device, "name": device_name()});
    sink.send(Message::Text(auth.to_string())).await.map_err(|e| e.to_string())?;

    let first = tokio::time::timeout(Duration::from_secs(10), stream.next())
        .await
        .map_err(|_| "中转站没有回应".to_string())?
        .ok_or("中转站断开了")?
        .map_err(|e| e.to_string())?;
    let first = first.into_text().unwrap_or_default();
    let ready: Value = serde_json::from_str(&first).unwrap_or(Value::Null);
    if ready.get("t").and_then(Value::as_str) != Some("ready") {
        let code = ready.get("code").and_then(Value::as_str).unwrap_or("unknown");
        return Err(match code {
            "auth_failed" | "auth_required" => "中央不认这个登录 (重新登录单位账号试试)".into(),
            "mobile_disabled" => "单位没开放手机端".into(),
            "not_found" => "中央还没升级到支持手机的版本".into(),
            c => format!("中转站拒绝连接: {c}"),
        });
    }
    on_relay_frame(&first);

    let (tx, mut rx) = mpsc::unbounded_channel::<String>();
    bridge().state.lock().unwrap().tx = Some(tx);
    set_status(true, None);
    // 中转站那边以电脑上的名单为准 (电脑离线时撤销的, 这里补上)
    let phones: Vec<String> = dir().map(|d| store::phones_in(&d).into_iter().map(|p| p.device).collect()).unwrap_or_default();
    send_raw(json!({"t": "sync_pairs", "phones": phones}));

    let mut ping = tokio::time::interval(Duration::from_secs(25));
    loop {
        tokio::select! {
            msg = stream.next() => match msg {
                Some(Ok(Message::Text(t))) => on_relay_frame(&t),
                Some(Ok(Message::Close(_))) | None => return Err("中转站断开了".into()),
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(format!("连接中断: {e}")),
            },
            out = rx.recv() => match out {
                Some(t) => sink.send(Message::Text(t)).await.map_err(|e| format!("发送失败: {e}"))?,
                None => return Ok(()),
            },
            _ = ping.tick() => { let _ = sink.send(Message::Text(json!({"t": "ping"}).to_string())).await; }
            _ = bridge().wake.notified() => {
                if !dir().map(|d| store::enabled_in(&d)).unwrap_or(false) {
                    let _ = sink.close().await;
                    return Ok(());
                }
            }
        }
    }
}

async fn supervisor() {
    let mut backoff = 2u64;
    loop {
        let enabled = dir().map(|d| store::enabled_in(&d)).unwrap_or(false);
        if !enabled {
            set_status(false, None);
            bridge().wake.notified().await;
            continue;
        }
        let started = Instant::now();
        let result = run_once().await;
        let still_enabled = dir().map(|d| store::enabled_in(&d)).unwrap_or(false);
        set_status(false, result.err().filter(|_| still_enabled));
        if !still_enabled {
            continue;
        }
        if started.elapsed() > Duration::from_secs(60) {
            backoff = 2;
        }
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(backoff)) => {}
            _ = bridge().wake.notified() => {}
        }
        backoff = (backoff * 2).min(60);
    }
}

/// 启动时调一次 (lib.rs setup)
pub fn start(app: tauri::AppHandle) {
    if bridge().app.set(app).is_ok() {
        tauri::async_runtime::spawn(supervisor());
    }
}

// ── 给「设置 → 手机」的命令 ─────────────────────────────────

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MobileStatus {
    pub enabled: bool,
    pub connected: bool,
    pub error: Option<String>,
    pub max_phones: Option<u32>,
    pub relay_url: String,
    /// 手机网页地址 = 门户地址 + /m
    pub mobile_url: String,
    pub phones: Vec<store::Phone>,
    pub pending: Vec<PendingPair>,
}

#[tauri::command]
pub fn mobile_status() -> Result<MobileStatus, String> {
    let d = dir()?;
    let st = bridge().state.lock().unwrap();
    Ok(MobileStatus {
        enabled: store::enabled_in(&d),
        connected: st.connected,
        error: st.error.clone(),
        max_phones: st.max_phones,
        relay_url: relay_url(&crate::services::endpoints::endpoints().gateway_base()),
        mobile_url: format!("{}/m", crate::services::endpoints::endpoints().web_base().trim_end_matches('/')),
        phones: store::phones_in(&d),
        pending: st.pending.values().cloned().collect(),
    })
}

#[tauri::command]
pub fn mobile_set_enabled(enabled: bool) -> Result<(), String> {
    store::set_enabled_in(&dir()?, enabled)?;
    bridge().wake.notify_waiters();
    bridge().wake.notify_one();
    Ok(())
}

#[tauri::command]
pub fn mobile_pair_decide(device: String, allow: bool) -> Result<(), String> {
    let pending = bridge().state.lock().unwrap().pending.remove(&device).ok_or("这个配对请求已经过期了")?;
    if allow {
        store::add_phone_in(
            &dir()?,
            store::Phone {
                device: device.clone(),
                name: pending.name.clone(),
                public_key: pending.public_key.clone(),
                paired_at: chrono::Local::now().to_rfc3339(),
            },
        )?;
        bridge().state.lock().unwrap().last_accepted = Some((device.clone(), Instant::now()));
    }
    send_raw(json!({"t": "pair_result", "to": device, "ok": allow, "name": pending.name}));
    emit("mobile://changed", Value::Null);
    Ok(())
}

#[tauri::command]
pub fn mobile_revoke(device: String) -> Result<(), String> {
    store::remove_phone_in(&dir()?, &device)?;
    bridge().state.lock().unwrap().sessions.remove(&device);
    send_raw(json!({"t": "revoke", "device": device}));
    emit("mobile://changed", Value::Null);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 模拟一台手机, 走一遍: 配对 → 握手 → 加密请求 → 加密回复; 再看撤销 / 解不开时电脑怎么回。
    /// 手机这头用的是跟网页 (tweetnacl) 同格式的 crypto.rs 函数。
    #[test]
    fn 模拟手机_配对握手收发() {
        let tmp = tempfile::tempdir().unwrap();
        *TEST_DIR.lock().unwrap() = Some(tmp.path().to_path_buf());
        let (tx, mut rx) = mpsc::unbounded_channel::<String>();
        bridge().state.lock().unwrap().tx = Some(tx);
        macro_rules! next {
            () => {
                serde_json::from_str::<Value>(&rx.try_recv().expect("应该发了一帧")).unwrap()
            };
        }

        // 1. 配对: 手机发公钥 → 电脑回自己的公钥, 两边验证码一致; 员工点允许 → pair_result ok
        let phone_sk = crypto::new_secret();
        let phone_pk = crypto::public_of(&phone_sk);
        on_relay_frame(&json!({"t": "pair_request", "from": "phone-test-01", "name": "iPhone",
                               "pk": crypto::b64(&phone_pk)}).to_string());
        let offer = next!();
        assert_eq!(offer["t"], "pair_offer");
        let pc_pk = crypto::key32(offer["pk"].as_str().unwrap()).unwrap();
        let pending = mobile_status().unwrap().pending;
        assert_eq!(pending[0].code, crypto::sas(&phone_pk, &pc_pk));
        mobile_pair_decide("phone-test-01".into(), true).unwrap();
        assert_eq!(next!(), json!({"t": "pair_result", "to": "phone-test-01", "ok": true, "name": "iPhone"}));
        assert_eq!(store::phones_in(tmp.path()).len(), 1);

        // 2. 握手: hello (长期钥匙) → hello_ack 带电脑的临时公钥
        let long = crypto::make_box(&pc_pk, &phone_sk);
        let eph = crypto::new_secret();
        let hello = json!({"op": "hello", "eph": crypto::b64(&crypto::public_of(&eph)), "sid": "s1"});
        on_relay_frame(&json!({"t": "msg", "from": "phone-test-01",
                               "d": crypto::seal(&long, crypto::KIND_LONG, hello.to_string().as_bytes())}).to_string());
        let ack_frame = next!();
        let (kind, raw) = crypto::kind_of(ack_frame["d"].as_str().unwrap()).unwrap();
        assert_eq!(kind, crypto::KIND_LONG);
        let ack: Value = serde_json::from_slice(&crypto::open(&long, &raw).unwrap()).unwrap();
        assert_eq!(ack["op"], "hello_ack");
        assert_eq!(ack["sid"], "s1");
        let sess = crypto::make_box(&crypto::key32(ack["eph"].as_str().unwrap()).unwrap(), &eph);

        // 3. 加密请求 → 加密回复 (ping 不碰 Hermes)
        let mut seq = 0;
        let piece = crypto::split(json!({"op": "ping", "rid": "r1"}).to_string().as_bytes(), 1, &mut seq).remove(0);
        on_relay_frame(&json!({"t": "msg", "from": "phone-test-01",
                               "d": crypto::seal(&sess, crypto::KIND_SESSION, &piece)}).to_string());
        let reply_frame = tauri::async_runtime::block_on(async {
            for _ in 0..100 {
                if let Ok(t) = rx.try_recv() {
                    return t;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            panic!("没收到回复");
        });
        let reply_frame: Value = serde_json::from_str(&reply_frame).unwrap();
        let (_, raw) = crypto::kind_of(reply_frame["d"].as_str().unwrap()).unwrap();
        let mut r = crypto::Reassembler::default();
        let crypto::Piece::Done(body) = r.push(&crypto::open(&sess, &raw).unwrap()) else { panic!("应是整条") };
        assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(), json!({"op": "pong", "rid": "r1"}));

        // 4. 解不开的会话帧 → 请手机重新握手
        let wrong = crypto::make_box(&pc_pk, &crypto::new_secret());
        on_relay_frame(&json!({"t": "msg", "from": "phone-test-01",
                               "d": crypto::seal(&wrong, crypto::KIND_SESSION, &piece)}).to_string());
        let (_, raw) = crypto::kind_of(next!()["d"].as_str().unwrap()).unwrap();
        let m: Value = serde_json::from_slice(&crypto::open(&long, &raw).unwrap()).unwrap();
        assert_eq!(m["op"], "rehello");

        // 5. 撤销后再来消息: 电脑不认, 让中转站也删
        mobile_revoke("phone-test-01".into()).unwrap();
        assert_eq!(next!(), json!({"t": "revoke", "device": "phone-test-01"}));
        on_relay_frame(&json!({"t": "msg", "from": "phone-test-01",
                               "d": crypto::seal(&sess, crypto::KIND_SESSION, &piece)}).to_string());
        assert_eq!(next!(), json!({"t": "revoke", "device": "phone-test-01"}));

        // 6. 没配过的手机发的配对请求, 员工点拒绝 → 不记
        on_relay_frame(&json!({"t": "pair_request", "from": "phone-test-02", "name": "x",
                               "pk": crypto::b64(&phone_pk)}).to_string());
        let _offer = next!();
        mobile_pair_decide("phone-test-02".into(), false).unwrap();
        assert_eq!(next!()["ok"], false);
        assert!(store::phones_in(tmp.path()).is_empty());

        bridge().state.lock().unwrap().tx = None;
        *TEST_DIR.lock().unwrap() = None;
    }

    #[test]
    fn 中转站地址由中央地址推出() {
        assert_eq!(relay_url("http://127.0.0.1:8999"), "ws://127.0.0.1:8999/relay/ws");
        assert_eq!(relay_url("https://catfish.example.com:443/"), "wss://catfish.example.com:443/relay/ws");
    }
}
