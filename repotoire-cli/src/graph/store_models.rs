use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Node types in the code graph
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NodeKind {
    File,
    Function,
    Class,
    Module,
    Variable,
    Commit,
}

/// A node in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeNode {
    pub kind: NodeKind,
    pub name: String,
    pub qualified_name: String,
    pub file_path: String,
    pub line_start: u32,
    pub line_end: u32,
    pub language: Option<String>,
    pub properties: HashMap<String, serde_json::Value>,
}

impl CodeNode {
    pub fn new(kind: NodeKind, name: &str, file_path: &str) -> Self {
        Self {
            kind,
            name: name.to_string(),
            qualified_name: name.to_string(),
            file_path: file_path.to_string(),
            line_start: 0,
            line_end: 0,
            language: None,
            properties: HashMap::new(),
        }
    }

    pub fn file(path: &str) -> Self {
        Self::new(NodeKind::File, path, path)
    }

    pub fn function(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Function, name, file_path)
    }

    pub fn class(name: &str, file_path: &str) -> Self {
        Self::new(NodeKind::Class, name, file_path)
    }

    pub fn with_qualified_name(mut self, qn: &str) -> Self {
        self.qualified_name = qn.to_string();
        self
    }

    pub fn with_lines(mut self, start: u32, end: u32) -> Self {
        self.line_start = start;
        self.line_end = end;
        self
    }

    pub fn with_language(mut self, lang: &str) -> Self {
        self.language = Some(lang.to_string());
        self
    }

    pub fn with_property(mut self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        self.properties.insert(key.to_string(), value.into());
        self
    }

    pub fn set_property(&mut self, key: &str, value: impl Into<serde_json::Value>) {
        self.properties.insert(key.to_string(), value.into());
    }

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        self.properties.get(key).and_then(|v| v.as_i64())
    }

    pub fn get_f64(&self, key: &str) -> Option<f64> {
        self.properties.get(key).and_then(|v| v.as_f64())
    }

    pub fn get_str(&self, key: &str) -> Option<&str> {
        self.properties.get(key).and_then(|v| v.as_str())
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        self.properties.get(key).and_then(|v| v.as_bool())
    }

    /// Lines of code
    pub fn loc(&self) -> u32 {
        if self.line_end >= self.line_start {
            self.line_end - self.line_start + 1
        } else {
            0
        }
    }

    /// Cyclomatic complexity (if stored)
    pub fn complexity(&self) -> Option<i64> {
        self.get_i64("complexity")
    }

    /// Parameter count (for functions)
    pub fn param_count(&self) -> Option<i64> {
        self.get_i64("paramCount")
    }
}

/// Edge types in the code graph
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum EdgeKind {
    Calls,
    Imports,
    Contains,
    Inherits,
    Uses,
    ModifiedIn,
}

/// An edge in the code graph
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CodeEdge {
    pub kind: EdgeKind,
    pub properties: HashMap<String, serde_json::Value>,
}

impl CodeEdge {
    pub fn new(kind: EdgeKind) -> Self {
        Self {
            kind,
            properties: HashMap::new(),
        }
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

    pub fn with_property(mut self, key: &str, value: impl Into<serde_json::Value>) -> Self {
        self.properties.insert(key.to_string(), value.into());
        self
    }
}
