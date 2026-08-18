//! macOS 钥匙串条目的访问控制 —— 让 tool-bridge 也读得到 (8/18)。
//!
//! # 病历
//!
//! keyring 的 apple-native 后端走 `SecKeychainAddGenericPassword`, 最后一个
//! 参数是 `ptr::null_mut()` —— **没有任何 ACL 参数**。Apple 对这个 API 的默认
//! 行为是: 新条目的访问控制只信任**创建它的那个程序**。
//!
//! 钥匙串访问里看到的就是这样 (8/18 鸿波截图实证, 不是推断):
//!
//! ```text
//! ○ 允许所有应用程序访问此项目
//! ● 允许访问之前确认
//!   始终允许通过这些应用程序访问:
//!     • catfish-companion-app          ← 只有它
//! ```
//!
//! 而教学时读密码的是 tool-bridge, 它 `subprocess.run(["security",
//! "find-generic-password", ...], timeout=5)` (secret_resolver.py)。
//! `/usr/bin/security` 是另一个二进制 → macOS 要弹窗确认 → 那个子进程没有 UI
//! 也没人应答 → 卡满 5 秒:
//!
//! ```text
//! keychain 查询超时 (security 命令卡住)
//! ```
//!
//! # 为什么不用「允许所有应用程序」
//!
//! `security add-generic-password -A` 就是那个意思, 也确实能解决。但它等于对
//! **任何**以该用户身份跑的进程开放, 只为了让一个已知的读取方能读 —— 判据比
//! 真事宽。
//!
//! 这里显式信任**两个**二进制:
//!
//!   · 本程序 (Companion) —— 写的那个
//!   · `/usr/bin/security` —— tool-bridge 读密码时 exec 的那个, Apple 签名的
//!     系统二进制
//!
//! 别的进程照旧要弹窗确认。
//!
//! # 为什么要自己写 FFI
//!
//! `security-framework-sys 2.17` 里这三个**没有绑定** (查过):
//! `SecAccessCreate` / `SecTrustedApplicationCreateFromPath` /
//! `SecKeychainItemSetAccess`。keyring 也没有任何 access / trusted 的口子。
//!
//! 剩下两个 (`SecKeychainFindGenericPassword` / `SecKeychainItemFreeContent`)
//! sys 里有, 直接用它的 —— 少两个手写签名就少两处 ABI 出错的机会。
//!
//! ⚠ 这三个手写签名对不对, **编译器验不了**。做过的交叉核对: 同族的
//!   `SecKeychainAddGenericPassword` / `FindGenericPassword` 在 sys 里的声明,
//!   跟我按 Apple 头文件写的逐字段一致 (u32 长度 + `*const c_char` + 末尾
//!   `*mut ...Ref` 出参 + 返 OSStatus), 说明这一族的写法我没记错。
//!
//! # 失败了会怎样
//!
//! 返回 Err, 但**密码已经存进去了** —— 调用方只记一条 warning, 不把整个保存
//! 判成失败。最坏退化成改之前的样子 (读的时候弹窗), 而不是密码没存上。

use core_foundation::array::CFArray;
use core_foundation::base::{CFRelease, CFTypeRef, TCFType};
use core_foundation::string::{CFString, CFStringRef};
use libc::{c_char, c_void};
// OSStatus 从 core-foundation-sys 拿 —— security-framework-sys 里那个是私有
// re-export (E0603), 别绕过它自己 alias 一个 i32。
use core_foundation::base::OSStatus;
use security_framework_sys::base::{SecAccessRef, SecKeychainItemRef};
use security_framework_sys::keychain::SecKeychainFindGenericPassword;
use security_framework_sys::keychain_item::SecKeychainItemFreeContent;
use std::ptr;

/// `SecTrustedApplicationRef` 在 sys 里也没定义。只在两个 API 之间传递,
/// 不解引用 —— 不透明指针足够。
type SecTrustedApplicationRef = *mut c_void;

const ERR_SEC_SUCCESS: OSStatus = 0;

#[link(name = "Security", kind = "framework")]
extern "C" {
    /// path 传 NULL = 当前程序自己。
    fn SecTrustedApplicationCreateFromPath(
        path: *const c_char,
        app: *mut SecTrustedApplicationRef,
    ) -> OSStatus;

    /// trustedlist 里的程序访问时不弹窗; 其余的照旧要确认。
    fn SecAccessCreate(
        descriptor: CFStringRef,
        trustedlist: CFTypeRef,
        access_ref: *mut SecAccessRef,
    ) -> OSStatus;

    fn SecKeychainItemSetAccess(item_ref: SecKeychainItemRef, access: SecAccessRef) -> OSStatus;
}

