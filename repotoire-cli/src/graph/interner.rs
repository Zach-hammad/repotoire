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

use lasso::{Spur, ThreadedRodeo};
use std::sync::LazyLock;

/// A string key - small (4 bytes) reference to an interned string
pub type StrKey = Spur;

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

/// Thread-safe string interner for concurrent graph building
#[derive(Debug)]
pub struct StringInterner {
    inner: ThreadedRodeo,
}

impl Default for StringInterner {
    fn default() -> Self {
        Self::new()
    }
}

impl StringInterner {
    /// Create a new interner
    pub fn new() -> Self {
        Self {
            inner: ThreadedRodeo::default(),
        }
    }

    /// Create with estimated capacity
    pub fn with_capacity(strings: usize, _bytes: usize) -> Self {
        Self {
            inner: ThreadedRodeo::with_capacity(lasso::Capacity::for_strings(strings)),
        }
    }

    /// Intern a string, returning a key
    /// If the string was already interned, returns the existing key
    #[inline]
    pub fn intern(&self, s: &str) -> StrKey {
        self.inner.get_or_intern(s)
    }

    /// Get the string for a key
    #[inline]
    pub fn resolve(&self, key: StrKey) -> &str {
        self.inner.resolve(&key)
    }

    /// Try to get a key for an already-interned string
    #[inline]
    pub fn get(&self, s: &str) -> Option<StrKey> {
        self.inner.get(s)
    }

    /// Number of unique strings interned
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    /// Check if empty
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }

    /// Estimated memory usage in bytes
    pub fn memory_usage(&self) -> usize {
        // Rough estimate: average string length × count + overhead
        // lasso stores strings contiguously which is very efficient
        self.inner.len() * 50 // Assume 50 bytes average per string
    }

    /// Get the StrKey for the empty string "".
    /// Used as a sentinel for "no value" in CodeNode's optional StrKey fields.
    pub fn empty_key(&self) -> StrKey {
        self.intern("")
    }
}

/// A read-only interner for when building is complete
/// Uses the RodeoReader which is more memory efficient
#[derive(Debug)]
#[allow(dead_code)] // Infrastructure for future freeze-after-build optimization
pub struct ReadOnlyInterner {
    inner: lasso::RodeoReader,
}

#[allow(dead_code)] // Infrastructure for future freeze-after-build optimization
impl ReadOnlyInterner {
    /// Convert from a mutable interner (freezes it)
    pub fn from_interner(interner: StringInterner) -> Self {
        Self {
            inner: interner.inner.into_reader(),
        }
    }

    /// Resolve a key to its string
    #[inline]
    pub fn resolve(&self, key: StrKey) -> &str {
        self.inner.resolve(&key)
    }

    /// Try to get a key for a string
    #[inline]
    pub fn get(&self, s: &str) -> Option<StrKey> {
        self.inner.get(s)
    }

    /// Number of strings
    pub fn len(&self) -> usize {
        self.inner.len()
    }

    /// Check if empty
    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
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
}
