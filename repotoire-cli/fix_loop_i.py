#!/usr/bin/env python3
"""Fix 'for i in' loops that conflict with 'let i = graph.interner()'.
Renames the loop variable to 'idx'."""
import re
import os

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    # Find functions that have both 'let i = ... .interner()' and 'for i in'
    # We need to rename 'for i in' to 'for idx in' within those functions

    lines = content.split('\n')
    new_lines = list(lines)

    # Find ranges of functions that use interner
    i = 0
    while i < len(lines):
        line = lines[i]
        # Look for 'let i = ...interner()' or 'let i = graph.interner()'
        if re.match(r'\s*let i = .*\.interner\(\);?\s*$', line):
            # Found interner declaration. Find the enclosing function boundaries
            # Find function start (backwards)
            func_start = i
            brace_depth = 0
            for j in range(i, -1, -1):
                if re.match(r'\s*(pub(\(crate\))?\s+)?(unsafe\s+)?(async\s+)?fn\s+\w+', lines[j]):
                    func_start = j
                    break

            # Find function end (forward from func_start, counting braces)
            in_func = False
            depth = 0
            func_end = len(lines) - 1
            for j in range(func_start, len(lines)):
                for ch in lines[j]:
                    if ch == '{':
                        in_func = True
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if in_func and depth == 0:
                            func_end = j
                            break
                if in_func and depth == 0:
                    break

            # Now look for 'for i in' within this function range
            for j in range(func_start, func_end + 1):
                if j == i:
                    continue  # Skip the interner line itself

                # Fix 'for i in 0..' patterns
                if re.search(r'\bfor\s+i\s+in\b', lines[j]):
                    new_lines[j] = re.sub(r'\bfor\s+i\s+in\b', 'for idx in', lines[j])
                    # Also need to rename uses of 'i' within the loop body
                    # Find the loop body range
                    loop_depth = 0
                    loop_started = False
                    for k in range(j, func_end + 1):
                        for ci, ch in enumerate(lines[k]):
                            if ch == '{':
                                loop_started = True
                                loop_depth += 1
                            elif ch == '}':
                                loop_depth -= 1
                                if loop_started and loop_depth == 0:
                                    break
                        if loop_started and loop_depth == 0:
                            # Rename 'i' to 'idx' within the loop body (j to k)
                            for m in range(j + 1, k + 1):
                                # Only rename standalone 'i' (not inside identifiers)
                                # Replace patterns like [i], (i), i], , i), format!("..{i}.."), etc.
                                new_lines[m] = re.sub(r'\bi\b(?!ntern|mport|f\b|s_|n_|terator|ter|nto|gnore)', 'idx', new_lines[m])
                            break
        i += 1

    new_content = '\n'.join(new_lines)
    if new_content != original:
        with open(filepath, 'w') as f:
            f.write(new_content)
        return True
    return False


# Target files with known conflicts
target_files = [
    'src/detectors/generator_misuse.rs',
    'src/detectors/infinite_loop.rs',
    'src/detectors/insecure_random.rs',
    'src/detectors/jwt_weak.rs',
    'src/detectors/missing_await.rs',
    'src/detectors/mutable_default_args.rs',
    'src/detectors/nosql_injection.rs',
    'src/detectors/prototype_pollution.rs',
    'src/detectors/unhandled_promise.rs',
    'src/detectors/xxe.rs',
    'src/git/enrichment.rs',
    'src/predictive/mod.rs',
]

fixed = 0
for f in target_files:
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
    if os.path.exists(filepath):
        if fix_file(filepath):
            print(f"Fixed: {f}")
            fixed += 1
        else:
            print(f"Skipped: {f}")

print(f"\nFixed {fixed} files")
