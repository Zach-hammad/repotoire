#!/usr/bin/env python3
"""
Batch fix remaining CompactNode migration errors.
Handles:
1. Missing `let i = graph.interner();` in functions
2. contexts.get(func.qn(i)) -> contexts.get(&func.qualified_name)
3. Various StrKey-as-String patterns
"""
import re
import os
import subprocess

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

def get_error_files():
    """Get files with E0425 errors from cargo check."""
    result = subprocess.run(
        ['cargo', 'check'], capture_output=True, text=True,
        cwd=os.path.dirname(os.path.abspath(__file__))
    )

    files = {}
    lines = result.stderr.split('\n')
    pending_e0425 = False

    for line in lines:
        if "cannot find value `i` in this scope" in line:
            pending_e0425 = True
        elif pending_e0425 and '--> src/' in line:
            m = re.search(r'--> (src/[^:]+):(\d+)', line)
            if m:
                f = m.group(1)
                ln = int(m.group(2))
                if f not in files:
                    files[f] = []
                files[f].append(ln)
            pending_e0425 = False
        elif line.startswith('error') and 'E0425' not in line:
            pending_e0425 = False

    return files


def fix_missing_interner(filepath, error_lines):
    """Add let i = graph.interner(); to functions missing it."""
    with open(filepath, 'r') as f:
        content = f.read()
        lines = content.split('\n')

    original = content

    # Find function/method boundaries with their line numbers
    # We look for fn declarations and track brace depth
    func_defs = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match function declaration (handles multi-line signatures)
        if re.match(r'\s*(pub(\(crate\))?\s+)?(unsafe\s+)?(async\s+)?fn\s+\w+', line):
            # Find opening brace
            sig_start = i
            brace_found = False
            sig_lines = []
            j = i
            while j < len(lines):
                sig_lines.append(lines[j])
                if '{' in lines[j]:
                    brace_found = True
                    break
                j += 1

            if brace_found:
                sig_text = '\n'.join(sig_lines)
                # Get indent level
                indent = re.match(r'(\s*)', lines[j]).group(1)
                # j is the line with the opening brace
                func_defs.append((sig_start, j, sig_text, indent))
            i = j + 1
        else:
            i += 1

    # For each error line, find its enclosing function
    insertions = {}  # line_after_brace -> (indent, graph_var)

    for err_line in error_lines:
        err_idx = err_line - 1  # 0-indexed

        # Find the function this error is in
        best_func = None
        for sig_start, brace_line, sig_text, indent in func_defs:
            if sig_start <= err_idx:
                # Check this is the innermost function
                if best_func is None or sig_start > best_func[0]:
                    best_func = (sig_start, brace_line, sig_text, indent)

        if best_func is None:
            continue

        sig_start, brace_line, sig_text, indent = best_func
        insert_line = brace_line + 1  # Line after opening brace (0-indexed)

        # Check if interner already exists in this function
        # Search from insert_line until we find a matching closing brace
        func_body_start = insert_line
        func_body = '\n'.join(lines[func_body_start:min(func_body_start+200, len(lines))])

        if 'let i = graph.interner()' in func_body or 'let i = self.graph.interner()' in func_body:
            continue

        # Skip if already scheduled for insertion
        if insert_line in insertions:
            continue

        # Determine graph variable from signature
        graph_var = None
        if 'graph: &dyn' in sig_text or 'graph: &GraphStore' in sig_text or 'graph: &crate::graph' in sig_text:
            graph_var = 'graph'
        elif '&self' in sig_text or '&mut self' in sig_text:
            # Check for self.graph in function body
            if 'self.graph' in func_body:
                graph_var = 'self.graph'

        if graph_var is None:
            # Try to find graph variable in the function body
            m = re.search(r'let\s+graph\s*=\s*', func_body)
            if m:
                graph_var = 'graph'

        if graph_var:
            # Determine indentation for inserted line
            inner_indent = indent + '    '
            # Check first non-empty line after brace for actual indent
            for k in range(insert_line, min(insert_line + 5, len(lines))):
                if lines[k].strip():
                    inner_indent = re.match(r'(\s*)', lines[k]).group(1)
                    break

            insertions[insert_line] = (inner_indent, graph_var)

    if not insertions:
        return False

    # Apply insertions in reverse order
    new_lines = list(lines)
    for line_idx in sorted(insertions.keys(), reverse=True):
        inner_indent, graph_var = insertions[line_idx]
        new_lines.insert(line_idx, f'{inner_indent}let i = {graph_var}.interner();')

    new_content = '\n'.join(new_lines)
    if new_content != original:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    return False


