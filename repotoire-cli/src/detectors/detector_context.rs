//! Shared pre-computed data for detector execution.
//!
//! Built once during `precompute_gd_startup()` and shared via `AnalysisContext`.
//! Avoids redundant graph queries and Vec<CodeNode> cloning across detectors.

use crate::detectors::class_context::ClassContextMap;
use crate::graph::GraphQueryExt;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Per-file content presence flags, pre-computed during `DetectorContext::build()`.
///
/// Detectors query these via `AnalysisContext` to skip files that lack
/// relevant keywords, avoiding expensive per-line regex scans on irrelevant files.
#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct ContentFlags(u32);

impl ContentFlags {
    /// File I/O operations: open, readFile, writeFile, unlink, rmdir, etc.
    pub const FILE_OPS: Self = Self(1 << 0);
    /// Path manipulation: path.join, os.path, filepath, pathlib, etc.
    pub const PATH_OPS: Self = Self(1 << 1);
    /// SQL keywords: SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, execute(
    pub const HAS_SQL: Self = Self(1 << 2);
    /// Import/require statements: import, require(, from
    pub const HAS_IMPORT: Self = Self(1 << 3);
    /// Dynamic code evaluation: eval(, exec(, Function(
    pub const HAS_EVAL: Self = Self(1 << 4);
    /// HTTP client usage: requests., fetch(, axios, urllib, http.get, reqwest, etc.
    pub const HAS_HTTP_CLIENT: Self = Self(1 << 5);
    /// User input sources: request., req.body, req.query, input(, sys.argv, etc.
    pub const HAS_USER_INPUT: Self = Self(1 << 6);
    /// Cryptographic operations: hashlib, crypto, md5, sha1, AES, encrypt, etc.
    pub const HAS_CRYPTO: Self = Self(1 << 7);
    /// Template rendering: render(, template, jinja, Markup(, innerHTML
    pub const HAS_TEMPLATE: Self = Self(1 << 8);
    /// Deserialization: pickle, marshal, yaml.load, json.loads, deserialize
    pub const HAS_SERIALIZE: Self = Self(1 << 9);
    /// OS command execution: os.system, subprocess, child_process, popen
    pub const HAS_EXEC: Self = Self(1 << 10);
    /// Secret/credential patterns: password, secret, api_key, token, private_key, etc.
    pub const HAS_SECRET_PATTERN: Self = Self(1 << 11);
    /// ML/data-science libraries: torch, numpy, tensorflow, sklearn, pandas
    pub const HAS_ML: Self = Self(1 << 12);
    /// React hooks and imports: useState, useEffect, React, react
    pub const HAS_REACT: Self = Self(1 << 13);
    /// Django framework: django, Django
    pub const HAS_DJANGO: Self = Self(1 << 14);
    /// Express.js framework: express, app.get(, app.post(, router.
    pub const HAS_EXPRESS: Self = Self(1 << 15);

    pub const fn empty() -> Self {
        Self(0)
    }

    pub const fn all() -> Self {
        Self(u32::MAX)
    }

    pub fn union(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }

    pub fn is_empty(self) -> bool {
        self.0 == 0
    }

    pub fn has(self, flag: Self) -> bool {
        self.0 & flag.0 != 0
    }

    pub fn set(&mut self, flag: Self) {
        self.0 |= flag.0;
    }
}

