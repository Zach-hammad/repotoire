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

/// A string key - small (4 bytes) reference to an interned string
pub type StrKey = Spur;

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
}

/// A read-only interner for when building is complete
/// Uses the RodeoReader which is more memory efficient
#[derive(Debug)]
pub struct ReadOnlyInterner {
    inner: lasso::RodeoReader,
}

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

/// Compact node representation using interned strings
/// 
/// Size comparison:
/// - CodeNode with Strings: ~200 bytes minimum
/// - CompactNode with keys: ~40 bytes
#[derive(Debug, Clone, Copy)]
pub struct CompactNode {
    pub kind: CompactNodeKind,
    pub name: StrKey,
    pub qualified_name: StrKey,
    pub file_path: StrKey,
    pub line_start: u32,
    pub line_end: u32,
    pub flags: u32, // Packed: is_async(1), complexity(15), param_count(8), etc.
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum CompactNodeKind {
    File = 0,
    Function = 1,
    Class = 2,
    Module = 3,
}

impl CompactNode {
    /// Create a file node
    pub fn file(interner: &StringInterner, path: &str) -> Self {
        let key = interner.intern(path);
        Self {
            kind: CompactNodeKind::File,
            name: key,
            qualified_name: key,
            file_path: key,
            line_start: 0,
            line_end: 0,
            flags: 0,
        }
    }
    
    /// Create a function node
    pub fn function(
        interner: &StringInterner,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        is_async: bool,
        complexity: u16,
    ) -> Self {
        let mut flags = 0u32;
        if is_async { flags |= 1; }
        flags |= ((complexity as u32) & 0x7FFF) << 1;
        
        Self {
            kind: CompactNodeKind::Function,
            name: interner.intern(name),
            qualified_name: interner.intern(qualified_name),
            file_path: interner.intern(file_path),
            line_start,
            line_end,
            flags,
        }
    }
    
    /// Create a class node
    pub fn class(
        interner: &StringInterner,
        name: &str,
        qualified_name: &str,
        file_path: &str,
        line_start: u32,
        line_end: u32,
        method_count: u16,
    ) -> Self {
        let flags = (method_count as u32) << 16;
        
        Self {
            kind: CompactNodeKind::Class,
            name: interner.intern(name),
            qualified_name: interner.intern(qualified_name),
            file_path: interner.intern(file_path),
            line_start,
            line_end,
            flags,
        }
    }
    
    /// Get complexity (for functions)
    pub fn complexity(&self) -> u16 {
        ((self.flags >> 1) & 0x7FFF) as u16
    }
    
    /// Get is_async flag (for functions)
    pub fn is_async(&self) -> bool {
        self.flags & 1 == 1
    }
    
    /// Get method count (for classes)
    pub fn method_count(&self) -> u16 {
        (self.flags >> 16) as u16
    }
    
    /// Lines of code
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            1
        }
    }
}

/// Compact edge representation
#[derive(Debug, Clone, Copy)]
pub struct CompactEdge {
    pub kind: CompactEdgeKind,
    pub source: StrKey,  // qualified_name of source
    pub target: StrKey,  // qualified_name of target
    pub flags: u16,      // Additional flags (is_type_only, etc.)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum CompactEdgeKind {
    Contains = 0,
    Calls = 1,
    Imports = 2,
    Inherits = 3,
}

impl CompactEdge {
    pub fn contains(interner: &StringInterner, source: &str, target: &str) -> Self {
        Self {
            kind: CompactEdgeKind::Contains,
            source: interner.intern(source),
            target: interner.intern(target),
            flags: 0,
        }
    }
    
    pub fn calls(interner: &StringInterner, caller: &str, callee: &str) -> Self {
        Self {
            kind: CompactEdgeKind::Calls,
            source: interner.intern(caller),
            target: interner.intern(callee),
            flags: 0,
        }
    }
    
    pub fn imports(interner: &StringInterner, importer: &str, imported: &str, is_type_only: bool) -> Self {
        Self {
            kind: CompactEdgeKind::Imports,
            source: interner.intern(importer),
            target: interner.intern(imported),
            flags: if is_type_only { 1 } else { 0 },
        }
    }
    
    pub fn is_type_only(&self) -> bool {
        self.flags & 1 == 1
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
    fn test_compact_node_size() {
        // Verify CompactNode is small
        assert!(std::mem::size_of::<CompactNode>() <= 32);
        assert!(std::mem::size_of::<CompactEdge>() <= 16);
    }
    
    #[test]
    fn test_compact_function_flags() {
        let interner = StringInterner::new();
        let node = CompactNode::function(
            &interner,
            "my_func",
            "module::my_func",
            "src/lib.rs",
            10,
            20,
            true,  // is_async
            42,    // complexity
        );
        
        assert!(node.is_async());
        assert_eq!(node.complexity(), 42);
        assert_eq!(node.loc(), 11);
    }
}
