#!/usr/bin/env python3
"""Fix remaining CompactNode migration errors across the entire codebase.

This script handles:
1. E0425: Missing `i` variable - add `let i = graph.interner();` or use global_interner()
2. E0658: `.as_str()` on `&str` (unstable) - remove redundant `.as_str()`
3. E0599: `.as_str()` on Spur - resolve via interner first
4. E0599: `.starts_with()`, `.ends_with()`, `.contains()` on Spur - resolve via interner
5. E0277: Display not implemented for Spur - resolve via interner for format!
6. E0308: Type mismatch (StrKey vs String) - add conversions
7. E0609: `.properties` field on CodeNode - use get_str/get_i64 or ExtraProps
"""

import re
import subprocess
import sys
import os

os.chdir('/home/zhammad/personal/repotoire/repotoire-cli')

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def get_errors():
    """Run cargo check and parse errors."""
    result = subprocess.run(['cargo', 'check'], capture_output=True, text=True, timeout=180)
    return result.stderr + result.stdout

def parse_errors(output):
    """Parse error output into structured list."""
    errors = []
    current_error = None
    for line in output.split('\n'):
        m = re.match(r'error\[(E\d+)\]:', line)
        if m:
            current_error = {'code': m.group(1), 'message': line}
            errors.append(current_error)
        elif current_error and '-->' in line:
            m2 = re.match(r'\s*--> (src/[^:]+):(\d+):(\d+)', line)
            if m2 and 'file' not in current_error:
                current_error['file'] = m2.group(1)
                current_error['line'] = int(m2.group(2))
                current_error['col'] = int(m2.group(3))
    return errors

# ============================================================
# Pattern 1: Remove redundant .as_str() on &str results
# qn(i).as_str() -> qn(i) since qn() already returns &str
# path(i).as_str() -> path(i)
# node_name(i).as_str() -> node_name(i)
# lang(i).as_str() -> lang(i)
# ============================================================
def fix_redundant_as_str(content):
    """Remove .as_str() after calls that already return &str."""
    # These methods already return &str, so .as_str() is redundant
    patterns = [
        (r'\.qn\(([^)]+)\)\.as_str\(\)', r'.qn(\1)'),
        (r'\.path\(([^)]+)\)\.as_str\(\)', r'.path(\1)'),
        (r'\.node_name\(([^)]+)\)\.as_str\(\)', r'.node_name(\1)'),
        (r'\.lang\(([^)]+)\)\.as_str\(\)', r'.lang(\1)'),
    ]
    for pat, repl in patterns:
        content = re.sub(pat, repl, content)
    return content

# ============================================================
# Pattern 2: Fix .as_str() on StrKey (Spur) from get_calls/get_imports/get_inheritance
# These return Vec<(StrKey, StrKey)>, elements need i.resolve()
# caller.as_str() -> i.resolve(*caller) or i.resolve(caller)
# ============================================================

# ============================================================
# Pattern 3: Fix .starts_with(), .ends_with(), .contains() on StrKey
# m.name.starts_with('_') -> m.node_name(i).starts_with('_')
# ============================================================

# ============================================================
# Pattern 4: Fix missing interner in closures
# Inside .map(|f| f.qn(i)...) where i is not in scope
# ============================================================

def fix_file(filepath):
    """Apply all fixable patterns to a file."""
    content = read_file(filepath)
    original = content

    # Fix 1: Remove redundant .as_str() after interner resolution methods
    content = fix_redundant_as_str(content)

    if content != original:
        write_file(filepath, content)
        return True
    return False

def main():
    # First pass: fix the easy stuff (redundant .as_str())
    # Find all Rust source files
    import glob
    rs_files = glob.glob('src/**/*.rs', recursive=True)

    fixed_count = 0
    for f in rs_files:
        if fix_file(f):
            print(f"Fixed redundant .as_str(): {f}")
            fixed_count += 1

    print(f"\nFixed {fixed_count} files with redundant .as_str()")

if __name__ == '__main__':
    main()
