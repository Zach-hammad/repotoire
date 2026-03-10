#!/usr/bin/env python3
"""
Fix remaining &var.field patterns and format! patterns.
"""
import re
import os

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    var_patterns = r'(?:func|f|class_node|cls|node|class|callee|caller|method|file_node|target|source|child|parent|base|derived|n|c|dep|dependent|member|function|entry|method_node)'

    field_resolvers = {
        'file_path': 'path(i)',
        'qualified_name': 'qn(i)',
        'name': 'node_name(i)',
    }

    for field, resolver in field_resolvers.items():
        # Fix: &var.field -> var.RESOLVER (when used as &str)
        # Common patterns: function_call(&func.file_path), format!("...", &func.name)
        pattern = rf'&({var_patterns})\.{field}\b(?!\()'
        replacement = rf'\1.{resolver}'
        content = re.sub(pattern, replacement, content)

        # Fix: format!("...", func.name, ...) where func.name is Spur
        # Pattern: in format macro, func.field NOT followed by .method()
        # This is tricky because func.name could be in many contexts

        # Fix: func.field == "string" or "string" == func.field
        # Pattern: var.field == "..." -> var.RESOLVER == "..."
        pattern = rf'(\b{var_patterns})\.{field}\s*==\s*"'
        replacement = rf'\1.{resolver} == "'
        content = re.sub(pattern, replacement, content)

        pattern = rf'"\s*==\s*(\b{var_patterns})\.{field}\b(?!\()'
        replacement = rf'" == \1.{resolver}'
        content = re.sub(pattern, replacement, content)

        # Fix: func.field != "string"
        pattern = rf'(\b{var_patterns})\.{field}\s*!=\s*"'
        replacement = rf'\1.{resolver} != "'
        content = re.sub(pattern, replacement, content)

        # Fix: .contains(&var.field) where it was already partially fixed
        # Pattern: .contains(&Spur) -> needs the & removed since resolver returns &str
        # Actually the pattern should be: contains(var.RESOLVER) not contains(&var.RESOLVER)

        # Fix remaining .FIELD.as_str() patterns
        pattern = rf'(\b{var_patterns})\.{field}\.as_str\(\)'
        replacement = rf'\1.{resolver}'
        content = re.sub(pattern, replacement, content)

        # Fix: format!("...", var.field) where not followed by method call
        # This handles cases like format!("Dead function: {}", func.name)
        # Be careful not to match func.name() or func.name.method()
        # Match: var.field followed by ), ,, \n, or whitespace but not . or (
        pattern = rf'(\b{var_patterns})\.{field}(?=\s*[),\n])'
        # Only replace if not already using resolver
        if re.search(pattern, content):
            # Need to be more careful - only replace when in format!/println! context
            # or when used as a String value
            pass  # Skip this for safety

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False


def main():
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

    rs_files = []
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith('.rs'):
                rs_files.append(os.path.join(root, f))

    # Only process detector files, mcp, cli, predictive, git, scoring, classifier
    target_dirs = ['detectors', 'mcp', 'cli', 'predictive', 'git', 'scoring', 'classifier']

    fixed = 0
    for filepath in sorted(rs_files):
        rel = os.path.relpath(filepath, src_dir)
        if any(rel.startswith(d) for d in target_dirs):
            if fix_file(filepath):
                print(f"Fixed: {rel}")
                fixed += 1

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
