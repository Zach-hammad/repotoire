//! Edge resolution for call and import edges in the code graph.

use crate::graph::store_models::CodeEdge;
use crate::parsers::ParseResult;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use super::module_lookup::ModuleLookup;

pub(crate) const AMBIGUOUS_METHOD_NAMES: &[&str] = &[
    // Iterator trait methods
    "find",
    "map",
    "filter",
    "fold",
    "reduce",
    "collect",
    "any",
    "all",
    "count",
    "sum",
    "min",
    "max",
    "zip",
    "chain",
    "skip",
    "take",
    "flat_map",
    "for_each",
    "enumerate",
    "peekable",
    "position",
    // Option/Result methods
    "unwrap",
    "expect",
    "ok",
    "err",
    "map_err",
    "unwrap_or",
    "unwrap_or_else",
    "unwrap_or_default",
    "and_then",
    "or_else",
    "is_some",
    "is_none",
    "is_ok",
    "is_err",
    // String/str methods
    "contains",
    "starts_with",
    "ends_with",
    "replace",
    "trim",
    "split",
    "join",
    "to_lowercase",
    "to_uppercase",
    "chars",
    "bytes",
    "lines",
    // Vec/slice methods
    "push",
    "pop",
    "insert",
    "remove",
    "sort",
    "sort_by",
    "retain",
    "extend",
    "truncate",
    "clear",
    "is_empty",
    "len",
    "first",
    "last",
    "iter",
    "into_iter",
    "iter_mut",
    // HashMap/BTreeMap
    "entry",
    "or_insert",
    "or_default",
    "or_insert_with",
    "keys",
    "values",
    // Conversion traits
    "into",
    "from",
    "as_ref",
    "as_mut",
    "to_owned",
    "to_string",
    "clone",
    // Display/Debug/comparison traits
    "fmt",
    "eq",
    "cmp",
    "partial_cmp",
    "hash",
    // I/O
    "read",
    "write",
    "flush",
    "close",
    "seek",
    // Sync primitives
    "lock",
    "unlock",
    "send",
    "recv",
    // Common trait methods
    "next",
    "poll",
    "drop",
    "deref",
    "index",
    "borrow",
    // Python/JS common builtins (also bare names from method calls)
    "append",
    "update",
    "items",
    "keys",
    "values",
    "strip",
    "encode",
    "decode",
    "match",
    "test",
    "exec",
    "apply",
    "bind",
    "call",
    "then",
    "catch",
    "finally",
    "resolve",
    "reject",
    "slice",
    "splice",
    "shift",
    "unshift",
    "concat",
    "includes",
    "indexOf",
    "forEach",
    "some",
    "every",
    "flat",
    "fill",
    "at",
    "with",
];


pub(super) fn build_call_edges_fast(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    parse_results: &[(PathBuf, Arc<ParseResult>)],
    _repo_path: &Path,
    global_func_map: &HashMap<String, String>,
    module_lookup: &ModuleLookup,
) {
    for (caller, callee) in &result.calls {
        let parts: Vec<&str> = callee.rsplitn(2, "::").collect();
        let callee_name = parts[0];
        let callee_module = if parts.len() > 1 {
            Some(parts[1])
        } else {
            None
        };
        let callee_name = callee_name.rsplit('.').next().unwrap_or(callee_name);

        // Try to find callee in this file first (fast path)
        if let Some(callee_func) = result.functions.iter().find(|f| f.name == callee_name) {
            edges.push((
                caller.clone(),
                callee_func.qualified_name.clone(),
                CodeEdge::calls(),
            ));
            continue;
        }

        // Skip cross-file resolution for bare method names that are ambiguous.
        // These come from method_call_expression nodes where the parser extracts
        // just the method name without receiver type. Resolving "find" globally
        // would conflate str::find, Iterator::find, and user-defined find() into
        // one node, creating massive false-positive fan-in/fan-out counts.
        if callee_module.is_none()
            && AMBIGUOUS_METHOD_NAMES.contains(&callee_name)
        {
            continue;
        }

        // Use module lookup for O(1) cross-file resolution
        let found = resolve_callee_cross_file(
            callee_name,
            callee_module,
            module_lookup,
            parse_results,
            global_func_map,
        );
        let callee_qn = match found {
            Some(qn) => qn,
            None => continue,
        };
        edges.push((caller.clone(), callee_qn, CodeEdge::calls()));
    }
}

fn resolve_callee_cross_file(
    callee_name: &str,
    callee_module: Option<&str>,
    module_lookup: &ModuleLookup,
    parse_results: &[(PathBuf, Arc<crate::parsers::ParseResult>)],
    global_func_map: &std::collections::HashMap<String, String>,
) -> Option<String> {
    if let Some(module) = callee_module {
        let candidates = module_lookup.by_stem.get(module)?;
        for (_file_path, idx) in candidates {
            let (_, other_result) = parse_results.get(*idx)?;
            if let Some(func) = other_result
                .functions
                .iter()
                .find(|f| f.name == callee_name)
            {
                return Some(func.qualified_name.clone());
            }
        }
    }
    global_func_map.get(callee_name).cloned()
}


fn first_other_file(candidates: Option<&Vec<(String, usize)>>, exclude: &str) -> Option<String> {
    candidates?.iter().find(|(p, _)| p != exclude).map(|(p, _)| p.clone())
}


pub(super) fn build_import_edges_fast(
    edges: &mut Vec<(String, String, CodeEdge)>,
    result: &ParseResult,
    relative_str: &str,
    module_lookup: &ModuleLookup,
) {
    for import_info in &result.imports {
        let clean_import = import_info
            .path
            .trim_start_matches("./")
            .trim_start_matches("../")
            .trim_start_matches("crate::")
            .trim_start_matches("super::");

        let module_parts: Vec<&str> = clean_import.split("::").collect();
        let first_module = module_parts.first().copied().unwrap_or("");
        let python_path = clean_import.replace('.', "/");

        // Try fast lookup paths in order of specificity
        let matched_file = first_other_file(module_lookup.by_pattern.get(clean_import), relative_str)
            .or_else(|| first_other_file(module_lookup.by_pattern.get(&python_path), relative_str))
            .or_else(|| first_other_file(module_lookup.by_stem.get(first_module), relative_str))
            .or_else(|| first_other_file(module_lookup.by_stem.get(clean_import), relative_str));

        if let Some(target_file) = matched_file {
            let mut import_edge = CodeEdge::imports();
            if import_info.is_type_only {
                import_edge = import_edge.with_type_only();
            }
            edges.push((relative_str.to_string(), target_file, import_edge));
        }
    }
}