/// Scan file content and return which keyword categories are present.
///
/// Called once per file during `DetectorContext::build()` (in parallel via rayon).
/// Each category uses simple `str::contains()` checks -- no aho-corasick needed.
pub(crate) fn compute_content_flags(content: &str) -> ContentFlags {
    let mut flags = ContentFlags::default();

    // FILE_OPS
    if content.contains("open(")
        || content.contains("unlink")
        || content.contains("rmdir")
        || content.contains("mkdir")
        || content.contains("copyFile")
        || content.contains("rename(")
        || content.contains("readFile")
        || content.contains("writeFile")
        || content.contains("shutil")
        || content.contains("os.remove")
        || content.contains("createReadStream")
        || content.contains("createWriteStream")
        || content.contains("sendFile")
        || content.contains("send_file")
        || content.contains("serve_file")
        || content.contains("appendFile")
        || content.contains("statSync")
        || content.contains("accessSync")
    {
        flags.set(ContentFlags::FILE_OPS);
    }

    // PATH_OPS
    if content.contains("path.join")
        || content.contains("path.resolve")
        || content.contains("os.path")
        || content.contains("filepath")
        || content.contains("pathlib")
    {
        flags.set(ContentFlags::PATH_OPS);
    }

    // HAS_SQL
    if content.contains("SELECT ")
        || content.contains("INSERT ")
        || content.contains("UPDATE ")
        || content.contains("DELETE ")
        || content.contains("CREATE ")
        || content.contains("DROP ")
        || content.contains("select ")
        || content.contains("insert ")
        || content.contains("execute(")
    {
        flags.set(ContentFlags::HAS_SQL);
    }

    // HAS_IMPORT
    if content.contains("import ") || content.contains("require(") || content.contains("from ") {
        flags.set(ContentFlags::HAS_IMPORT);
    }

    // HAS_EVAL
    if content.contains("eval(") || content.contains("exec(") || content.contains("Function(") {
        flags.set(ContentFlags::HAS_EVAL);
    }

    // HAS_HTTP_CLIENT
    if content.contains("requests.")
        || content.contains("fetch(")
        || content.contains("axios")
        || content.contains("urllib")
        || content.contains("http.get")
        || content.contains("http.post")
        || content.contains("HttpClient")
        || content.contains("ureq")
        || content.contains("reqwest")
    {
        flags.set(ContentFlags::HAS_HTTP_CLIENT);
    }

    // HAS_USER_INPUT
    if content.contains("request.")
        || content.contains("req.body")
        || content.contains("req.query")
        || content.contains("req.params")
        || content.contains("request.GET")
        || content.contains("request.POST")
        || content.contains("input(")
        || content.contains("sys.argv")
        || content.contains("process.argv")
    {
        flags.set(ContentFlags::HAS_USER_INPUT);
    }

    // HAS_CRYPTO
    if content.contains("hashlib")
        || content.contains("crypto")
        || content.contains("md5")
        || content.contains("sha1")
        || content.contains("DES")
        || content.contains("AES")
        || content.contains("cipher")
        || content.contains("encrypt")
        || content.contains("decrypt")
    {
        flags.set(ContentFlags::HAS_CRYPTO);
    }

    // HAS_TEMPLATE
    if content.contains("render(")
        || content.contains("template")
        || content.contains("jinja")
        || content.contains("Markup(")
        || content.contains("innerHTML")
    {
        flags.set(ContentFlags::HAS_TEMPLATE);
    }

    // HAS_SERIALIZE
    if content.contains("pickle")
        || content.contains("marshal")
        || content.contains("yaml.load")
        || content.contains("json.loads")
        || content.contains("deserialize")
        || content.contains("ObjectInputStream")
        || content.contains("readObject")
        || content.contains("XMLDecoder")
    {
        flags.set(ContentFlags::HAS_SERIALIZE);
    }

    // HAS_EXEC
    if content.contains("os.system")
        || content.contains("subprocess")
        || content.contains("child_process")
        || content.contains("popen")
    {
        flags.set(ContentFlags::HAS_EXEC);
    }

    // HAS_SECRET_PATTERN
    if content.contains("password")
        || content.contains("secret")
        || content.contains("api_key")
        || content.contains("token")
        || content.contains("private_key")
        || content.contains("BEGIN RSA")
    {
        flags.set(ContentFlags::HAS_SECRET_PATTERN);
    }

    // HAS_ML
    if content.contains("torch")
        || content.contains("numpy")
        || content.contains("tensorflow")
        || content.contains("sklearn")
        || content.contains("pandas")
    {
        flags.set(ContentFlags::HAS_ML);
    }

    // HAS_REACT
    if content.contains("useState")
        || content.contains("useEffect")
        || content.contains("React")
        || content.contains("react")
    {
        flags.set(ContentFlags::HAS_REACT);
    }

    // HAS_DJANGO
    if content.contains("django") || content.contains("Django") {
        flags.set(ContentFlags::HAS_DJANGO);
    }

    // HAS_EXPRESS
    if content.contains("express")
        || content.contains("app.get(")
        || content.contains("app.post(")
        || content.contains("router.")
    {
        flags.set(ContentFlags::HAS_EXPRESS);
    }

    flags
}

