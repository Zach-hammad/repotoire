#!/usr/bin/env python3
"""
Fix files where .node_name(i)/.qn(i)/.path(i)/.lang(i) accidentally uses
a loop counter 'i' instead of the interner.

For each affected line, replace the method call to use global_interner().
"""
import re
import os
import subprocess

def get_conflict_lines():
    """Get file:line pairs where StringInterner/usize conflict occurs."""
    result = subprocess.run(
        ['cargo', 'check'], capture_output=True, text=True,
        cwd=os.path.dirname(os.path.abspath(__file__))
    )

    lines_to_fix = {}
    stderr_lines = result.stderr.split('\n')
    pending_file = None

    for idx, line in enumerate(stderr_lines):
        if 'expected `&StringInterner`, found `usize`' in line:
            # Look backwards for the --> src/... line
            for j in range(idx, max(idx-8, -1), -1):
                m = re.search(r'--> (src/[^:]+):(\d+)', stderr_lines[j])
                if m:
                    f = m.group(1)
                    ln = int(m.group(2))
                    if f not in lines_to_fix:
                        lines_to_fix[f] = set()
                    lines_to_fix[f].add(ln)
                    break

    return lines_to_fix

def fix_file(filepath, error_lines):
    """For each error line, replace .node_name(i)/.qn(i)/.path(i)/.lang(i)
    with versions using global_interner()."""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    original = ''.join(lines)

    for ln in error_lines:
        idx = ln - 1  # 0-indexed
        if idx < 0 or idx >= len(lines):
            continue

        line = lines[idx]

        # Check if this line has a resolver method that uses (i) where i is a loop counter
        # Replace .node_name(i) -> .node_name(crate::graph::interner::global_interner())
        # But that's verbose. Better: add 'let gi = crate::graph::interner::global_interner();'
        # at the beginning of the enclosing block and use gi.

        # Simpler approach: just replace the specific call on this line
        for method in ['node_name', 'qn', 'path', 'lang']:
            pattern = rf'\.{method}\(i\)'
            if re.search(pattern, line):
                # Replace with global interner call
                line = re.sub(pattern, f'.{method}(crate::graph::interner::global_interner())', line)

        lines[idx] = line

    new_content = ''.join(lines)
    if new_content != original:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    return False


def main():
    conflict_lines = get_conflict_lines()
    print(f"Found {len(conflict_lines)} files with loop counter/interner conflicts:")

    fixed = 0
    for filepath, error_lines in sorted(conflict_lines.items()):
        full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filepath)
        if os.path.exists(full_path):
            if fix_file(full_path, error_lines):
                print(f"  Fixed: {filepath} ({len(error_lines)} lines)")
                fixed += 1

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
