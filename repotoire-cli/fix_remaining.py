#!/usr/bin/env python3
"""
Fix remaining CompactNode migration errors across all consumer files.
This script handles several patterns:

1. Missing `let i = graph.interner();` in functions that use `.path(i)`, `.qn(i)`, etc.
2. StrKey fields used as String (e.g., `.qualified_name` in String contexts like HashSet<String>)
3. Various field access patterns that need resolution through the interner
"""
import re
import os
import subprocess
import sys

def get_errors():
    """Run cargo check and parse error locations."""
    result = subprocess.run(
        ['cargo', 'check'],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__))
    )
    return result.stderr

def find_missing_interner_files(errors):
    """Find files with E0425 'cannot find value `i`' errors."""
    files = {}
    for line in errors.split('\n'):
        if 'cannot find value `i`' in line:
            m = re.search(r'--> (src/\S+):(\d+)', line)
            if m:
                f = m.group(1)
                if f not in files:
                    files[f] = []
                files[f].append(int(m.group(2)))
    return files

def add_interner_to_file(filepath, error_lines):
    """Add `let i = graph.interner();` to functions that need it."""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    original = ''.join(lines)

    # For each error line, find the enclosing function and add `let i = graph.interner();`
    # Find all function boundaries
    functions = []
    brace_depth = 0
    func_start = None
    func_line = None

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # Detect function start
        if re.match(r'\s*(pub\s+)?(async\s+)?fn\s+', line):
            func_line = idx

        for ch in stripped:
            if ch == '{':
                if func_line is not None and func_start is None:
                    func_start = idx
                    brace_depth = 1
                    func_line = None
                elif func_start is not None:
                    brace_depth += 1
            elif ch == '}':
                if func_start is not None:
                    brace_depth -= 1
                    if brace_depth == 0:
                        functions.append((func_start, idx))
                        func_start = None

    # For each error line, find which function it's in
    insertions = set()
    for err_line in error_lines:
        err_idx = err_line - 1  # 0-indexed
        for func_start, func_end in functions:
            if func_start <= err_idx <= func_end:
                # Check if `let i = graph.interner();` already exists in this function
                func_content = ''.join(lines[func_start:func_end+1])
                if 'let i = graph.interner()' in func_content or 'let i = self.graph.interner()' in func_content:
                    break

                # Find the right insertion point - first line after opening brace
                insert_idx = func_start + 1

                # Determine the graph variable name from function signature
                func_sig = ''.join(lines[max(0,func_start-5):func_start+2])

                graph_var = None
                if 'graph: &GraphStore' in func_sig or 'graph: &dyn GraphQuery' in func_sig or 'graph: &crate::graph::GraphStore' in func_sig:
                    graph_var = 'graph'
                elif 'state: &mut HandlerState' in func_sig:
                    # Skip - handled differently
                    break
                elif '&self' in func_sig or '&mut self' in func_sig:
                    # Check if self has a graph field
                    if 'self.graph' in func_content:
                        graph_var = 'self.graph'
                    elif 'self.store' in func_content:
                        graph_var = 'self.store'
                    else:
                        # Check for graph() method
                        if 'graph()' in func_content or '.graph.' in func_content:
                            graph_var = None  # Can't determine
                        break
                else:
                    # Look for graph variable in function body
                    graph_match = re.search(r'\blet\s+graph\s*=', func_content)
                    if graph_match:
                        graph_var = 'graph'
                    elif 'state.graph()' in func_content:
                        # Find where graph is obtained
                        graph_var = None  # Will be found later
                        break
                    else:
                        break

                if graph_var:
                    # Determine indentation
                    indent = '    '
                    for line in lines[insert_idx:min(insert_idx+5, len(lines))]:
                        if line.strip():
                            indent = re.match(r'(\s*)', line).group(1)
                            break

                    insertions.add((insert_idx, f'{indent}let i = {graph_var}.interner();\n'))
                break

    if not insertions:
        return False

    # Apply insertions (in reverse order to preserve line numbers)
    for idx, text in sorted(insertions, reverse=True):
        lines.insert(idx, text)

    new_content = ''.join(lines)
    if new_content != original:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    return False


def fix_strkey_as_string(filepath):
    """Fix patterns where StrKey fields are used in String contexts."""
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # Pattern: HashMap<String, ...> or HashSet<String> being populated with .qualified_name (which is StrKey)
    # This is complex and needs manual review for each file

    # Pattern: .clone() on StrKey fields - StrKey is Copy, so .clone() is fine but sometimes
    # it's used where a String is expected

    # Pattern: func.qualified_name used where &str is expected
    # In many detectors, the fix_refs.py incorrectly replaced &func.qualified_name with func.qn(i)
    # where the original was used as a StrKey key in HashSet/HashMap
    # This needs to be handled case by case

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False


def main():
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

    print("Running cargo check to find errors...")
    errors = get_errors()

    # Fix 1: Add missing interner bindings
    missing_files = find_missing_interner_files(errors)
    print(f"\nFound {len(missing_files)} files with missing interner:")

    fixed = 0
    for filepath, error_lines in sorted(missing_files.items()):
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        if os.path.exists(full_path):
            if add_interner_to_file(full_path, error_lines):
                print(f"  Fixed: {filepath} (lines: {error_lines})")
                fixed += 1
            else:
                print(f"  Skipped: {filepath} (could not determine graph variable)")

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
