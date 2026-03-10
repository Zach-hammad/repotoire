use crate::graph::interner::StrKey;
use serde::{Deserialize, Serialize};

/// Node types in the code graph
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NodeKind {
    File,
    Function,
    Class,
    Module,
    Variable,
    Commit,
}

/// A node in the code graph — compact, Copy, ~48 bytes.
///
/// All string fields are interned StrKeys. Use `graph.interner().resolve(key)`
/// or the helper methods `qn()`, `path()`, `node_name()` to get `&str`.
/// Metric fields replace the old `properties: HashMap<String, Value>`.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct CodeNode {
    // Identity
    pub kind: NodeKind,
    pub name: StrKey,
    pub qualified_name: StrKey,
    pub file_path: StrKey,
    pub language: StrKey, // EMPTY_KEY = no language

    // Location
    pub line_start: u32,
    pub line_end: u32,

    // Metrics (typed fields, no HashMap)
    pub complexity: u16,
    pub param_count: u8,
    pub method_count: u16,
    pub max_nesting: u8,
    pub return_count: u8,
    pub commit_count: u16,

    // Packed boolean flags
    pub flags: u8,
}

// Flag bit positions
pub const FLAG_IS_ASYNC: u8 = 1 << 0;
pub const FLAG_IS_EXPORTED: u8 = 1 << 1;
pub const FLAG_IS_PUBLIC: u8 = 1 << 2;
pub const FLAG_IS_METHOD: u8 = 1 << 3;
pub const FLAG_ADDRESS_TAKEN: u8 = 1 << 4;
pub const FLAG_HAS_DECORATORS: u8 = 1 << 5;

impl CodeNode {
    /// Create an empty node with the given kind and all StrKeys set to EMPTY_KEY.
    pub fn empty(kind: NodeKind, empty_key: StrKey) -> Self {
        Self {
            kind,
            name: empty_key,
            qualified_name: empty_key,
            file_path: empty_key,
            language: empty_key,
            line_start: 0,
            line_end: 0,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        }
    }

    // --- Convenience builders (use GraphStore's interner) ---

    /// Create a new CodeNode with the given kind, name, and file_path.
    /// The qualified_name defaults to `file_path::name`.
    pub fn new(kind: NodeKind, name: &str, file_path: &str) -> Self {
        let i = crate::graph::interner::global_interner();
        let name_key = i.intern(name);
        let fp_key = i.intern(file_path);
        let qn = format!("{}::{}", file_path, name);
        let qn_key = i.intern(&qn);
        Self {
            kind,
            name: name_key,
            qualified_name: qn_key,
            file_path: fp_key,
            language: i.empty_key(),
            line_start: 0,
            line_end: 0,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        }
    }

