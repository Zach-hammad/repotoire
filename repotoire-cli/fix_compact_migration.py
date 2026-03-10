#!/usr/bin/env python3
"""
Mechanical fixer for CompactNode migration.
Handles the most common patterns across all detector files.
"""
import re
import sys
import os

def fix_file(filepath):
    """Apply mechanical fixes to a single file."""
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # Track if we need to add `let i = graph.interner();`
    needs_interner = False

    # Pattern: func.file_path.contains("...") -> func.path(i).contains("...")
    # Also handle &func.file_path where &str is expected
    # Pattern: func.name.contains/starts_with/ends_with/to_lowercase etc.
    # Pattern: func.qualified_name.contains/starts_with etc.
    # Pattern: func.file_path.clone() -> func.path(i).to_string()
    # Pattern: func.qualified_name.clone() -> func.qn(i).to_string()
    # Pattern: func.name.clone() -> func.node_name(i).to_string()

    # These are the string method calls on StrKey fields that need resolution
    str_methods = [
        'contains', 'starts_with', 'ends_with', 'to_lowercase', 'to_uppercase',
        'as_str', 'split', 'rsplit', 'rsplit_once', 'is_empty', 'chars',
        'len', 'trim', 'replace', 'find', 'rfind',
    ]

    # Map of field -> resolver method
    field_resolvers = {
        'file_path': 'path(i)',
        'qualified_name': 'qn(i)',
        'name': 'node_name(i)',
    }

    # Fix: var.FIELD.METHOD(...) -> var.RESOLVER.METHOD(...)
    # where var could be func, f, class, cls, node, etc.
    var_patterns = r'(?:func|f|class_node|cls|node|class|callee|caller|method|file_node|target|source|child|parent|base|derived|n|c|dep|dependent|member)'

    for field, resolver in field_resolvers.items():
        for method in str_methods:
            # Pattern: var.field.method(
            pattern = rf'(\b(?:{var_patterns}))\.{field}\.({method})\b'
            replacement = rf'\1.{resolver}.{method}'
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)
                needs_interner = True

        # Pattern: var.field.clone() -> var.RESOLVER.to_string()
        pattern = rf'(\b(?:{var_patterns}))\.{field}\.clone\(\)'
        if re.search(pattern, content):
            content = re.sub(pattern, rf'\1.{resolver}.to_string()', content)
            needs_interner = True

        # Pattern: &var.field where &str is expected (in function calls, not struct construction)
        # This is tricky - we can't blindly replace all occurrences
        # For now, handle common patterns like format!("...", &func.file_path)
        # and function_call(&func.file_path)

    # Fix: PathBuf::from(&func.file_path) -> PathBuf::from(func.path(i))
    for field, resolver in field_resolvers.items():
        pattern = rf'PathBuf::from\(&(\b(?:{var_patterns}))\.{field}\)'
        if re.search(pattern, content):
            content = re.sub(pattern, rf'PathBuf::from(\1.{resolver})', content)
            needs_interner = True

    # Fix: func.file_path.clone().into() -> PathBuf::from(func.path(i))
    for field, resolver in field_resolvers.items():
        pattern = rf'(\b(?:{var_patterns}))\.{field}\.clone\(\)\.into\(\)'
        if re.search(pattern, content):
            content = re.sub(pattern, rf'PathBuf::from(\1.{resolver})', content)
            needs_interner = True

    # Fix: format!("...", func.name) where func.name is Spur
    # This is harder to do mechanically without false positives
    # Skip for now - manual fixes needed

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False


def main():
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

    # Find all .rs files
    rs_files = []
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith('.rs'):
                rs_files.append(os.path.join(root, f))

    fixed = 0
    for filepath in sorted(rs_files):
        if fix_file(filepath):
            rel = os.path.relpath(filepath, os.path.dirname(src_dir))
            print(f"Fixed: {rel}")
            fixed += 1

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