def fix_contexts_get_pattern():
    """Fix contexts.get(func.qn(i)) -> contexts.get(&func.qualified_name) across all files.
    FunctionContextMap is HashMap<StrKey, ...>, so lookup should use StrKey not &str."""

    var_patterns = r'(?:func|f|class_node|cls|node|class|callee|caller|method|target|source|child|parent|n|c|member|function|entry)'

    fixed = 0
    for root, dirs, files in os.walk(SRC):
        # Skip parsers
        rel = os.path.relpath(root, SRC)
        if rel.startswith('parsers') or rel.startswith('values'):
            continue

        for fname in files:
            if not fname.endswith('.rs'):
                continue
            filepath = os.path.join(root, fname)
            with open(filepath, 'r') as f:
                content = f.read()

            original = content

            # Fix: contexts.get(func.qn(i)) -> contexts.get(&func.qualified_name)
            # The FunctionContextMap uses StrKey keys
            content = re.sub(
                rf'contexts\.get\(({var_patterns})\.qn\(i\)\)',
                r'contexts.get(&\1.qualified_name)',
                content
            )

            # Fix: contexts.get(func.path(i)) -> contexts.get(&func.file_path) (if used as StrKey key)
            # This is less common but handle it

            # Fix: qn_to_idx.get(func.qn(i)) -> qn_to_idx.get(&func.qualified_name)
            # qn_to_idx uses StrKey keys
            content = re.sub(
                rf'qn_to_idx\.get\(({var_patterns})\.qn\(i\)\)',
                r'qn_to_idx.get(&\1.qualified_name)',
                content
            )

            if content != original:
                with open(filepath, 'w') as f:
                    f.write(content)
                print(f"  Fixed contexts.get pattern in {os.path.relpath(filepath, SRC)}")
                fixed += 1

    return fixed


def fix_strkey_clone_pattern():
    """Fix .qualified_name.clone() and .file_path.clone() where they're used as StrKey
    (StrKey is Copy, so .clone() returns StrKey, but if it's used where String is expected,
    we need .qn(i).to_string() instead).

    Also fix patterns where StrKey is directly used as String in HashSet<String>/HashMap<String,...>.
    """
    var_patterns = r'(?:func|f|class_node|cls|node|class|callee|caller|method|target|source|child|parent|n|c|dep|dependent|member|function|entry|method_node|file_node|importer)'

    fixed = 0
    for root, dirs, files in os.walk(SRC):
        rel = os.path.relpath(root, SRC)
        if rel.startswith('parsers') or rel.startswith('values'):
            continue

        for fname in files:
            if not fname.endswith('.rs'):
                continue
            filepath = os.path.join(root, fname)
            with open(filepath, 'r') as f:
                content = f.read()

            original = content

            # Fix: n.file_path used in json!() macro - needs resolution
            # These were partially handled by previous scripts

            # Fix: HashMap/HashSet operations where StrKey is used but String is expected
            # Pattern: .insert(func.qualified_name) where the map is HashMap<String,...>
            # This is too context-dependent to automate safely

            if content != original:
                with open(filepath, 'w') as f:
                    f.write(content)
                print(f"  Fixed StrKey pattern in {os.path.relpath(filepath, SRC)}")
                fixed += 1

    return fixed


def main():
    print("=" * 60)
    print("Step 1: Fix contexts.get(func.qn(i)) patterns")
    print("=" * 60)
    n = fix_contexts_get_pattern()
    print(f"Fixed {n} files\n")

    print("=" * 60)
    print("Step 2: Fix missing interner bindings")
    print("=" * 60)
    error_files = get_error_files()
    print(f"Found {len(error_files)} files with missing 'i' variable:")

    fixed = 0
    for filepath, error_lines in sorted(error_files.items()):
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        if os.path.exists(full_path):
            if fix_missing_interner(full_path, error_lines):
                print(f"  Fixed: {filepath} ({len(error_lines)} errors)")
                fixed += 1
            else:
                print(f"  Skipped: {filepath} ({len(error_lines)} errors)")

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
