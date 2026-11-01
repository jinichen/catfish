//! 手机通道的加密层 (docs/MOBILE-CLIENT-DESIGN.md §4) —— 跟手机网页 `central/web/src/mobile/crypto.ts` 一字不差地对应。
//!
//! 中转站看到的 `d` = base64( 类型 1 字节 | nonce 24 字节 | NaCl box 密文 )。
//!   类型 1 (长期密钥): 配对时双方交换的长期公钥加密, 只用来握手 (hello / hello_ack / rehello)。
//!   类型 2 (会话密钥): 每次手机连上都换一对临时密钥, 断开后旧消息解不开。
//!
//! 类型 2 的明文 = seq(u32) | mid(u32) | idx(u16) | cnt(u16) | 一段 UTF-8 JSON。
//! seq 每帧递增 (防重放), 一条消息超过 PIECE_BYTES 就拆成几段, 同一个 mid。
//! 中转站限 `d` 6000 字符 (跨进程 NOTIFY 的上限), PIECE_BYTES 留足余量。

use base64::Engine;
use crypto_box::aead::{Aead, AeadCore, OsRng};
use crypto_box::{PublicKey, SalsaBox, SecretKey};
use sha2::{Digest, Sha256};
use std::collections::HashMap;

pub const KIND_LONG: u8 = 1;
pub const KIND_SESSION: u8 = 2;
pub const PIECE_BYTES: usize = 4000;
const HEADER: usize = 12;

pub fn b64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

pub fn unb64(s: &str) -> Option<Vec<u8>> {
    base64::engine::general_purpose::STANDARD.decode(s).ok()
}

pub fn key32(s: &str) -> Option<[u8; 32]> {
    unb64(s)?.try_into().ok()
}

pub fn new_secret() -> SecretKey {
    SecretKey::generate(&mut OsRng)
}

pub fn public_of(sk: &SecretKey) -> [u8; 32] {
    *sk.public_key().as_bytes()
}

pub fn make_box(peer_pk: &[u8; 32], my_sk: &SecretKey) -> SalsaBox {
    SalsaBox::new(&PublicKey::from(*peer_pk), my_sk)
}

pub fn seal(b: &SalsaBox, kind: u8, plain: &[u8]) -> String {
    let nonce = SalsaBox::generate_nonce(&mut OsRng);
    let ct = b.encrypt(&nonce, plain).expect("NaCl box 加密不会失败");
    let mut out = Vec::with_capacity(1 + 24 + ct.len());
    out.push(kind);
    out.extend_from_slice(&nonce);
    out.extend_from_slice(&ct);
    b64(&out)
}

/// 解出 (类型, 原始字节); 类型在解密前就要知道 (决定用哪把钥匙), 所以分两步。
pub fn kind_of(d: &str) -> Option<(u8, Vec<u8>)> {
    let raw = unb64(d)?;
    if raw.len() < 1 + 24 + 16 {
        return None;
    }
    Some((raw[0], raw))
}

pub fn open(b: &SalsaBox, raw: &[u8]) -> Option<Vec<u8>> {
    let nonce = crypto_box::Nonce::from_slice(&raw[1..25]);
    b.decrypt(nonce, &raw[25..]).ok()
}

/// 配对验证码: 两边各自用「手机公钥 + 电脑公钥」算, 中转站换了任何一把钥匙两边就对不上。
/// 6 位 (比设计稿写的 4 位多两位: 被冒充时撞对的机会从万分之一降到百万分之一)。
pub fn sas(phone_pk: &[u8; 32], pc_pk: &[u8; 32]) -> String {
    let mut h = Sha256::new();
    h.update(b"catfish-pair-v1");
    h.update(phone_pk);
    h.update(pc_pk);
    let d = h.finalize();
    let n = u32::from_be_bytes([d[0], d[1], d[2], d[3]]) % 1_000_000;
    format!("{n:06}")
}

/// 一条消息 → 若干段会话明文 (每段带 seq)。
pub fn split(body: &[u8], mid: u32, seq: &mut u32) -> Vec<Vec<u8>> {
    let pieces: Vec<&[u8]> = if body.is_empty() { vec![&[][..]] } else { body.chunks(PIECE_BYTES).collect() };
    let cnt = pieces.len() as u16;
    pieces
        .into_iter()
        .enumerate()
        .map(|(i, p)| {
            *seq += 1;
            let mut out = Vec::with_capacity(HEADER + p.len());
            out.extend_from_slice(&seq.to_be_bytes());
            out.extend_from_slice(&mid.to_be_bytes());
            out.extend_from_slice(&(i as u16).to_be_bytes());
            out.extend_from_slice(&cnt.to_be_bytes());
            out.extend_from_slice(p);
            out
        })
        .collect()
}

/// 收端: 查 seq、拼段。
#[derive(Default)]
pub struct Reassembler {
    last_seq: u32,
    parts: HashMap<u32, Vec<Option<Vec<u8>>>>,
}

