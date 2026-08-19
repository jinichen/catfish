//! macOS: 存密码时**创建就带 ACL**, 让 tool-bridge 也读得到 (8/19 重写)。
//!
//! # 为什么不能用 keyring 写
//!
//! keyring 的 apple-native 走 `SecKeychainAddGenericPassword`, 最后一个参数
//! 传 `ptr::null_mut()` —— 没有任何 ACL 入口。Apple 对这个 API 的默认行为是:
//! 新条目只信任**创建它的那个程序**。
//!
//! 而教学时读密码的是 tool-bridge, 它 `subprocess.run(["security",
//! "find-generic-password", ...], timeout=5)` (secret_resolver.py)。
//! `/usr/bin/security` 是另一个二进制 → macOS 弹窗要确认 → 子进程没 UI 也没人
//! 应答 → 卡满 5 秒 → `keychain 查询超时`。
//!
//! # 8/18 走错的那条路 (留着当路标)
//!
//! 第一版是「keyring 照常写 → 再 `SecKeychainItemSetAccess` 改 ACL」。
//! **改已存在条目的 ACL 属于所有者权限, macOS 每次都要用户授权**:
//!
//! ```text
//! Catfish Companion 想要更改你钥匙串中 "catfish-teaching:neis.ffcs.cn (catfish)"
//! 项目的所有者 (该项目的所有者是允许更改访问许可的用户)。
//! ```
//!
//! 鸿波连输三次登录密码。等于把问题从「读的时候超时」搬成了「写的时候拦人」,
//! 更糟。而且事后看 ACL 里仍然只有 Companion —— 那一版还有第二个错:
//! `CFArray::from_copyable` 建的是 **null callbacks** 数组 (见 core-foundation
//! 0.9 array.rs:75 传的是 `ptr::null()`), 不是 SecAccessCreate 要的 CF 对象数组。
//!
//! # 现在这条路
//!
//! **创建时就带 ACL 不需要任何授权** —— 你是创建者, 初始 ACL 由你定义。
//! 所以改成: 先删掉同名旧条目, 再 `SecKeychainItemCreateFromContent` 带着
//! `initialAccess` 一次建好。全程不弹窗。
//!
//! 删自己的条目也不弹窗 (Companion 在旧条目的 ACL 里)。
//!
//! # 信任谁
//!
//! 只两个二进制:
//!
//!   · 本程序 (Companion) —— 写的那个
//!   · `/usr/bin/security` —— tool-bridge 读密码时 exec 的那个, Apple 签名
//!
//! **不用「允许所有应用程序」** (`security -A` 那个)。那等于对任何以该用户身份
//! 跑的进程开放, 只为让一个已知的读取方能读 —— 判据比真事宽。别的进程照旧要
//! 弹窗确认。
//!
//! # FFI 边界
//!
//! `security-framework-sys 2.17` 里**没有**的, 自己声明 (查过):
//!   · `SecTrustedApplicationCreateFromPath` / `SecAccessCreate`
//!   · `SecKeychainItemCreateFromContent`
//!
//! 有的直接用, 少一个手写签名就少一处 ABI 出错的机会:
//!   · `SecKeychainFindGenericPassword` / `SecKeychainItemDelete`
//!   · `SecKeychainItemFreeContent`
//!   · `SecKeychainAttribute` / `SecKeychainAttributeList` 结构体 (base.rs:20/29)
//!   · `CFArrayCreate` / `kCFTypeArrayCallBacks` (core-foundation-sys array.rs:44/48)
//!
//! ⚠ 手写的三个签名对不对**编译器验不了**。交叉核对过: sys 里同族的
//!   `SecKeychainItemModifyAttributesAndData` 是
//!   `(itemRef, *const SecKeychainAttributeList, u32 length, *const c_void data)`,
//!   跟 `CreateFromContent` 中段的 `(attrList, length, data)` 三件套完全一致,
//!   说明这一族的参数排布我没记错。

use core_foundation::base::{CFRelease, CFTypeRef, TCFType};
use core_foundation::string::{CFString, CFStringRef};
use core_foundation_sys::array::{kCFTypeArrayCallBacks, CFArrayCreate, CFArrayRef};
use core_foundation_sys::base::{kCFAllocatorDefault, CFIndex, OSStatus};
use libc::{c_char, c_void};
use security_framework_sys::base::{
    SecAccessRef, SecKeychainAttribute, SecKeychainAttributeList, SecKeychainItemRef,
};
use security_framework_sys::keychain::SecKeychainFindGenericPassword;
use security_framework_sys::keychain_item::{SecKeychainItemDelete, SecKeychainItemFreeContent};
use std::ptr;