/// Shared pre-computed data available to all detectors.
///
/// This is built in parallel with taint analysis and HMM (zero wall-clock cost)
/// and provides zero-copy access to commonly needed graph data.
#[allow(dead_code)] // Fields are scaffolding for detectors that will consume them
pub struct DetectorContext {
    /// QN -> Vec<caller QN> -- avoids Vec<CodeNode> cloning in get_callers()
    pub callers_by_qn: HashMap<String, Vec<String>>,
    /// QN -> Vec<callee QN> -- avoids Vec<CodeNode> cloning in get_callees()
    pub callees_by_qn: HashMap<String, Vec<String>>,
    /// Parent class QN -> Vec<child class QN>
    pub class_children: HashMap<String, Vec<String>>,
    /// Pre-loaded raw file content
    pub file_contents: HashMap<PathBuf, Arc<str>>,
    /// Pre-computed per-file content keyword flags (16 categories).
    /// Populated during build() alongside file_contents, zero extra I/O cost.
    pub content_flags: HashMap<PathBuf, ContentFlags>,
    /// Pre-built class contexts for god class detection (built as 5th parallel thread)
    pub class_contexts: Option<Arc<ClassContextMap>>,
    /// Resolved variable values from graph-based constant propagation
    pub value_store: Option<Arc<crate::values::store::ValueStore>>,
    /// Repository root path
    pub repo_path: PathBuf,
}

impl DetectorContext {
    /// Create an empty DetectorContext (for FileLocal-only detection that
    /// doesn't need graph-derived callers/callees maps).
    pub fn empty() -> Self {
        Self {
            callers_by_qn: HashMap::new(),
            callees_by_qn: HashMap::new(),
            class_children: HashMap::new(),
            file_contents: HashMap::new(),
            content_flags: HashMap::new(),
            class_contexts: None,
            value_store: None,
            repo_path: PathBuf::new(),
        }
    }

