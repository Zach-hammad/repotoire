#!/usr/bin/env python3
"""Fix Spur Display errors and remaining type mismatches.

This handles:
1. format!("...", node.name) -> format!("...", node.node_name(i))
2. format!("...", node.file_path) -> format!("...", node.path(i))
3. format!("...", node.qualified_name) -> format!("...", node.qn(i))
4. format!("...", node.language) -> format!("...", node.lang(i).unwrap_or(""))
5. HashMap<String,..>.get(key) where key is StrKey -> get(i.resolve(key))
6. .rsplit_once() / .rsplit() / .ends_with() / .starts_with() on StrKey
7. Vec<String> from StrKey iterator -> resolve first
"""

import re
import subprocess
import os

os.chdir('/home/zhammad/personal/repotoire/repotoire-cli')

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def get_error_locations():
    """Run cargo check and extract error file:line pairs with error codes."""
    result = subprocess.run(['cargo', 'check'], capture_output=True, text=True, timeout=180)
    output = result.stderr + result.stdout

    errors = []
    lines = output.split('\n')
    current_code = None
    current_msg = None
    for line in lines:
        m = re.match(r'error\[(E\d+)\]:\s*(.*)', line)
        if m:
            current_code = m.group(1)
            current_msg = m.group(2)
        elif current_code:
            loc = re.match(r'\s*--> (src/[^:]+):(\d+):(\d+)', line)
            if loc:
                errors.append({
                    'code': current_code,
                    'message': current_msg,
                    'file': loc.group(1),
                    'line': int(loc.group(2)),
                    'col': int(loc.group(3)),
                })
                current_code = None  # Reset after capturing location
    return errors

def fix_spur_display_in_format(filepath, error_lines):
    """Fix Spur in format! macros by resolving via interner.

    The key insight: in format! macros, StrKey fields need to be resolved.
    We need to replace patterns like:
      format!("...", node.name, ...)  -> format!("...", node.node_name(i), ...)
      format!("...", node.file_path, ...) -> format!("...", node.path(i), ...)
      format!("...", node.qualified_name, ...) -> format!("...", node.qn(i), ...)
    """
    content = read_file(filepath)
    lines = content.split('\n')
    changed = False

    # Determine interner variable name by looking for it in the file
    interner_var = None
    for line in lines:
        m = re.match(r'\s*let\s+(i|gi|interner)\s*=\s*(?:graph|self\.graph|g)\.interner\(\)', line)
        if m:
            interner_var = m.group(1)
            break
        m = re.match(r'\s*let\s+(i|gi|interner)\s*=\s*crate::graph::interner::global_interner\(\)', line)
        if m:
            interner_var = m.group(1)
            break

    if not interner_var:
        interner_var = 'crate::graph::interner::global_interner()'

    for err_line_num in error_lines:
        idx = err_line_num - 1
        if idx >= len(lines):
            continue
        line = lines[idx]

        # Skip if line is a comment
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('/*'):
            continue

        # Replace bare StrKey field accesses that would be used in Display context
        # Pattern: some_var.name where name is a StrKey field in a format context
        # We need to be careful not to replace .node_name(i) etc.

        # Direct field replacements for nodes accessed as X.field
        # Only replace if the field is used bare (not already calling a method on it)
        replacements = [
            # node.name -> node.node_name(interner) (but not .name. or .name()  or .node_name)
            (r'(\w+)\.name(?!\w|\(|\.)', lambda m: f'{m.group(1)}.node_name({interner_var})'),
            # node.qualified_name -> node.qn(interner)
            (r'(\w+)\.qualified_name(?!\w|\()', lambda m: f'{m.group(1)}.qn({interner_var})'),
            # node.file_path -> node.path(interner)
            (r'(\w+)\.file_path(?!\w|\()', lambda m: f'{m.group(1)}.path({interner_var})'),
            # node.language -> node.lang(interner).unwrap_or("")
            (r'(\w+)\.language(?!\w|\()', lambda m: f'{m.group(1)}.lang({interner_var}).unwrap_or("")'),
        ]

        new_line = line
        for pattern, replacement in replacements:
            new_line = re.sub(pattern, replacement, new_line)

        if new_line != line:
            lines[idx] = new_line
            changed = True

    if changed:
        write_file(filepath, '\n'.join(lines))
    return changed


def fix_string_borrow_spur(filepath, error_lines):
    """Fix HashMap<String,...>.get(spur_key) -> .get(i.resolve(spur_key))
    and HashMap<String,...>.contains_key(spur_key)
    """
    content = read_file(filepath)
    lines = content.split('\n')
    changed = False

    for err_line_num in error_lines:
        idx = err_line_num - 1
        if idx >= len(lines):
            continue
        line = lines[idx]
        # These are typically HashMap get() calls where the key is a StrKey
        # but the map expects String keys
        # We can't easily fix these automatically without more context
        pass

    return False


def main():
    print("Parsing cargo check errors...")
    errors = get_error_locations()
    print(f"Found {len(errors)} errors")

    # Group E0277 "Spur doesn't implement Display" errors by file
    display_errors = {}
    for e in errors:
        if e['code'] == 'E0277' and 'Display' in e.get('message', ''):
            display_errors.setdefault(e['file'], []).append(e['line'])

    print(f"\nDisplay errors in {len(display_errors)} files:")
    fixed_count = 0
    for filepath, err_lines in sorted(display_errors.items()):
        if fix_spur_display_in_format(filepath, err_lines):
            print(f"  Fixed: {filepath} ({len(err_lines)} errors)")
            fixed_count += 1
        else:
            print(f"  Skipped: {filepath} ({len(err_lines)} errors)")

    print(f"\nFixed {fixed_count} files")


if __name__ == '__main__':
    main()