/// `SecTrustedApplicationRef` / `SecKeychainRef` 在 sys 里没有(或不便取)。
/// 只在 API 之间传递, 不解引用 —— 不透明指针足够。
type SecTrustedApplicationRef = *mut c_void;
type SecKeychainRef = *mut c_void;
/// FourCharCode。
type SecItemClass = u32;

const ERR_SEC_SUCCESS: OSStatus = 0;
/// 找不到条目 —— 第一次存时是正常情况, 不是错误。
const ERR_SEC_ITEM_NOT_FOUND: OSStatus = -25300;

/// FourCharCode 用 `from_be_bytes` 写, 不手敲十六进制 —— 手敲容易错一位而且
/// 看不出来 ('genp' = 0x67656E70)。
const K_SEC_GENERIC_PASSWORD_ITEM_CLASS: SecItemClass = u32::from_be_bytes(*b"genp");
const K_SEC_SERVICE_ITEM_ATTR: u32 = u32::from_be_bytes(*b"svce");
const K_SEC_ACCOUNT_ITEM_ATTR: u32 = u32::from_be_bytes(*b"acct");

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
        trustedlist: CFArrayRef,
        access_ref: *mut SecAccessRef,
    ) -> OSStatus;

    /// ★ 关键: `initial_access` 让新条目**一出生就带对的 ACL**, 不用事后改,
    ///   所以不触发「更改所有者」那个授权弹窗。
    fn SecKeychainItemCreateFromContent(
        item_class: SecItemClass,
        attr_list: *const SecKeychainAttributeList,
        length: u32,
        data: *const c_void,
        keychain_ref: SecKeychainRef,
        initial_access: SecAccessRef,
        item_ref: *mut SecKeychainItemRef,
    ) -> OSStatus;
}

/// tool-bridge 读密码时 exec 的就是它 (secret_resolver.py `_resolve_keychain`)。
const READER_BINARY: &[u8] = b"/usr/bin/security\0";

/// 建一个「只信任 Companion + /usr/bin/security」的 access 对象。
fn build_access(descriptor: &str) -> Result<SecAccessRef, String> {
    let mut self_app: SecTrustedApplicationRef = ptr::null_mut();
    let st = unsafe { SecTrustedApplicationCreateFromPath(ptr::null(), &mut self_app) };
    if st != ERR_SEC_SUCCESS {
        return Err(format!("取当前程序的信任项失败 (OSStatus={st})"));
    }

    // ⚠ 显式标类型, 不用 `.cast()`。`.cast()` 会**跟着 extern 声明变** ——
    //   8/18 实测: 把声明里的 `*const c_char` 改成 `*const u32` 做变异,
    //   编译器一声不吭。等于把"签名写错"洗成合法代码, 而签名写错是 UB。
    let reader_ptr: *const c_char = READER_BINARY.as_ptr() as *const c_char;
    let mut reader_app: SecTrustedApplicationRef = ptr::null_mut();
    let st = unsafe { SecTrustedApplicationCreateFromPath(reader_ptr, &mut reader_app) };
    if st != ERR_SEC_SUCCESS {
        unsafe { CFRelease(self_app as CFTypeRef) };
        return Err(format!("取 /usr/bin/security 的信任项失败 (OSStatus={st})"));
    }

    // ⚠ 必须用 kCFTypeArrayCallBacks, 不能用 core-foundation 的
    //   `CFArray::from_copyable` —— 那个传的 callbacks 是 NULL (array.rs:75),
    //   建出来的是"裸值数组"而不是 CF 对象数组。8/18 那版就是这么写的, 结果
    //   SecAccessCreate 吃不到 trustedlist, ACL 里只剩 Companion 一个。
    let values: [*const c_void; 2] = [self_app as *const c_void, reader_app as *const c_void];
    let trusted: CFArrayRef = unsafe {
        CFArrayCreate(
            kCFAllocatorDefault,
            values.as_ptr(),
            values.len() as CFIndex,
            &kCFTypeArrayCallBacks,
        )
    };
    // kCFTypeArrayCallBacks 会 retain 两个元素, 我们那份所有权可以还掉了。
    unsafe {
        CFRelease(self_app as CFTypeRef);
        CFRelease(reader_app as CFTypeRef);
    }
    if trusted.is_null() {
        return Err("建信任列表失败 (CFArrayCreate 返 null)".to_string());
    }

    let desc = CFString::new(descriptor);
    let mut access: SecAccessRef = ptr::null_mut();
    let st = unsafe { SecAccessCreate(desc.as_concrete_TypeRef(), trusted, &mut access) };
    unsafe { CFRelease(trusted as CFTypeRef) };
    if st != ERR_SEC_SUCCESS || access.is_null() {
        return Err(format!("SecAccessCreate 失败 (OSStatus={st})"));
    }
    Ok(access)
}

