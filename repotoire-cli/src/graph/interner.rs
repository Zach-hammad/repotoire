//! String interning for memory-efficient graph storage
//!
//! Instead of storing duplicate strings (e.g., file paths repeated for every function),
//! we store each unique string once and reference it by a small key.
//!
//! # Memory Savings
//!
//! For a repo with 75k files and 250k functions:
//! - Without interning: 250k × 100 bytes = 25MB just for file paths
//! - With interning: 75k × 100 bytes + 250k × 4 bytes = 8.5MB (66% savings)
//!
//! The savings compound for qualified_name, name, etc.
//!
//! # Implementation
//!
//! Chunk-based arena string interner. Strings are stored in append-only `String`
//! chunks that are never reallocated or removed. This makes it safe to hand out
//! `&str` references tied to the `StringInterner` lifetime even though the actual
//! data lives behind a `RwLock` — the backing memory is stable.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::hash::{DefaultHasher, Hash, Hasher};
use std::sync::{LazyLock, RwLock};

/// A string key — small (4 bytes) reference to an interned string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[repr(transparent)]
pub struct StrKey(u32);

impl StrKey {
    /// Return the raw `u32` index.
    pub fn as_u32(self) -> u32 {
        self.0
    }
}

/// Global interner singleton — used by CodeNode convenience builders
/// (CodeNode::function(), CodeNode::file(), CodeNode::class(), etc.)
/// so that nodes can be constructed without needing a reference to a
/// specific GraphBuilder.
///
/// In production, GraphBuilder also uses this same interner so all StrKeys
/// are compatible. Tests that create a standalone GraphBuilder::new().freeze()
/// share this interner too since it's a global singleton.
static GLOBAL_INTERNER: LazyLock<StringInterner> = LazyLock::new(StringInterner::new);

/// Access the global string interner.
pub fn global_interner() -> &'static StringInterner {
    &GLOBAL_INTERNER
}

// ---------------------------------------------------------------------------
// Inner arena
// ---------------------------------------------------------------------------

/// Initial chunk capacity (64 KiB). Doubles on growth.
const INITIAL_CHUNK_CAPACITY: usize = 64 * 1024;

struct InternerInner {
    /// Append-only list of string chunks. Each chunk is a `String` that we
    /// only ever push bytes into; we never remove or reallocate it once it is
    /// added to the vec.
    chunks: Vec<String>,
    /// Per-key metadata: (chunk_index, byte_start, byte_len).
    /// Indexed by `StrKey.0`.
    spans: Vec<(u16, u32, u32)>,
    /// Hash → candidate StrKeys for dedup (open-addressed with chaining).
    map: HashMap<u64, Vec<StrKey>>,
    /// Capacity for the *next* chunk we allocate.
    next_chunk_capacity: usize,
}

impl InternerInner {
    fn new() -> Self {
        let mut inner = Self {
            chunks: Vec::new(),
            spans: Vec::new(),
            map: HashMap::new(),
            next_chunk_capacity: INITIAL_CHUNK_CAPACITY,
        };
        // Pre-allocate the first chunk.
        inner.chunks.push(String::with_capacity(INITIAL_CHUNK_CAPACITY));
        inner
    }

    fn with_capacity(strings: usize, bytes: usize) -> Self {
        let cap = bytes.max(INITIAL_CHUNK_CAPACITY);
        let mut inner = Self {
            chunks: Vec::new(),
            spans: Vec::with_capacity(strings),
            map: HashMap::with_capacity(strings),
            next_chunk_capacity: cap.checked_next_power_of_two().unwrap_or(cap),
        };
        inner.chunks.push(String::with_capacity(cap));
        inner
    }

    /// Resolve a key to its `&str`. Caller must guarantee the key is valid.
    #[inline]
    fn resolve(&self, key: StrKey) -> &str {
        let (chunk_idx, start, len) = self.spans[key.0 as usize];
        let chunk = &self.chunks[chunk_idx as usize];
        &chunk[start as usize..(start + len) as usize]
    }

    /// Hash a string with `DefaultHasher` (SipHash-1-3).
    #[inline]
    fn hash_str(s: &str) -> u64 {
        let mut h = DefaultHasher::new();
        s.hash(&mut h);
        h.finish()
    }

    /// Look up an already-interned string.
    fn get(&self, s: &str) -> Option<StrKey> {
        let hash = Self::hash_str(s);
        let bucket = self.map.get(&hash)?;
        bucket.iter().copied().find(|&key| self.resolve(key) == s)
    }

    /// Intern a string, deduplicating if already present.
    fn intern(&mut self, s: &str) -> StrKey {
        let hash = Self::hash_str(s);

        // Fast path: check existing.
        if let Some(bucket) = self.map.get(&hash) {
            for &key in bucket {
                if self.resolve(key) == s {
                    return key;
                }
            }
        }

        // Need to insert. Find or create a chunk with enough room.
        let last_idx = self.chunks.len() - 1;
        let last = &self.chunks[last_idx];
        let chunk_idx = if last.len() + s.len() <= last.capacity() {
            last_idx
        } else {
            // Allocate a new chunk. Capacity doubles but is at least big enough
            // for this string.
            let cap = self.next_chunk_capacity.max(s.len());
            self.next_chunk_capacity = cap.saturating_mul(2);
            self.chunks.push(String::with_capacity(cap));
            self.chunks.len() - 1
        };

        let start = self.chunks[chunk_idx].len() as u32;
        self.chunks[chunk_idx].push_str(s);
        let len = s.len() as u32;

        let key = StrKey(self.spans.len() as u32);
        self.spans.push((chunk_idx as u16, start, len));
        self.map.entry(hash).or_default().push(key);
        key
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Thread-safe string interner for concurrent graph building.
///
/// Internally uses a chunk-based arena behind a `RwLock`. Strings are stored
/// in append-only chunks that are never reallocated, so `&str` references
/// remain valid for the lifetime of the `StringInterner`.
pub struct StringInterner {
    inner: RwLock<InternerInner>,
}

// Manual Debug impl — we don't want to lock just for debug printing.
impl std::fmt::Debug for StringInterner {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StringInterner").finish_non_exhaustive()
    }
}

impl Default for StringInterner {
    fn default() -> Self {
        Self::new()
    }
}

impl StringInterner {
    /// Create a new interner.
    pub fn new() -> Self {
        Self {
            inner: RwLock::new(InternerInner::new()),
        }
    }

