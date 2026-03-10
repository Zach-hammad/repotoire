#!/usr/bin/env python3
"""
Fix files where .node_name(i)/.qn(i)/.path(i) accidentally uses a loop counter 'i'
instead of the interner.

Strategy: In each function that has 'for i in' and '.node_name(i)', add
'let gi = graph.interner();' at the top and replace the calls to use 'gi'.
"""
import re
import os

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')

files = [
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

resolver_methods = ['.node_name(i)', '.qn(i)', '.path(i)', '.lang(i)']

fixed = 0
for f in files:
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
    if not os.path.exists(filepath):
        continue

    with open(filepath, 'r') as fh:
        content = fh.read()

    original = content

    # Check if any resolver method uses 'i' as parameter
    has_conflict = any(m in content for m in resolver_methods)
    has_for_i = 'for i in' in content

    if not has_conflict or not has_for_i:
        continue

    # Strategy: find functions that have BOTH 'for i in' and resolver methods
    # In those functions, replace the resolver calls to use a different interner variable

    lines = content.split('\n')

    # Find function ranges
    func_ranges = []
    func_starts = []
    for idx, line in enumerate(lines):
        if re.match(r'\s*(pub(\(crate\))?\s+)?(unsafe\s+)?(async\s+)?fn\s+\w+', line):
            func_starts.append(idx)

    # For each function, find its brace-delimited body
    for fs in func_starts:
        depth = 0
        started = False
        fe = None
        for j in range(fs, len(lines)):
            for ch in lines[j]:
                if ch == '{':
                    started = True
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if started and depth == 0:
                        fe = j
                        break
            if fe is not None:
                break
        if fe is not None:
            func_ranges.append((fs, fe))

    # For each function range, check if it has both 'for i in' and resolver methods
    for fs, fe in func_ranges:
        func_body = '\n'.join(lines[fs:fe+1])
        has_for_i_in_func = 'for i in' in func_body
        has_resolver_in_func = any(m in func_body for m in resolver_methods)

        if not has_for_i_in_func or not has_resolver_in_func:
            continue

        # This function has a conflict. Replace resolver methods to use 'gi' instead of 'i'
        for idx in range(fs, fe + 1):
            for m in resolver_methods:
                gi_m = m.replace('(i)', '(gi)')
                lines[idx] = lines[idx].replace(m, gi_m)

        # Also add 'let gi = graph.interner();' at the top of the function if not already there
        # Find the opening brace line
        for idx in range(fs, fe + 1):
            if '{' in lines[idx]:
                insert_after = idx
                break

        # Check if gi is already defined
        if 'let gi = ' not in func_body:
            # Determine indent
            indent = '    '
            for idx in range(insert_after + 1, min(insert_after + 5, fe + 1)):
                if lines[idx].strip():
                    indent = re.match(r'(\s*)', lines[idx]).group(1)
                    break

            # Check what graph variable to use
            func_sig = '\n'.join(lines[fs:insert_after+1])
            if 'graph: &dyn' in func_sig or 'graph: &' in func_sig:
                graph_var = 'graph'
            elif '&self' in func_sig:
                if 'self.graph' in func_body:
                    graph_var = 'self.graph'
                else:
                    graph_var = 'graph'  # fallback
            else:
                graph_var = 'graph'  # fallback

            lines.insert(insert_after + 1, f'{indent}let gi = {graph_var}.interner();')

    new_content = '\n'.join(lines)
    if new_content != original:
        with open(filepath, 'w') as fh:
            fh.write(new_content)
        print(f"Fixed: {f}")
        fixed += 1

print(f"\nFixed {fixed} files")