/// 删掉 service+account 对应的旧条目。没有就当成功 —— 第一次存本来就没有。
fn delete_existing(service: &str, account: &str) -> Result<(), String> {
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
    // ⚠ 旧密码的明文我们一个字节都不看, 但这个 API 会 malloc 出来 —— 必须还
    //   回去, 否则明文留在进程堆里。
    if !pw_data.is_null() {
        unsafe { SecKeychainItemFreeContent(ptr::null_mut(), pw_data) };
    }
    if st == ERR_SEC_ITEM_NOT_FOUND {
        return Ok(()); // 第一次存, 正常
    }
    if st != ERR_SEC_SUCCESS || item.is_null() {
        return Err(format!("查旧条目失败 (OSStatus={st})"));
    }
    let st = unsafe { SecKeychainItemDelete(item) };
    unsafe { CFRelease(item as CFTypeRef) };
    if st != ERR_SEC_SUCCESS {
        return Err(format!("删旧条目失败 (OSStatus={st})"));
    }
    Ok(())
}

/// 存密码, 新条目**一出生就允许 tool-bridge 读**。
///
/// 幂等: 同名旧条目先删再建。
///
/// ⚠ 这个函数**取代** keyring 的 `set_password`, 不是补充 —— 两个都调的话
///   keyring 那次会先建一个 ACL 不对的条目, 我们再删掉重建, 白折腾一轮, 而且
///   中间那次可能触发弹窗。
pub(crate) fn save_password(service: &str, account: &str, password: &str) -> Result<(), String> {
    let access = build_access(&format!("{service} (catfish)"))?;

    if let Err(e) = delete_existing(service, account) {
        unsafe { CFRelease(access as CFTypeRef) };
        return Err(e);
    }

    // 属性表: service + account。keyring 的 macOS 后端就是拿这两个当主键
    // (macos.rs: Entry::new(service, account) → _name_ 存 service、_account_
    // 存 user), tool-bridge 那边 `security find-generic-password -s <service>`
    // 查的也是 service —— 三边对齐。
    let svc_ptr: *const c_char = service.as_ptr() as *const c_char;
    let acct_ptr: *const c_char = account.as_ptr() as *const c_char;
    let mut attrs: [SecKeychainAttribute; 2] = [
        SecKeychainAttribute {
            tag: K_SEC_SERVICE_ITEM_ATTR,
            length: service.len() as u32,
            data: svc_ptr as *mut c_void,
        },
        SecKeychainAttribute {
            tag: K_SEC_ACCOUNT_ITEM_ATTR,
            length: account.len() as u32,
            data: acct_ptr as *mut c_void,
        },
    ];
    let attr_list = SecKeychainAttributeList {
        count: attrs.len() as u32,
        attr: attrs.as_mut_ptr(),
    };

    let pw_ptr: *const c_void = password.as_ptr() as *const c_void;
    let mut item: SecKeychainItemRef = ptr::null_mut();
    let st = unsafe {
        SecKeychainItemCreateFromContent(
            K_SEC_GENERIC_PASSWORD_ITEM_CLASS,
            &attr_list,
            password.len() as u32,
            pw_ptr,
            ptr::null_mut(), // 默认钥匙串 (登录)
            access,
            &mut item,
        )
    };
    unsafe {
        if !item.is_null() {
            CFRelease(item as CFTypeRef);
        }
        CFRelease(access as CFTypeRef);
    }
    if st != ERR_SEC_SUCCESS {
        return Err(format!("写钥匙串失败 (OSStatus={st})"));
    }
    Ok(())
}