pub enum Piece {
    /// 拼齐了一条
    Done(Vec<u8>),
    /// 还差几段
    Partial,
    /// 重放 / 乱序 / 格式不对 —— 丢掉
    Reject,
}

impl Reassembler {
    pub fn push(&mut self, plain: &[u8]) -> Piece {
        if plain.len() < HEADER {
            return Piece::Reject;
        }
        let seq = u32::from_be_bytes(plain[0..4].try_into().unwrap());
        let mid = u32::from_be_bytes(plain[4..8].try_into().unwrap());
        let idx = u16::from_be_bytes(plain[8..10].try_into().unwrap()) as usize;
        let cnt = u16::from_be_bytes(plain[10..12].try_into().unwrap()) as usize;
        if seq <= self.last_seq || cnt == 0 || idx >= cnt || cnt > 256 {
            return Piece::Reject;
        }
        self.last_seq = seq;
        let body = plain[HEADER..].to_vec();
        if cnt == 1 {
            return Piece::Done(body);
        }
        // 只留最近几条没拼完的, 防对端发一堆残段把内存撑大
        if !self.parts.contains_key(&mid) && self.parts.len() >= 8 {
            self.parts.clear();
        }
        let slot = self.parts.entry(mid).or_insert_with(|| vec![None; cnt]);
        if slot.len() != cnt {
            self.parts.remove(&mid);
            return Piece::Reject;
        }
        slot[idx] = Some(body);
        if slot.iter().all(Option::is_some) {
            let all = self.parts.remove(&mid).unwrap();
            return Piece::Done(all.into_iter().flatten().flatten().collect());
        }
        Piece::Partial
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 加密解密来回() {
        let a = new_secret();
        let b = new_secret();
        let ab = make_box(&public_of(&b), &a);
        let ba = make_box(&public_of(&a), &b);
        let d = seal(&ab, KIND_SESSION, "你好".as_bytes());
        let (kind, raw) = kind_of(&d).unwrap();
        assert_eq!(kind, KIND_SESSION);
        assert_eq!(open(&ba, &raw).unwrap(), "你好".as_bytes());
        // 别人的钥匙解不开
        let c = new_secret();
        assert!(open(&make_box(&public_of(&a), &c), &raw).is_none());
    }

    /// 跟 tweetnacl (手机网页) 互通: 这一组是用 tweetnacl 1.0.3 `nacl.box` 生成的。
    #[test]
    fn 能解开tweetnacl加的密() {
        let sk_b = SecretKey::from([2u8; 32]);
        let sk_a = SecretKey::from([1u8; 32]);
        let bx = make_box(&public_of(&sk_a), &sk_b);
        let (_, raw) = kind_of(super::tests_vector::D).unwrap();
        assert_eq!(open(&bx, &raw).unwrap(), b"catfish");
        assert_eq!(b64(&public_of(&sk_a)), super::tests_vector::PK_A);
    }

    #[test]
    fn 验证码两边一致_换钥匙就变() {
        let p = [7u8; 32];
        let c = [9u8; 32];
        assert_eq!(sas(&p, &c), sas(&p, &c));
        assert_eq!(sas(&p, &c).len(), 6);
        assert_ne!(sas(&p, &c), sas(&c, &p));
        assert_eq!(sas(&p, &c), super::tests_vector::SAS_7_9);
    }

    #[test]
    fn 大消息分段再拼回() {
        let body: Vec<u8> = "鲶".repeat(3000).into_bytes(); // 9000 字节
        let mut seq = 0;
        let parts = split(&body, 5, &mut seq);
        assert_eq!(parts.len(), 3);
        assert_eq!(seq, 3);
        let mut r = Reassembler::default();
        assert!(matches!(r.push(&parts[0]), Piece::Partial));
        assert!(matches!(r.push(&parts[1]), Piece::Partial));
        match r.push(&parts[2]) {
            Piece::Done(b) => assert_eq!(b, body),
            _ => panic!("应拼齐"),
        }
    }

    #[test]
    fn 重放的段丢掉() {
        let mut seq = 0;
        let p = split(b"{}", 1, &mut seq);
        let mut r = Reassembler::default();
        assert!(matches!(r.push(&p[0]), Piece::Done(_)));
        assert!(matches!(r.push(&p[0]), Piece::Reject));
    }
}

#[cfg(test)]
mod tests_vector {
    // 由 central/web/src/mobile/crypto.test.ts 的同一组输入生成; 两边测试互相钉住格式
    pub const PK_A: &str = "pOCSkrZRwni5dyxWn1+puxPZBrRqtoyd+dwrRAn4ogk=";
    pub const D: &str = "AgMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA2d1x9clQSLyZsnX8rBUkKiASgtpQmVS";
    pub const SAS_7_9: &str = "025159";
}
