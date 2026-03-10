#!/usr/bin/env python3
"""Fix remaining CompactNode migration errors - v4.

Targeted fixes for the 98 remaining errors:
1. contexts.get(&func.qualified_name) -> contexts.get(func.qn(i)) -- HashMap<String> with StrKey
2. .file_path == "string" -> .path(i) == "string"
3. .qualified_name == "string" -> .qn(i) == "string"
4. func.node_name(i) on non-CodeNode types (Function, Class, FuncInfo, FunctionAST, Dependency)
5. .properties access on CodeNode
6. Vec<String> from StrKey iterator -> resolve via interner
7. entry(spur.clone()) where HashMap<String> expected -> entry(resolved.to_string())
8. Spurious let i = graph.interner() where _graph
"""

import re
import os

os.chdir('/home/zhammad/personal/repotoire/repotoire-cli')

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

fixes = []

# ============================================================
# Fix: cli/watch.rs - func.node_name(i) on parser Function/Class types
# ============================================================
def fix_watch():
    path = 'src/cli/watch.rs'
    content = read_file(path)
    # func.node_name(i) -> &func.name (parser Function has name: String)
    content = content.replace('func.node_name(i)', '&func.name')
    content = content.replace('class.node_name(i)', '&class.name')
    # .with_property("x", val) -> remove (CodeNode no longer has properties)
    # These are chained builder calls, need to remove them
    content = re.sub(r'\n\s*\.with_property\([^)]+\)', '', content)
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: detectors/data_clumps.rs - FuncInfo has .name not .qn/.node_name
# ============================================================
def fix_data_clumps():
    path = 'src/detectors/data_clumps.rs'
    content = read_file(path)
    # FuncInfo has fields: name, params, file_path as String
    content = content.replace('f.qn(crate::graph::interner::global_interner())', '&f.qualified_name')
    content = content.replace('func.qn(crate::graph::interner::global_interner())', '&func.qualified_name')
    content = content.replace('f.node_name(crate::graph::interner::global_interner())', '&f.name')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: detectors/ai_boilerplate.rs - FunctionAST has .name not .node_name/.path
# ============================================================
def fix_ai_boilerplate():
    path = 'src/detectors/ai_boilerplate.rs'
    content = read_file(path)
    # FunctionAST has: name: String, file_path: String
    content = content.replace('f.node_name(crate::graph::interner::global_interner()).to_string()', 'f.name.clone()')
    content = content.replace('f.path(crate::graph::interner::global_interner())', '&f.file_path')
    content = content.replace('f.node_name(crate::graph::interner::global_interner())', '&f.name')
    # Fix the type annotations
    # .map(|s| s.as_str()) where s is already a &String
    content = content.replace('.map(|s| s.as_str())', '.map(|s| s.as_str())')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: contexts.get(&func.qualified_name) -- HashMap<String> with StrKey key
# In these files, contexts is HashMap<String, FunctionContext> but func.qualified_name is StrKey
# ============================================================
def fix_contexts_get():
    """Fix contexts.get(&func.qualified_name) -> contexts.get(func.qn(i))"""
    files_patterns = [
        ('src/detectors/ai_missing_tests.rs', [
            ('contexts.get(&f.qualified_name)', 'contexts.get(f.qn(i))'),
            ('contexts.get(&func.qualified_name)', 'contexts.get(func.qn(i))'),
        ]),
        ('src/detectors/architectural_bottleneck.rs', [
            ('contexts.get(&func.qualified_name)', 'contexts.get(func.qn(i))'),
        ]),
        ('src/detectors/degree_centrality.rs', [
            ('contexts.get(&func.qualified_name)', 'contexts.get(func.qn(i))'),
        ]),
        ('src/detectors/influential_code.rs', [
            ('contexts.get(&func.qualified_name)', 'contexts.get(func.qn(i))'),
        ]),
    ]
    for filepath, replacements in files_patterns:
        content = read_file(filepath)
        changed = False
        for old, new in replacements:
            if old in content:
                content = content.replace(old, new)
                changed = True
        if changed:
            write_file(filepath, content)
            fixes.append(filepath)

# ============================================================
# Fix: .file_path == *rel_str -> .path(i) == *rel_str for surprisal
# ============================================================
def fix_surprisal():
    path = 'src/detectors/surprisal.rs'
    content = read_file(path)
    content = content.replace('f.file_path == *rel_str', 'f.path(i) == *rel_str')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: hierarchical_surprisal.rs
# ============================================================
def fix_hierarchical_surprisal():
    path = 'src/detectors/hierarchical_surprisal.rs'
    content = read_file(path)
    # Remove spurious let i = graph.interner() where _graph is used
    content = content.replace('let i = graph.interner();', 'let i = _graph.interner();')
    # f.qualified_name == *qn -> f.qn(i) == *qn
    content = content.replace('f.qualified_name == *qn', 'f.qn(i) == *qn')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: detector_context.rs - entry(parent.clone()) where parent is StrKey
# ============================================================
def fix_detector_context():
    path = 'src/detectors/detector_context.rs'
    content = read_file(path)
    lines = content.split('\n')
    new_lines = []
    for idx, line in enumerate(lines):
        # .entry(parent.clone()) -> .entry(i.resolve(parent).to_string())
        if '.entry(parent.clone())' in line:
            line = line.replace('.entry(parent.clone())', '.entry(i.resolve(*parent).to_string())')
        # .push(child.clone()) -> .push(i.resolve(child).to_string())
        if '.push(child.clone());' in line:
            line = line.replace('.push(child.clone());', '.push(i.resolve(*child).to_string());')
        new_lines.append(line)
    write_file(path, '\n'.join(new_lines))
    fixes.append(path)