    /// Build the detector context from the graph and source files.
    ///
    /// Reads the call graph, inheritance edges, and file contents.
    /// Designed to run in parallel with other precompute work.
    ///
    /// Uses NodeIndex-based API when available (CodeGraph) to avoid
    /// Vec<CodeNode> cloning and (StrKey, StrKey) pair allocation.
    pub fn build(
        graph: &dyn crate::graph::GraphQuery,
        source_files: &[PathBuf],
        value_store: Option<Arc<crate::values::store::ValueStore>>,
        repo_path: &Path,
    ) -> (Self, Vec<(PathBuf, Arc<str>, ContentFlags)>) {
        let i = graph.interner();
        use rayon::prelude::*;

        let func_idxs = graph.functions_idx();

        // Build callers/callees maps
        let mut callers_by_qn: HashMap<String, Vec<String>>;
        let mut callees_by_qn: HashMap<String, Vec<String>>;

        if !func_idxs.is_empty() {
            // NodeIndex-based path (CodeGraph): iterate functions and their adjacency directly
            callers_by_qn = HashMap::new();
            callees_by_qn = HashMap::new();

            for &func_idx in func_idxs {
                let Some(func) = graph.node_idx(func_idx) else {
                    continue;
                };
                let func_qn = func.qn(i).to_string();

                let callee_idxs = graph.callees_idx(func_idx);
                if !callee_idxs.is_empty() {
                    let callee_qns: Vec<String> = callee_idxs
                        .iter()
                        .filter_map(|&ci| graph.node_idx(ci).map(|n| n.qn(i).to_string()))
                        .collect();
                    callees_by_qn.insert(func_qn.clone(), callee_qns);
                }

                let caller_idxs = graph.callers_idx(func_idx);
                if !caller_idxs.is_empty() {
                    let caller_qns: Vec<String> = caller_idxs
                        .iter()
                        .filter_map(|&ci| graph.node_idx(ci).map(|n| n.qn(i).to_string()))
                        .collect();
                    callers_by_qn.insert(func_qn, caller_qns);
                }
            }
        } else {
            // Fallback: old API for non-CodeGraph implementors
            let functions = graph.get_functions();
            let (_qn_to_idx, callers_by_idx, callees_by_idx) = graph.build_call_maps_raw();

            callers_by_qn = HashMap::with_capacity(callers_by_idx.len());
            callees_by_qn = HashMap::with_capacity(callees_by_idx.len());

            for (&callee_idx, caller_idxs) in &callers_by_idx {
                if let Some(callee_qn) = functions.get(callee_idx).map(|f| f.qn(i).to_string()) {
                    let caller_qns: Vec<String> = caller_idxs
                        .iter()
                        .filter_map(|&ci| functions.get(ci).map(|f| f.qn(i).to_string()))
                        .collect();
                    callers_by_qn.insert(callee_qn, caller_qns);
                }
            }

            for (&caller_idx, callee_idxs) in &callees_by_idx {
                if let Some(caller_qn) = functions.get(caller_idx).map(|f| f.qn(i).to_string()) {
                    let callee_qns: Vec<String> = callee_idxs
                        .iter()
                        .filter_map(|&ci| functions.get(ci).map(|f| f.qn(i).to_string()))
                        .collect();
                    callees_by_qn.insert(caller_qn, callee_qns);
                }
            }
        }

        // Build class hierarchy using NodeIndex-based API when available
        let inherit_edges = graph.all_inheritance_edges();
        let mut class_children: HashMap<String, Vec<String>> = HashMap::new();
        if !inherit_edges.is_empty() {
            for &(child_idx, parent_idx) in inherit_edges {
                if let (Some(child), Some(parent)) =
                    (graph.node_idx(child_idx), graph.node_idx(parent_idx))
                {
                    class_children
                        .entry(parent.qn(i).to_string())
                        .or_default()
                        .push(child.qn(i).to_string());
                }
            }
        } else {
            // Fallback
            let inheritance = graph.get_inheritance();
            for (child, parent) in &inheritance {
                class_children
                    .entry(i.resolve(*parent).to_string())
                    .or_default()
                    .push(i.resolve(*child).to_string());
            }
        }

        // Pre-load file contents and compute content flags in parallel (single pass)
        let file_data: Vec<(PathBuf, Arc<str>, ContentFlags)> = source_files
            .par_iter()
            .filter_map(|f| {
                std::fs::read_to_string(f).ok().map(|c| {
                    let flags = compute_content_flags(&c);
                    (f.clone(), Arc::from(c.as_str()), flags)
                })
            })
            .collect();

        // Clone file data for FileIndex construction (caller builds FileIndex from this)
        // Use relative paths so FileIndex entries match graph-stored paths (e.g. "src/file.rs")
        let file_data_for_index: Vec<(PathBuf, Arc<str>, ContentFlags)> = file_data
            .iter()
            .map(|(p, c, f)| {
                let rel = p.strip_prefix(repo_path).unwrap_or(p);
                (rel.to_path_buf(), Arc::clone(c), *f)
            })
            .collect();

        let mut file_contents = HashMap::with_capacity(file_data.len());
        let mut content_flags = HashMap::with_capacity(file_data.len());
        for (path, content, flags) in file_data {
            let rel = path.strip_prefix(repo_path).unwrap_or(&path).to_path_buf();
            file_contents.insert(rel.clone(), content);
            content_flags.insert(rel, flags);
        }

        (
            Self {
                callers_by_qn,
                callees_by_qn,
                class_children,
                file_contents,
                content_flags,
                class_contexts: None,
                value_store,
                repo_path: repo_path.to_path_buf(),
            },
            file_data_for_index,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::graph::builder::GraphBuilder;
    use crate::graph::store_models::{CodeEdge, CodeNode};

    #[test]
    fn test_empty_graph_produces_empty_context() {
        let graph = GraphBuilder::new();
        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));
        assert!(ctx.callers_by_qn.is_empty());
        assert!(ctx.callees_by_qn.is_empty());
        assert!(ctx.class_children.is_empty());
        assert!(ctx.file_contents.is_empty());
    }