    /// Create a Function node.
    pub fn function(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Function, name, file_path)
    }

    /// Create a File node (name and qualified_name both set to file_path).
    pub fn file(file_path: &str) -> Self {
        let i = crate::graph::interner::global_interner();
        let fp_key = i.intern(file_path);
        Self {
            kind: NodeKind::File,
            name: fp_key,
            qualified_name: fp_key,
            file_path: fp_key,
            language: i.empty_key(),
            line_start: 1,
            line_end: 0,
            complexity: 0,
            param_count: 0,
            method_count: 0,
            max_nesting: 0,
            return_count: 0,
            commit_count: 0,
            flags: 0,
        }
    }

    /// Create a Class node.
    pub fn class(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Class, name, file_path)
    }

    /// Builder: set qualified_name.
    pub fn with_qualified_name(mut self, qn: &str) -> Self {
        let i = crate::graph::interner::global_interner();
        self.qualified_name = i.intern(qn);
        self
    }

    /// Builder: set line range.
    pub fn with_lines(mut self, start: u32, end: u32) -> Self {
        self.line_start = start;
        self.line_end = end;
        self
    }

    /// Backward-compat shim: map old property names to typed fields.
    /// Used in tests that haven't been migrated yet.
    pub fn with_property(mut self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        let val: serde_json::Value = value.into();
        match key {
            "complexity" => {
                self.complexity = val.as_i64().unwrap_or(0) as u16;
            }
            "nesting_depth" | "max_nesting" => {
                self.max_nesting = val.as_i64().unwrap_or(0) as u8;
            }
            "methodCount" | "method_count" => {
                self.method_count = val.as_i64().unwrap_or(0) as u16;
            }
            "param_count" => {
                self.param_count = val.as_i64().unwrap_or(0) as u8;
            }
            "is_async" => {
                if val.as_bool().unwrap_or(false) {
                    self.flags |= FLAG_IS_ASYNC;
                }
            }
            "is_exported" | "exported" => {
                if val.as_bool().unwrap_or(false) {
                    self.flags |= FLAG_IS_EXPORTED;
                }
            }
            _ => {
                // Silently ignore unknown properties
            }
        }
        self
    }

    // --- Flag accessors ---

    pub fn is_async(&self) -> bool {
        self.flags & FLAG_IS_ASYNC != 0
    }
    pub fn is_exported(&self) -> bool {
        self.flags & FLAG_IS_EXPORTED != 0
    }
    pub fn is_public(&self) -> bool {
        self.flags & FLAG_IS_PUBLIC != 0
    }
    pub fn is_method(&self) -> bool {
        self.flags & FLAG_IS_METHOD != 0
    }
    pub fn address_taken(&self) -> bool {
        self.flags & FLAG_ADDRESS_TAKEN != 0
    }
    pub fn has_decorators(&self) -> bool {
        self.flags & FLAG_HAS_DECORATORS != 0
    }

    pub fn set_flag(&mut self, flag: u8) {
        self.flags |= flag;
    }

    // --- Metric accessors (backward compat shims) ---

    /// Lines of code
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            1
        }
    }

    /// Cyclomatic complexity as Option<i64> (returns None if 0).
    /// Use `self.complexity` field directly for the raw u16 value.
    pub fn complexity_opt(&self) -> Option<i64> {
        if self.complexity > 0 {
            Some(self.complexity as i64)
        } else {
            None
        }
    }

    /// Parameter count as Option<i64> (returns None if 0).
    /// Use `self.param_count` field directly for the raw u8 value.
    pub fn param_count_opt(&self) -> Option<i64> {
        if self.param_count > 0 {
            Some(self.param_count as i64)
        } else {
            None
        }
    }

    // --- String resolution helpers ---

    pub fn qn<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.qualified_name)
    }

    pub fn path<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.file_path)
    }

    pub fn node_name<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> &'a str {
        i.resolve(self.name)
    }

    pub fn lang<'a>(&self, i: &'a crate::graph::interner::StringInterner) -> Option<&'a str> {
        let s = i.resolve(self.language);
        if s.is_empty() {
            None
        } else {
            Some(s)
        }
    }

    // --- Backward compat shims for property access ---

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        match key {
            "complexity" => {
                if self.complexity > 0 {
                    Some(self.complexity as i64)
                } else {
                    None
                }
            }
            "paramCount" => {
                if self.param_count > 0 {
                    Some(self.param_count as i64)
                } else {
                    None
                }
            }
            "methodCount" => {
                if self.method_count > 0 {
                    Some(self.method_count as i64)
                } else {
                    None
                }
            }
            "maxNesting" | "nesting_depth" => {
                if self.max_nesting > 0 {
                    Some(self.max_nesting as i64)
                } else {
                    None
                }
            }
            "returnCount" => {
                if self.return_count > 0 {
                    Some(self.return_count as i64)
                } else {
                    None
                }
            }
            "commit_count" => {
                if self.commit_count > 0 {
                    Some(self.commit_count as i64)
                } else {
                    None
                }
            }
            "lineEnd" => Some(self.line_end as i64),
            "loc" => Some(self.loc() as i64),
            _ => None,
        }
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        match key {
            "is_async" => Some(self.is_async()),
            "is_exported" => Some(self.is_exported()),
            "is_public" => Some(self.is_public()),
            "is_method" => Some(self.is_method()),
            "address_taken" => Some(self.address_taken()),
            "has_decorators" => Some(self.has_decorators()),
            _ => None,
        }
    }

    #[deprecated(note = "String properties are in the ExtraProps side table. Use graph.extra_props(node.qualified_name) instead.")]
    pub fn get_str(&self, _key: &str) -> Option<&str> {
        None
    }

}

/// Edge types in the code graph
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    Calls,
    Imports,
    Contains,
    Inherits,
    Uses,
    ModifiedIn,
}

/// An edge in the code graph — compact, Copy, ~4 bytes.
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct CodeEdge {
    pub kind: EdgeKind,
    pub flags: u8, // bit 0: is_type_only
}

impl CodeEdge {
    pub fn new(kind: EdgeKind) -> Self {
        Self { kind, flags: 0 }
    }
    pub fn calls() -> Self {
        Self::new(EdgeKind::Calls)
    }
    pub fn imports() -> Self {
        Self::new(EdgeKind::Imports)
    }
    pub fn contains() -> Self {
        Self::new(EdgeKind::Contains)
    }
    pub fn inherits() -> Self {
        Self::new(EdgeKind::Inherits)
    }
    pub fn uses() -> Self {
        Self::new(EdgeKind::Uses)
    }

    pub fn with_type_only(mut self) -> Self {
        self.flags |= 1;
        self
    }

    pub fn is_type_only(&self) -> bool {
        self.flags & 1 != 0
    }

    // Backward compat shim
    pub fn with_property(self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        if key == "is_type_only" {
            let val: serde_json::Value = value.into();
            if val.as_bool().unwrap_or(false) {
                return self.with_type_only();
            }
        }
        self
    }
}

/// Extra properties stored in a side table, not on every node.
#[derive(Debug, Clone, Default)]
pub struct ExtraProps {
    pub params: Option<StrKey>,
    pub doc_comment: Option<StrKey>,
    pub decorators: Option<StrKey>,
    pub author: Option<StrKey>,
    pub last_modified: Option<StrKey>,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn compact_node_size() {
        assert!(
            std::mem::size_of::<CodeNode>() <= 48,
            "CodeNode is {} bytes, target <=48",
            std::mem::size_of::<CodeNode>()
        );
    }
    #[test]
    fn compact_edge_size() {
        assert!(
            std::mem::size_of::<CodeEdge>() <= 4,
            "CodeEdge is {} bytes, target <=4",
            std::mem::size_of::<CodeEdge>()
        );
    }
}
