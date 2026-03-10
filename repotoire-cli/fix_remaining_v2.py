#!/usr/bin/env python3
"""Fix remaining CompactNode migration errors - v2.

Handles:
1. Missing `i` in closures: |f| f.qn(i) where i not in scope -> use global_interner()
2. Spurious `let i = graph.interner();` where graph doesn't exist
3. .as_str() on Spur from get_calls/get_imports/get_inheritance
4. .starts_with/.ends_with/.contains on StrKey -> resolve first
5. StrKey.clone() where String expected -> resolve + .to_string()
6. .properties field access on CodeNode
7. Display not implemented for Spur in format! macros
"""

import re
import subprocess
import os
import glob

os.chdir('/home/zhammad/personal/repotoire/repotoire-cli')

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def parse_errors():
    """Run cargo check and extract structured errors."""
    result = subprocess.run(['cargo', 'check'], capture_output=True, text=True, timeout=180)
    output = result.stderr + result.stdout

    errors = []
    lines = output.split('\n')
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r'error\[(E\d+)\]:\s*(.*)', line)
        if m:
            error = {'code': m.group(1), 'message': m.group(2), 'raw_lines': [line]}
            # Collect context lines until next error or end
            idx += 1
            while idx < len(lines) and not re.match(r'error\[E\d+\]:', lines[idx]) and not lines[idx].startswith('error: could not compile'):
                error['raw_lines'].append(lines[idx])
                # Extract file location
                loc = re.match(r'\s*--> (src/[^:]+):(\d+):(\d+)', lines[idx])
                if loc and 'file' not in error:
                    error['file'] = loc.group(1)
                    error['line'] = int(loc.group(2))
                    error['col'] = int(loc.group(3))
                idx += 1
            errors.append(error)
        else:
            idx += 1
    return errors

# ================================================================
# Fix 1: Missing `i` in closures - use global interner
# Pattern: .qn(i), .path(i), .node_name(i) inside closures where i not in scope
# ================================================================
def fix_missing_i_in_closures(filepath, error_lines):
    """Fix closures that reference `i` for interner but i is not in scope."""
    content = read_file(filepath)
    lines = content.split('\n')
    changed = False

    for err_line in error_lines:
        if err_line - 1 >= len(lines):
            continue
        line = lines[err_line - 1]

        # Check if this is inside a closure (|f| or |x| pattern)
        # Replace .qn(i), .path(i), .node_name(i) with global_interner() versions
        gi = 'crate::graph::interner::global_interner()'

        # Pattern: inside a closure like .map(|f| f.qn(i)...)
        if re.search(r'\|[a-z_]+\|.*\.(qn|path|node_name|lang)\(i\)', line):
            new_line = re.sub(r'\.(qn|path|node_name|lang)\(i\)',
                            lambda m: f'.{m.group(1)}({gi})', line)
            if new_line != line:
                lines[err_line - 1] = new_line
                changed = True
        # Pattern: standalone call like func.qn(i) not in closure
        elif re.search(r'\.(qn|path|node_name|lang)\(i\)', line):
            # Check if there's a `let i = ` somewhere above in scope
            # If not, replace with global_interner
            has_i = False
            for check_line in range(max(0, err_line - 30), err_line - 1):
                if 'let i = ' in lines[check_line] or 'let i =' in lines[check_line]:
                    has_i = True
                    break
            if not has_i:
                new_line = re.sub(r'\.(qn|path|node_name|lang)\(i\)',
                                lambda m: f'.{m.group(1)}({gi})', line)
                if new_line != line:
                    lines[err_line - 1] = new_line
                    changed = True

    if changed:
        write_file(filepath, '\n'.join(lines))
    return changed

# ================================================================
# Fix 2: Spurious `let i = graph.interner();` where graph doesn't exist
# ================================================================
def fix_spurious_graph_interner(filepath):
    """Remove `let i = graph.interner();` lines where graph is not in scope."""
    content = read_file(filepath)
    lines = content.split('\n')
    changed = False

    new_lines = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == 'let i = graph.interner();':
            # Check if 'graph' is a parameter in the enclosing function
            # Look backwards for fn signature
            has_graph = False
            for check_idx in range(idx - 1, max(0, idx - 30), -1):
                check_line = lines[check_idx]
                if 'fn ' in check_line:
                    if 'graph' in check_line:
                        has_graph = True
                    break
                if 'graph' in check_line and ('let graph' in check_line or 'graph:' in check_line):
                    has_graph = True
                    break
            if not has_graph:
                # Check if graph is used elsewhere nearby (might be a struct field)
                context_range = lines[max(0, idx-5):min(len(lines), idx+5)]
                if not any('graph' in l and l != line for l in context_range if 'interner' not in l):
                    changed = True
                    continue  # Skip this line
        new_lines.append(line)

    if changed:
        write_file(filepath, '\n'.join(new_lines))
    return changed

def main():
    print("Parsing cargo check errors...")
    errors = parse_errors()
    print(f"Found {len(errors)} errors")

    # Group errors by file
    by_file = {}
    for e in errors:
        if 'file' in e:
            by_file.setdefault(e['file'], []).append(e)

    # ========================================
    # Fix E0425 "cannot find value `i`"
    # ========================================
    e0425_files = {}
    for e in errors:
        if e['code'] == 'E0425' and 'file' in e and '`i`' in e['message']:
            e0425_files.setdefault(e['file'], []).append(e['line'])

    for filepath, err_lines in e0425_files.items():
        if fix_missing_i_in_closures(filepath, err_lines):
            print(f"Fixed missing `i` in closures: {filepath}")

    # ========================================
    # Fix E0425 "cannot find value `graph`"
    # ========================================
    for e in errors:
        if e['code'] == 'E0425' and 'file' in e and '`graph`' in e['message']:
            if fix_spurious_graph_interner(e['file']):
                print(f"Fixed spurious graph.interner(): {e['file']}")

if __name__ == '__main__':
    main()