    /// Create with estimated capacity.
    pub fn with_capacity(strings: usize, bytes: usize) -> Self {
        Self {
            inner: RwLock::new(InternerInner::with_capacity(strings, bytes)),
        }
    }

    /// Intern a string, returning a key.
    /// If the string was already interned, returns the existing key.
    #[inline]
    pub fn intern(&self, s: &str) -> StrKey {
        // Fast path: read lock check.
        {
            let guard = self.inner.read().expect("interner lock poisoned");
            if let Some(key) = guard.get(s) {
                return key;
            }
        }
        // Slow path: write lock.
        let mut guard = self.inner.write().expect("interner lock poisoned");
        guard.intern(s)
    }

    /// Get the string for a key.
    ///
    /// # Safety rationale
    ///
    /// The returned `&str` borrows from `&self`, not from the lock guard.
    /// This is sound because:
    /// 1. Chunks are stored in a `Vec<String>` — we only push new chunks,
    ///    never remove or reallocate existing ones.
    /// 2. Strings within a chunk are never moved (we create a new chunk
    ///    instead of growing an existing one past its capacity).
    /// 3. The `&str` cannot outlive the `StringInterner` itself.
    #[inline]
    pub fn resolve(&self, key: StrKey) -> &str {
        let guard = self.inner.read().expect("interner lock poisoned");
        let s: &str = guard.resolve(key);
        // SAFETY: The backing `String` chunks are append-only and never
        // reallocated or removed. The pointer remains valid as long as
        // `self` (and therefore its inner `Vec<String>`) is alive.
        unsafe { std::mem::transmute::<&str, &str>(s) }
    }

    /// Try to get a key for an already-interned string.
    #[inline]
    pub fn get(&self, s: &str) -> Option<StrKey> {
        let guard = self.inner.read().expect("interner lock poisoned");
        guard.get(s)
    }

    /// Number of unique strings interned.
    pub fn len(&self) -> usize {
        self.inner.read().expect("interner lock poisoned").spans.len()
    }

    /// Check if empty.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Estimated memory usage in bytes.
    pub fn memory_usage(&self) -> usize {
        let guard = self.inner.read().expect("interner lock poisoned");
        let chunk_bytes: usize = guard.chunks.iter().map(|c| c.capacity()).sum();
        let span_bytes = guard.spans.capacity() * std::mem::size_of::<(u16, u32, u32)>();
        let map_overhead = guard.map.capacity() * std::mem::size_of::<(u64, Vec<StrKey>)>();
        chunk_bytes + span_bytes + map_overhead
    }

    /// Get the StrKey for the empty string "".
    /// Used as a sentinel for "no value" in CodeNode's optional StrKey fields.
    pub fn empty_key(&self) -> StrKey {
        self.intern("")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_interner_deduplication() {
        let interner = StringInterner::new();

        let k1 = interner.intern("src/main.rs");
        let k2 = interner.intern("src/main.rs");
        let k3 = interner.intern("src/lib.rs");

        // Same string should give same key
        assert_eq!(k1, k2);
        // Different strings should give different keys
        assert_ne!(k1, k3);
        // Should have 2 unique strings
        assert_eq!(interner.len(), 2);
    }

    #[test]
    fn test_empty_key() {
        let interner = StringInterner::new();
        let ek = interner.empty_key();
        assert_eq!(interner.resolve(ek), "");
        // Calling again should return the same key
        assert_eq!(interner.empty_key(), ek);
    }

    #[test]
    fn test_interner_thread_safety() {
        let interner = StringInterner::new();
        let key = interner.intern("hello");
        std::thread::scope(|s| {
            for _ in 0..4 {
                s.spawn(|| {
                    let k = interner.intern("hello");
                    assert_eq!(k, key);
                    assert_eq!(interner.resolve(k), "hello");
                });
            }
        });
    }

    #[test]
    fn test_interner_many_strings() {
        let interner = StringInterner::new();
        let mut keys = Vec::new();
        for i in 0..10_000 {
            keys.push(interner.intern(&format!("string_{i}")));
        }
        assert_eq!(interner.len(), 10_000);
        for (i, key) in keys.iter().enumerate() {
            assert_eq!(interner.resolve(*key), format!("string_{i}"));
        }
    }

    #[test]
    fn test_interner_resolve_after_growth() {
        let interner = StringInterner::new();
        let first_key = interner.intern("first");
        for i in 0..10_000 {
            interner.intern(&format!("padding_{i}"));
        }
        assert_eq!(interner.resolve(first_key), "first");
    }
}