/// tool-bridge 读密码时 exec 的就是它 (secret_resolver.py `_resolve_keychain`)。
const READER_BINARY: &[u8] = b"/usr/bin/security\0";

/// 把 `service` + `account` 那条钥匙串条目的 ACL 改成「本程序 + /usr/bin/security」。
///
/// 幂等 —— 每次保存后重设一遍即可, 不用先查当前是什么。
pub(crate) fn allow_tool_bridge_to_read(service: &str, account: &str) -> Result<(), String> {
    // ── 1. 两个受信程序 ────────────────────────────────────────────
    let mut self_app: SecTrustedApplicationRef = ptr::null_mut();
    let st = unsafe { SecTrustedApplicationCreateFromPath(ptr::null(), &mut self_app) };
    if st != ERR_SEC_SUCCESS {
        return Err(format!("取当前程序的信任项失败 (OSStatus={st})"));
    }

    // ⚠ 显式标类型, 不用 `.cast()`。
    //   `.cast()` 会**跟着 extern 声明变** —— 我把声明里的 `*const c_char` 改成
    //   `*const u32` 做变异测试时, 编译器一声不吭 (8/18 实测)。等于把"签名写错"
    //   洗成了合法代码, 而 FFI 签名写错是 UB。标了类型, 声明一改就编译不过。
    let reader_ptr: *const c_char = READER_BINARY.as_ptr() as *const c_char;
    let mut reader_app: SecTrustedApplicationRef = ptr::null_mut();
    let st = unsafe { SecTrustedApplicationCreateFromPath(reader_ptr, &mut reader_app) };
    if st != ERR_SEC_SUCCESS {
        unsafe { CFRelease(self_app as CFTypeRef) };
        return Err(format!("取 /usr/bin/security 的信任项失败 (OSStatus={st})"));
    }

    // ── 2. 建 access 对象 ─────────────────────────────────────────
    // CFArray 会 retain 这两个元素, 建完就能把我们自己那份所有权还掉。
    let trusted = CFArray::from_copyable(&[
        self_app as *const c_void,
        reader_app as *const c_void,
    ]);
    let desc = CFString::new(&format!("{service} (catfish)"));
    let mut access: SecAccessRef = ptr::null_mut();
    let st = unsafe {
        SecAccessCreate(
            desc.as_concrete_TypeRef(),
            trusted.as_CFTypeRef(),
            &mut access,
        )
    };
    unsafe {
        CFRelease(self_app as CFTypeRef);
        CFRelease(reader_app as CFTypeRef);
    }
    if st != ERR_SEC_SUCCESS {
        return Err(format!("SecAccessCreate 失败 (OSStatus={st})"));
    }

    // ── 3. 找到刚写进去的那条 ──────────────────────────────────────
    // keyring 写完不返 itemRef (它给 SecKeychainAddGenericPassword 传的是
    // null), 所以得按 service + account 再找一次。
    let svc_ptr: *const c_char = service.as_ptr() as *const c_char;
    let acct_ptr: *const c_char = account.as_ptr() as *const c_char;
    let mut item: SecKeychainItemRef = ptr::null_mut();
    let mut pw_len: u32 = 0;
    let mut pw_data: *mut c_void = ptr::null_mut();
    let st = unsafe {
        SecKeychainFindGenericPassword(
            ptr::null(),
            service.len() as u32,
            svc_ptr,
            account.len() as u32,
            acct_ptr,
            &mut pw_len,
            &mut pw_data,
            &mut item,
        )
    };
    // ⚠ 密码内容我们一个字节都不看, 但这个 API 会把它 malloc 出来 —— 必须还
    //   回去, 否则明文密码留在进程堆里。
    if !pw_data.is_null() {
        unsafe { SecKeychainItemFreeContent(ptr::null_mut(), pw_data) };
    }
    if st != ERR_SEC_SUCCESS || item.is_null() {
        unsafe { CFRelease(access as CFTypeRef) };
        return Err(format!("找不到刚存的条目 (OSStatus={st})"));
    }

    // ── 4. 换 ACL ────────────────────────────────────────────────
    let st = unsafe { SecKeychainItemSetAccess(item, access) };
    unsafe {
        CFRelease(item as CFTypeRef);
        CFRelease(access as CFTypeRef);
    }
    if st != ERR_SEC_SUCCESS {
        return Err(format!("SecKeychainItemSetAccess 失败 (OSStatus={st})"));
    }
    Ok(())
}
