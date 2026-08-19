//! 教学凭据的**取值**通道 —— tool-bridge 问, Companion 答 (8/19)。
//!
//! 这个文件只管"接线": 监听、收一行、把响应写回去。判据全在 socket_proto.rs ——
//! 那边没有 unix / keyring 依赖, 在没有 GTK 的 CI 沙箱里也跑得了。
//!
//! # 为什么密码要经过这里
//!
//! macOS 钥匙串的授权是按二进制记的: 条目自带一份"哪几个程序可以读我"的名单。
//! Companion 建条目时名单里只有 Companion 自己。
//!
//! 而教学时读密码的是 tool-bridge —— hermes venv 里的另一个 python 进程, 它 exec
//! `/usr/bin/security` 去读。不在名单里, 系统弹确认框。tool-bridge 是 launchd 起
//! 的后台进程, **那个框员工看不见**, 5 秒后超时, 教学卡在"填密码"这一步。
//!
//! 8/18 试过两条"让 tool-bridge 也读得到"的路, 都被否掉:
//!
//!   · c010210 存完之后改 ACL 把 `/usr/bin/security` 加进名单
//!     → 改**已存在**条目的 ACL, macOS 每次都要员工输开机密码 (鸿波连输三次)。
//!       把"读的时候卡住"搬成了"存的时候拦人"。
//!   · f4bdb4f 创建时就把 `/usr/bin/security` 写进名单
//!     → 不弹框了, 但 `security` 是谁都能 exec 的通用工具。把它放进信任名单跟
//!       `add-generic-password -A`(对所有程序开放) 没有实质区别 —— 任何进程 exec
//!       一下就把密码拿走, 而且悄无声息。那道墙等于自己拆了。
//!
//! 治本: **不让第二个二进制去读**。名单保持"只有 Companion", 别人要就走这条
//! socket 问。挡住别人的是 macOS 本身, 不是我们写的某个 if。
//!
//! # 鉴权只有两道, 而且都别指望它们挡黑客
//!
//! 1. socket 文件 0600 + `~/.catfish` 目录本身 —— 挡的是**别的用户**。
//! 2. service 名必须以 `catfish-teaching:` 开头 (socket_proto.rs) —— 挡的是
//!    **我们自己**: 把这条通道的能力钉死在"教学凭据"这一类上, 免得将来哪个调用点
//!    顺手拿它去读钥匙串里的 SSO token / API key。最小权限, 防的是设计漂移。
//!
//! 曾经想加第三道"校验对端进程是不是 tool-bridge"(LOCAL_PEERPID + proc_pidpath),
//! 鸿波 8/19 问「在本机上为什么要做 peer 进程校验」, 想清楚了: 同一个用户下能跑
//! 代码的进程可以 ptrace Companion 直接读内存, 也可以先 exec 那个 python 再连过来
//! 冒充 —— 这道检查**挡不住任何有决心的东西**, 代价却是两个 FFI。安全剧场, 不做。
//!
//! 真正的边界在钥匙串 ACL 上, 不在这条 socket 上。这条通道的实质是"Companion 把
//! 自己的读取权限借出去一次" —— 同用户进程本来就有别的办法拿到 Companion 能拿的
//! 东西, 所以它没有实质放大攻击面。
//!
//! # 顺带修好的一件事
//!
//! 万一还是弹了授权框 (换了签名 / dev build 重编之后第一次读会弹), 现在弹在
//! **Companion 这个前台 GUI 上**, 员工看得见、点得着, 点一次「始终允许」就完了。
//! 以前弹在一个看不见的后台进程上, 只能干等 5 秒超时。

use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::time::Duration;

use super::socket_proto;

/// 请求行上限。请求里只有一个 service 名, 正常不到 100 字节。
const MAX_REQUEST_BYTES: u64 = 8 * 1024;

/// 单条连接的读超时。防一个不说话的客户端把线程占住。
const READ_TIMEOUT: Duration = Duration::from_secs(10);