    #[test]
    fn test_file_contents_loaded() {
        let graph = GraphBuilder::new();
        let dir = tempfile::tempdir().unwrap();
        let file_path = dir.path().join("test.py");
        std::fs::write(&file_path, "def hello(): pass").unwrap();

        let (ctx, _file_data) =
            DetectorContext::build(&graph, &[file_path.clone()], None, dir.path());
        assert_eq!(ctx.file_contents.len(), 1);
        let rel_path = file_path.strip_prefix(dir.path()).unwrap().to_path_buf();
        assert!(ctx.file_contents.contains_key(&rel_path));
        assert_eq!(&*ctx.file_contents[&rel_path], "def hello(): pass");
    }

    #[test]
    fn test_file_contents_skips_missing_files() {
        let graph = GraphBuilder::new();
        let missing = PathBuf::from("/nonexistent/path/file.py");

        let (ctx, _file_data) = DetectorContext::build(&graph, &[missing], None, Path::new("/tmp"));
        assert!(ctx.file_contents.is_empty());
    }

    #[test]
    fn test_callers_callees_populated() {
        let mut graph = GraphBuilder::new();

        graph
            .add_node(CodeNode::function("caller", "test.py").with_qualified_name("module.caller"));
        graph
            .add_node(CodeNode::function("callee", "test.py").with_qualified_name("module.callee"));
        graph.add_edge_by_name("module.caller", "module.callee", CodeEdge::calls());

        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));

        // callers_by_qn: callee -> [caller]
        assert!(ctx.callers_by_qn.contains_key("module.callee"));
        assert!(ctx.callers_by_qn["module.callee"].contains(&"module.caller".to_string()));

        // callees_by_qn: caller -> [callee]
        assert!(ctx.callees_by_qn.contains_key("module.caller"));
        assert!(ctx.callees_by_qn["module.caller"].contains(&"module.callee".to_string()));
    }

    #[test]
    fn test_class_children_populated() {
        let mut graph = GraphBuilder::new();

        graph.add_node(CodeNode::class("Parent", "test.py").with_qualified_name("module.Parent"));
        graph.add_node(CodeNode::class("Child", "test.py").with_qualified_name("module.Child"));
        graph.add_edge_by_name("module.Child", "module.Parent", CodeEdge::inherits());

        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));
        assert!(ctx.class_children.contains_key("module.Parent"));
        assert!(ctx.class_children["module.Parent"].contains(&"module.Child".to_string()));
    }

    #[test]
    fn test_multiple_callers_for_same_callee() {
        let mut graph = GraphBuilder::new();

        graph.add_node(CodeNode::function("a", "test.py").with_qualified_name("mod.a"));
        graph.add_node(CodeNode::function("b", "test.py").with_qualified_name("mod.b"));
        graph.add_node(CodeNode::function("target", "test.py").with_qualified_name("mod.target"));
        graph.add_edge_by_name("mod.a", "mod.target", CodeEdge::calls());
        graph.add_edge_by_name("mod.b", "mod.target", CodeEdge::calls());

        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));
        let callers = &ctx.callers_by_qn["mod.target"];
        assert_eq!(callers.len(), 2);
        assert!(callers.contains(&"mod.a".to_string()));
        assert!(callers.contains(&"mod.b".to_string()));
    }

    #[test]
    fn test_multiple_children_for_same_parent() {
        let mut graph = GraphBuilder::new();

        graph.add_node(CodeNode::class("Base", "test.py").with_qualified_name("mod.Base"));
        graph.add_node(CodeNode::class("ChildA", "test.py").with_qualified_name("mod.ChildA"));
        graph.add_node(CodeNode::class("ChildB", "test.py").with_qualified_name("mod.ChildB"));
        graph.add_edge_by_name("mod.ChildA", "mod.Base", CodeEdge::inherits());
        graph.add_edge_by_name("mod.ChildB", "mod.Base", CodeEdge::inherits());

        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));
        let children = &ctx.class_children["mod.Base"];
        assert_eq!(children.len(), 2);
        assert!(children.contains(&"mod.ChildA".to_string()));
        assert!(children.contains(&"mod.ChildB".to_string()));
    }

    #[test]
    fn test_value_store_stored_when_provided() {
        let graph = GraphBuilder::new();
        let store = Arc::new(crate::values::store::ValueStore::new());
        let (ctx, _file_data) = DetectorContext::build(&graph, &[], Some(store), Path::new("/tmp"));
        assert!(ctx.value_store.is_some());
    }

    #[test]
    fn test_value_store_none_when_not_provided() {
        let graph = GraphBuilder::new();
        let (ctx, _file_data) = DetectorContext::build(&graph, &[], None, Path::new("/tmp"));
        assert!(ctx.value_store.is_none());
    }

    // ── ContentFlags unit tests ──────────────────────────────────────

    #[test]
    fn test_content_flags_file_ops() {
        let flags = super::compute_content_flags("let f = open(path, 'r')");
        assert!(flags.has(ContentFlags::FILE_OPS));
        assert!(!flags.has(ContentFlags::PATH_OPS));
    }

    #[test]
    fn test_content_flags_path_ops() {
        let flags = super::compute_content_flags("const p = path.join(dir, file)");
        assert!(!flags.has(ContentFlags::FILE_OPS));
        assert!(flags.has(ContentFlags::PATH_OPS));
    }

    #[test]
    fn test_content_flags_benign_content() {
        let flags = super::compute_content_flags("fn main() { println!(\"hello\"); }");
        assert!(!flags.has(ContentFlags::FILE_OPS));
        assert!(!flags.has(ContentFlags::PATH_OPS));
    }

    #[test]
    fn test_content_flags_multiple_categories() {
        let flags = super::compute_content_flags("const f = open(path.join(dir, file), 'r')");
        assert!(flags.has(ContentFlags::FILE_OPS));
        assert!(flags.has(ContentFlags::PATH_OPS));
    }

    #[test]
    fn test_content_flags_populated_in_build() {
        let graph = GraphBuilder::new();
        let dir = tempfile::tempdir().unwrap();

        let py_file = dir.path().join("app.py");
        std::fs::write(&py_file, "f = open(os.path.join(d, request.GET['f']))").unwrap();

        let safe_file = dir.path().join("safe.py");
        std::fs::write(&safe_file, "x = 1 + 2").unwrap();

        let (ctx, _file_data) = DetectorContext::build(
            &graph,
            &[py_file.clone(), safe_file.clone()],
            None,
            dir.path(),
        );

        // app.py should have both FILE_OPS and PATH_OPS flags
        let rel_py = py_file.strip_prefix(dir.path()).unwrap().to_path_buf();
        let app_flags = ctx.content_flags[&rel_py];
        assert!(app_flags.has(ContentFlags::FILE_OPS));
        assert!(app_flags.has(ContentFlags::PATH_OPS));

        // safe.py should have no flags
        let rel_safe = safe_file.strip_prefix(dir.path()).unwrap().to_path_buf();
        let safe_flags = ctx.content_flags[&rel_safe];
        assert!(!safe_flags.has(ContentFlags::FILE_OPS));
        assert!(!safe_flags.has(ContentFlags::PATH_OPS));
    }

    // ── Extended ContentFlags unit tests ────────────────────────────────

    #[test]
    fn test_content_flags_has_sql() {
        let flags = compute_content_flags("cursor.execute(\"SELECT * FROM users\")");
        assert!(flags.has(ContentFlags::HAS_SQL));

        let flags2 = compute_content_flags("db.run(\"INSERT INTO logs VALUES (?)\")");
        assert!(flags2.has(ContentFlags::HAS_SQL));

        let no_sql = compute_content_flags("let x = 42;");
        assert!(!no_sql.has(ContentFlags::HAS_SQL));
    }

    #[test]
    fn test_content_flags_has_import() {
        let flags = compute_content_flags("import os\nfrom pathlib import Path");
        assert!(flags.has(ContentFlags::HAS_IMPORT));

        let flags2 = compute_content_flags("const fs = require('fs')");
        assert!(flags2.has(ContentFlags::HAS_IMPORT));

        let no_import = compute_content_flags("fn main() { println!(\"hi\"); }");
        assert!(!no_import.has(ContentFlags::HAS_IMPORT));
    }

    #[test]
    fn test_content_flags_has_eval() {
        let flags = compute_content_flags("result = eval(user_input)");
        assert!(flags.has(ContentFlags::HAS_EVAL));

        let flags2 = compute_content_flags("exec(code_str)");
        assert!(flags2.has(ContentFlags::HAS_EVAL));

        let flags3 = compute_content_flags("new Function(body)");
        assert!(flags3.has(ContentFlags::HAS_EVAL));

        let no_eval = compute_content_flags("let value = calculate(10);");
        assert!(!no_eval.has(ContentFlags::HAS_EVAL));
    }

    #[test]
    fn test_content_flags_has_user_input() {
        let flags = compute_content_flags("name = request.GET['name']");
        assert!(flags.has(ContentFlags::HAS_USER_INPUT));

        let flags2 = compute_content_flags("const data = req.body.data");
        assert!(flags2.has(ContentFlags::HAS_USER_INPUT));

        let flags3 = compute_content_flags("val = input('Enter: ')");
        assert!(flags3.has(ContentFlags::HAS_USER_INPUT));

        let flags4 = compute_content_flags("args = sys.argv[1:]");
        assert!(flags4.has(ContentFlags::HAS_USER_INPUT));

        let no_input = compute_content_flags("x = compute(42)");
        assert!(!no_input.has(ContentFlags::HAS_USER_INPUT));
    }

    #[test]
    fn test_content_flags_has_ml() {
        let flags = compute_content_flags("import torch\nmodel = torch.nn.Linear(10, 5)");
        assert!(flags.has(ContentFlags::HAS_ML));

        let flags2 = compute_content_flags("import numpy as np\narr = np.array([1,2,3])");
        assert!(flags2.has(ContentFlags::HAS_ML));

        let flags3 = compute_content_flags("from sklearn.ensemble import RandomForestClassifier");
        assert!(flags3.has(ContentFlags::HAS_ML));

        let no_ml = compute_content_flags("fn add(a: i32, b: i32) -> i32 { a + b }");
        assert!(!no_ml.has(ContentFlags::HAS_ML));
    }

    #[test]
    fn test_content_flags_has_http_client() {
        let flags = compute_content_flags("resp = requests.get(url)");
        assert!(flags.has(ContentFlags::HAS_HTTP_CLIENT));

        let flags2 = compute_content_flags("const data = await fetch(url)");
        assert!(flags2.has(ContentFlags::HAS_HTTP_CLIENT));

        let flags3 = compute_content_flags("let client = reqwest::Client::new()");
        assert!(flags3.has(ContentFlags::HAS_HTTP_CLIENT));
    }

    #[test]
    fn test_content_flags_has_crypto() {
        let flags = compute_content_flags("h = hashlib.sha256(data)");
        assert!(flags.has(ContentFlags::HAS_CRYPTO));

        let flags2 = compute_content_flags("encrypted = encrypt(plaintext, key)");
        assert!(flags2.has(ContentFlags::HAS_CRYPTO));
    }

    #[test]
    fn test_content_flags_has_template() {
        let flags = compute_content_flags("return render(request, 'index.html', ctx)");
        assert!(flags.has(ContentFlags::HAS_TEMPLATE));

        let flags2 = compute_content_flags("el.innerHTML = userInput");
        assert!(flags2.has(ContentFlags::HAS_TEMPLATE));
    }

    #[test]
    fn test_content_flags_has_serialize() {
        let flags = compute_content_flags("data = pickle.loads(raw)");
        assert!(flags.has(ContentFlags::HAS_SERIALIZE));

        let flags2 = compute_content_flags("obj = json.loads(text)");
        assert!(flags2.has(ContentFlags::HAS_SERIALIZE));
    }

    #[test]
    fn test_content_flags_has_exec() {
        let flags = compute_content_flags("os.system('ls -la')");
        assert!(flags.has(ContentFlags::HAS_EXEC));

        let flags2 = compute_content_flags("proc = subprocess.Popen(cmd)");
        assert!(flags2.has(ContentFlags::HAS_EXEC));

        let flags3 = compute_content_flags("const cp = require('child_process')");
        assert!(flags3.has(ContentFlags::HAS_EXEC));
    }

    #[test]
    fn test_content_flags_has_secret_pattern() {
        let flags = compute_content_flags("password = 'hunter2'");
        assert!(flags.has(ContentFlags::HAS_SECRET_PATTERN));

        let flags2 = compute_content_flags("api_key = os.environ['KEY']");
        assert!(flags2.has(ContentFlags::HAS_SECRET_PATTERN));
    }

    #[test]
    fn test_content_flags_has_react() {
        let flags = compute_content_flags("const [val, setVal] = useState(0)");
        assert!(flags.has(ContentFlags::HAS_REACT));

        let flags2 = compute_content_flags("import React from 'react'");
        assert!(flags2.has(ContentFlags::HAS_REACT));
    }

    #[test]
    fn test_content_flags_has_django() {
        let flags = compute_content_flags("from django.http import HttpResponse");
        assert!(flags.has(ContentFlags::HAS_DJANGO));

        let no_django = compute_content_flags("from flask import Flask");
        assert!(!no_django.has(ContentFlags::HAS_DJANGO));
    }

    #[test]
    fn test_content_flags_has_express() {
        let flags = compute_content_flags("const app = express()");
        assert!(flags.has(ContentFlags::HAS_EXPRESS));

        let flags2 = compute_content_flags("app.get('/api', handler)");
        assert!(flags2.has(ContentFlags::HAS_EXPRESS));

        let flags3 = compute_content_flags("router.use(middleware)");
        assert!(flags3.has(ContentFlags::HAS_EXPRESS));
    }

    #[test]
    fn test_content_flags_multi_flag_complex() {
        // A realistic file that should trigger many flags
        let content = r#"
import os
from django.http import HttpResponse
import hashlib

def view(request):
    name = request.GET['name']
    query = "SELECT * FROM users WHERE name = '%s'" % name
    cursor.execute(query)
    h = hashlib.md5(name.encode())
    return render(request, 'result.html', {'hash': h})
"#;
        let flags = compute_content_flags(content);
        assert!(flags.has(ContentFlags::HAS_IMPORT));
        assert!(flags.has(ContentFlags::HAS_DJANGO));
        assert!(flags.has(ContentFlags::HAS_CRYPTO));
        assert!(flags.has(ContentFlags::HAS_USER_INPUT));
        assert!(flags.has(ContentFlags::HAS_SQL));
        assert!(flags.has(ContentFlags::HAS_TEMPLATE));
        // Should NOT have these
        assert!(!flags.has(ContentFlags::HAS_ML));
        assert!(!flags.has(ContentFlags::HAS_EXPRESS));
        assert!(!flags.has(ContentFlags::HAS_REACT));
    }

    #[test]
    fn test_content_flags_utility_methods() {
        let empty = ContentFlags::empty();
        assert!(empty.is_empty());
        assert!(!empty.has(ContentFlags::FILE_OPS));

        let all = ContentFlags::all();
        assert!(!all.is_empty());
        assert!(all.has(ContentFlags::FILE_OPS));
        assert!(all.has(ContentFlags::HAS_SQL));
        assert!(all.has(ContentFlags::HAS_EXPRESS));

        let union = ContentFlags::FILE_OPS.union(ContentFlags::HAS_SQL);
        assert!(union.has(ContentFlags::FILE_OPS));
        assert!(union.has(ContentFlags::HAS_SQL));
        assert!(!union.has(ContentFlags::PATH_OPS));
    }

    #[test]
    fn test_content_flags_eval_vs_exec_distinction() {
        // exec( should trigger HAS_EVAL, NOT HAS_EXEC
        let flags = compute_content_flags("exec(code_string)");
        assert!(flags.has(ContentFlags::HAS_EVAL));
        assert!(!flags.has(ContentFlags::HAS_EXEC));

        // os.system should trigger HAS_EXEC, NOT HAS_EVAL
        let flags2 = compute_content_flags("os.system('rm -rf /')");
        assert!(flags2.has(ContentFlags::HAS_EXEC));
        assert!(!flags2.has(ContentFlags::HAS_EVAL));

        // subprocess should trigger HAS_EXEC only
        let flags3 = compute_content_flags("subprocess.run(['ls'])");
        assert!(flags3.has(ContentFlags::HAS_EXEC));
        assert!(!flags3.has(ContentFlags::HAS_EVAL));
    }
}