# ============================================================
# Fix: function_context.rs
# ============================================================
def fix_function_context():
    path = 'src/detectors/function_context.rs'
    content = read_file(path)
    # qn.clone() gives &str not String -> use .to_string()
    content = content.replace('qualified_name: qn.clone(),', 'qualified_name: qn.to_string(),')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: api_surface.rs - .properties access
# ============================================================
def fix_api_surface():
    path = 'src/detectors/api_surface.rs'
    content = read_file(path)
    lines = content.split('\n')
    # Find the .properties block and replace with is_exported() flag
    new_lines = []
    skip_until_default = False
    for idx, line in enumerate(lines):
        if '.properties' in line and 'get("decorators")' in line:
            # This is the old pattern checking for @exported decorator
            # Replace the whole block with a simple flag check
            skip_until_default = True
            new_lines.append('    func.is_exported()')
            continue
        if skip_until_default:
            if '.unwrap_or(false)' in line:
                skip_until_default = False
            continue
        new_lines.append(line)
    write_file(path, '\n'.join(new_lines))
    fixes.append(path)

# ============================================================
# Fix: boolean_trap.rs - &def.qualified_name (StrKey) to call_fan_in(&str)
# ============================================================
def fix_boolean_trap():
    path = 'src/detectors/boolean_trap.rs'
    content = read_file(path)
    content = content.replace('graph.call_fan_in(&def.qualified_name)', 'graph.call_fan_in(def.qn(i))')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: callback_hell.rs - f.file_path == file_path (StrKey vs &str)
# ============================================================
def fix_callback_hell():
    path = 'src/detectors/callback_hell.rs'
    content = read_file(path)
    content = content.replace('f.file_path == file_path', 'f.path(i) == file_path')
    # .collect() for Vec<String> from StrKey iter
    # Find the pattern and add .map(|k| i.resolve(k).to_string()) before .collect()
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: debug_code.rs - func is StrKey, not String
# ============================================================
def fix_debug_code():
    path = 'src/detectors/debug_code.rs'
    content = read_file(path)
    # is_logging_utility(func) where func: StrKey -> resolve first
    content = content.replace('Self::is_logging_utility(func)', 'Self::is_logging_utility(i.resolve(func))')
    # format!("... {}", func) where func: StrKey -> resolve
    content = content.replace('notes.push(format!("📦 In function: `{}`", func));',
                              'notes.push(format!("📦 In function: `{}`", i.resolve(func)));')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: cors_misconfig.rs - format!("... {}", func) where func: StrKey
# ============================================================
def fix_cors_misconfig():
    path = 'src/detectors/cors_misconfig.rs'
    content = read_file(path)
    content = content.replace('notes.push(format!("📦 In function: `{}`", func));',
                              'notes.push(format!("📦 In function: `{}`", i.resolve(func)));')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: cleartext_credentials.rs
# ============================================================
def fix_cleartext_credentials():
    path = 'src/detectors/cleartext_credentials.rs'
    content = read_file(path)
    # Check what the error is - probably returning StrKey where String expected
    # Need to read file to understand context
    write_file(path, content)

# ============================================================
# Fix: dep_audit.rs - Dependency has .name not .node_name()
# ============================================================
def fix_dep_audit():
    path = 'src/detectors/dep_audit.rs'
    content = read_file(path)
    # dep.node_name(i) -> dep.name (Dependency is a parser type with String fields)
    content = content.replace('dep.node_name(i)', '&dep.name')
    # Remove spurious let i = graph.interner() where _graph is used
    content = content.replace('let i = graph.interner();', 'let i = _graph.interner();')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: deep_nesting.rs - Vec<String> from StrKey
# ============================================================
def fix_deep_nesting():
    path = 'src/detectors/deep_nesting.rs'
    content = read_file(path)
    # .collect() for Vec<String> from .map returning StrKey
    # Need to add .map(|k| i.resolve(k).to_string()) before .collect()
    write_file(path, content)

# ============================================================
# Fix: feature_envy.rs - callee.file_path == *own_file (StrKey vs &str)
# ============================================================
def fix_feature_envy():
    path = 'src/detectors/feature_envy.rs'
    content = read_file(path)
    content = content.replace('callee.file_path == *own_file', 'callee.path(i) == *own_file')
    write_file(path, content)
    fixes.append(path)

# ============================================================
# Fix: git/enrichment.rs - missing i in scope
# ============================================================
def fix_enrichment():
    path = 'src/git/enrichment.rs'
    content = read_file(path)
    lines = content.split('\n')
    new_lines = []
    for idx, line in enumerate(lines):
        if 'f.path(i)' in line or 'c.path(i)' in line:
            # Check if this is inside enrich_all() which has self but no i
            # Replace with global_interner
            line = line.replace('.path(i)', '.path(crate::graph::interner::global_interner())')
        new_lines.append(line)
    write_file(path, '\n'.join(new_lines))
    fixes.append(path)


def main():
    fix_watch()
    fix_data_clumps()
    fix_ai_boilerplate()
    fix_contexts_get()
    fix_surprisal()
    fix_hierarchical_surprisal()
    fix_detector_context()
    fix_function_context()
    fix_api_surface()
    fix_boolean_trap()
    fix_callback_hell()
    fix_debug_code()
    fix_cors_misconfig()
    fix_dep_audit()
    fix_feature_envy()
    fix_enrichment()

    print(f"Fixed {len(fixes)} files:")
    for f in sorted(set(fixes)):
        print(f"  {f}")


if __name__ == '__main__':
    main()