fn socket_path() -> Option<PathBuf> {
    crate::services::catfish_paths::companion_secrets_socket()
}

/// 起后台线程监听。启动失败只记日志, **不阻塞 Companion 启动** —— 起不来的后果
/// 是"教学时取不到密码"(而且 tool-bridge 那边会明说连不上 Companion), 不该顺带
/// 让整个 app 起不来。
///
/// ⚠ 用 std::thread 不是 tokio::spawn: setup hook 不在 tokio runtime context,
///   tokio::spawn 当场 panic (lib.rs:551 有同款注释)。这里也不需要 async ——
///   一个连接一次 RPC, 阻塞式最简单。
pub fn spawn() {
    std::thread::spawn(|| match serve() {
        Ok(()) => log::warn!("[secret-socket] 监听循环退出了 (不该发生)"),
        Err(e) => log::error!(
            "[secret-socket] 起不来: {e} —— 教学时「按站点填密码」会报连不上 Companion"
        ),
    });
}

fn serve() -> Result<(), String> {
    let path = socket_path().ok_or_else(|| "找不到 home 目录".to_string())?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("建目录 {} 失败: {e}", dir.display()))?;
    }
    // 上次崩溃可能留下死文件 —— bind 到已存在的路径会 EADDRINUSE。
    // 跟 tool-bridge server.py:504 同样的处理。
    if path.exists() {
        let _ = std::fs::remove_file(&path);
    }

    let listener =
        UnixListener::bind(&path).map_err(|e| format!("bind {} 失败: {e}", path.display()))?;

    // 0600。
    //
    // ⚠ 诚实说明: bind 到 chmod 之间有一小段窗口, 期间文件模式由 umask 决定。
    //   真正兜底的是父目录 `~/.catfish` 的权限 —— 别的用户连目录都进不去, 跟这个
    //   窗口无关。这一行是第二层, 不是唯一那层。
    if let Err(e) = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)) {
        log::warn!("[secret-socket] chmod 0600 失败 ({e}) —— 靠 ~/.catfish 目录权限兜底");
    }
    log::info!("[secret-socket] listening on {}", path.display());

    for stream in listener.incoming() {
        match stream {
            // 一连接一线程: 读钥匙串可能卡在授权框上等人点, 串行处理会把后面的
            // 请求一起堵住。连接是短的 (一次 RPC 就关), 不会堆积。
            Ok(s) => {
                std::thread::spawn(move || handle_one(s));
            }
            Err(e) => log::warn!("[secret-socket] accept 失败: {e}"),
        }
    }
    Ok(())
}

/// 一条连接 = 一次 RPC, 答完就关。跟 tool_bridge_rpc.rs 的短连约定一致。
fn handle_one(mut stream: UnixStream) {
    let _ = stream.set_read_timeout(Some(READ_TIMEOUT));

    let reader = match stream.try_clone() {
        Ok(s) => BufReader::new(s),
        Err(e) => {
            log::warn!("[secret-socket] try_clone 失败: {e}");
            return;
        }
    };
    let mut line = String::new();
    // take: 不让一个不发换行的客户端把内存撑爆
    if let Err(e) = reader.take(MAX_REQUEST_BYTES).read_line(&mut line) {
        log::warn!("[secret-socket] 读请求失败: {e}");
        return;
    }

    let resp = socket_proto::respond(line.trim(), |service| {
        // service 名是 `catfish-teaching:<站点>`, 不含密码 —— 可以进日志。
        // 有这一行才查得出"教学到底问没问过密码"。
        log::info!("[secret-socket] 取密码: {service}");
        super::read_password(service)
    });

    let mut out = serde_json::to_string(&resp).unwrap_or_else(|_| {
        r#"{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"序列化失败"}}"#.to_string()
    });
    out.push('\n');
    if let Err(e) = stream.write_all(out.as_bytes()) {
        log::warn!("[secret-socket] 写响应失败: {e}");
    }
    let _ = stream.flush();
}
