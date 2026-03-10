#!/usr/bin/env python3
"""Fix remaining 60+ errors - targeted per-file fixes."""

import re
import os
import subprocess

os.chdir('/home/zhammad/personal/repotoire/repotoire-cli')

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

fixed = []

def fix(filepath, old, new):
    content = read_file(filepath)
    if old in content:
        content = content.replace(old, new)
        write_file(filepath, content)
        if filepath not in fixed:
            fixed.append(filepath)
        return True
    return False

# ============================================================
# data_clumps.rs - FuncInfo doesn't have .qn() or .node_name()
# It has .name: String, .qualified_name: String, .params: Vec<ParamInfo>
# ============================================================
# These were already partially fixed but the replacements were wrong
# FuncInfo has qualified_name: String field
fix('src/detectors/data_clumps.rs',
    'f.qn(crate::graph::interner::global_interner())',
    'f.qualified_name.as_str()')
fix('src/detectors/data_clumps.rs',
    'func.qn(crate::graph::interner::global_interner())',
    'func.qualified_name.as_str()')
fix('src/detectors/data_clumps.rs',
    'f.node_name(crate::graph::interner::global_interner())',
    'f.name.as_str()')

# Let me check what FuncInfo looks like
# It probably has:
# - name: String
# - qualified_name: String
# - params: Vec<ParamInfo>
# - file_path: String

# ============================================================
# duplicate_code.rs - probably uses StrKey in format/display
# ============================================================
# Need to read and understand the error

# ============================================================
# function_context.rs - multiple errors
# ============================================================
# Already fixed qualified_name: qn.clone() -> qn.to_string()
# Need to fix calculate_call_depths and .get(qn)

# ============================================================
# Various detectors with .find_function_at returning CodeNode
# containing_func maps to f.name which is StrKey
# Used in Display context -> need to resolve
# ============================================================

# These detectors all have the pattern:
# let containing_func = graph.find_function_at(&path_str, line_num).map(|f| f.name);
# then: format!("...", func)  where func is StrKey
# The fix_display_v3 script should have caught most, but missed those where
# the variable name is `func` used in a let Some(func) = ...

# Pattern: these need global_interner to resolve the StrKey in format
# Let me find files that have this pattern

detectors_with_containing_func = [
    'src/detectors/hardcoded_ips.rs',
    'src/detectors/hardcoded_timeout.rs',
    'src/detectors/insecure_cookie.rs',
    'src/detectors/insecure_deserialize.rs',
    'src/detectors/insecure_random.rs',
    'src/detectors/nosql_injection.rs',
    'src/detectors/secrets.rs',
    'src/detectors/sync_in_async.rs',
    'src/detectors/unhandled_promise.rs',
    'src/detectors/xxe.rs',
    'src/detectors/unreachable_code.rs',
    'src/detectors/prototype_pollution.rs',
    'src/detectors/n_plus_one.rs',
    'src/detectors/empty_catch.rs',
    'src/detectors/todo_scanner.rs',
    'src/detectors/implicit_coercion.rs',
    'src/detectors/global_variables.rs',
    'src/detectors/django_security.rs',
    'src/detectors/regex_dos.rs',
    'src/detectors/large_files.rs',
]

for filepath in detectors_with_containing_func:
    if not os.path.exists(filepath):
        continue
    content = read_file(filepath)
    original = content

    # Pattern 1: .map(|f| f.name) returns StrKey, used in format!
    # Replace with .map(|f| f.node_name(interner).to_string())
    gi = 'crate::graph::interner::global_interner()'

    # Replace: graph.find_function_at(...).map(|f| f.name)
    # With: graph.find_function_at(...).map(|f| f.node_name(gi).to_string())
    content = re.sub(
        r'graph\.find_function_at\(([^)]+)\)\.map\(\|f\| f\.name\)',
        f'graph.find_function_at(\\1).map(|f| f.node_name({gi}).to_string())',
        content
    )

    # Also handle: graph.find_function_at(...).map(|f| (f.name, callers))
    content = re.sub(
        r'graph\.find_function_at\(([^)]+)\)\.map\(\|f\| \{\s*let callers',
        f'graph.find_function_at(\\1).map(|f| {{\n                            let callers',
        content
    )

    # Fix f.name used inside map closure with callers
    # Pattern: (f.name, callers) -> (f.node_name(gi).to_string(), callers)
    content = re.sub(
        r'\(f\.name, callers\)',
        f'(f.node_name({gi}).to_string(), callers)',
        content
    )

    # Pattern: .map(|f| f.name.clone()) -> .map(|f| f.node_name(gi).to_string())
    content = re.sub(
        r'\.map\(\|f\| f\.name\.clone\(\)\)',
        f'.map(|f| f.node_name({gi}).to_string())',
        content
    )

    if content != original:
        write_file(filepath, content)
        fixed.append(filepath)

# ============================================================
# feature_envy.rs - callee.file_path == *own_file (already attempted)
# ============================================================
# Check if the fix stuck - it might be a different pattern now

# ============================================================
# deep_nesting.rs - Vec<String> from StrKey iterator
# ============================================================

# ============================================================
# callback_hell.rs - f.path(i) == file_path and Vec<String> from StrKey
# ============================================================

# ============================================================
# surprisal.rs - remaining error
# ============================================================

# ============================================================
# classifier/debt.rs - file_path: path.clone()
# ============================================================

# ============================================================
# predictive/mod.rs - multiple StrKey issues
# ============================================================

# ============================================================
# graph/store_query.rs - 4 errors
# ============================================================

# ============================================================
# mcp/tools/evolution.rs - 2 errors
# ============================================================

# ============================================================
# mcp/tools/files.rs - 1 error
# ============================================================

print(f"Fixed {len(set(fixed))} files:")
for f in sorted(set(fixed)):
    print(f"  {f}")
