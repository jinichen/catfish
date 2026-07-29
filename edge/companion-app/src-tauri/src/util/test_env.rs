//! Test-only process-wide guard for tests that temporarily mutate HOME or
//! other process environment variables.
//!
//! The module was declared by `util/mod.rs` in the supplied source archive but
//! the file itself was missing, which prevented every `cargo test --lib` run
//! from compiling. Keeping one shared lock also gives future migrated tests a
//! single synchronization point.

use std::sync::Mutex;

pub static ENV_LOCK: Mutex<()> = Mutex::new(());
