#!/usr/bin/env python3
"""
Add `let i = graph.interner();` to functions that take a `graph` parameter
and use `.path(i)`, `.qn(i)`, `.node_name(i)`, or `.lang(i)`.
"""
import re
import os
import sys

def fix_file(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()

    content = ''.join(lines)

    # Check if file uses .path(i), .qn(i), etc.
    if not re.search(r'\.(path|qn|node_name|lang)\(i\)', content):
        return False

    modified = False
    new_lines = list(lines)

    # Find function signatures that have `graph` parameter
    # Pattern: fn something(...graph: &dyn ...GraphQuery...) ... {
    fn_pattern = re.compile(r'^\s*(?:pub\s+)?fn\s+\w+.*graph.*\{')
    graph_param_pattern = re.compile(r'graph\s*:\s*&')

    insertions = []  # (line_idx, indent, line_to_insert)

    idx = 0
    while idx < len(new_lines):
        line = new_lines[idx]

        # Check for function signature with graph parameter
        # May span multiple lines
        if re.search(r'^\s*(?:pub\s+)?fn\s+\w+', line) and 'graph' in ''.join(new_lines[idx:min(idx+5, len(new_lines))]):
            # Collect the full signature (may span lines)
            sig_lines = [line]
            sig_end = idx
            brace_found = False
            while sig_end < len(new_lines) - 1:
                if '{' in new_lines[sig_end]:
                    brace_found = True
                    break
                sig_end += 1
                sig_lines.append(new_lines[sig_end])

            if not brace_found:
                idx += 1
                continue

            full_sig = ''.join(sig_lines)

            # Verify it has a graph parameter
            if not graph_param_pattern.search(full_sig):
                idx += 1
                continue

            # Check if the function body already has `let i = graph.interner()`
            # Look at next ~5 lines after the brace
            body_start = sig_end + 1
            already_has = False
            for check_idx in range(body_start, min(body_start + 6, len(new_lines))):
                if 'graph.interner()' in new_lines[check_idx]:
                    already_has = True
                    break

            if already_has:
                idx = body_start
                continue

            # Check if the function body uses .path(i), .qn(i), etc.
            # Scan forward to find the body (approximate - look at next 300 lines max)
            body_end = min(body_start + 300, len(new_lines))
            body_text = ''.join(new_lines[body_start:body_end])

            if re.search(r'\.(path|qn|node_name|lang)\(i\)', body_text):
                # Determine indent from first non-empty body line
                indent = '        '  # default
                for check_idx in range(body_start, min(body_start + 5, len(new_lines))):
                    stripped = new_lines[check_idx].rstrip()
                    if stripped.strip():
                        indent = re.match(r'^(\s*)', stripped).group(1)
                        break

                insertions.append((body_start, indent))
                modified = True

            idx = body_start
        else:
            idx += 1

    # Apply insertions in reverse order to preserve line numbers
    for line_idx, indent in reversed(insertions):
        new_lines.insert(line_idx, f'{indent}let i = graph.interner();\n')

    if modified:
        with open(filepath, 'w') as f:
            f.writelines(new_lines)

    return modified


def main():
    files = [
        "src/detectors/ai_boilerplate.rs",
        "src/detectors/ai_churn.rs",
        "src/detectors/ai_complexity_spike.rs",
        "src/detectors/architectural_bottleneck.rs",
        "src/detectors/boolean_trap.rs",
        "src/detectors/callback_hell.rs",
        "src/detectors/class_context.rs",
        "src/detectors/cleartext_credentials.rs",
        "src/detectors/commented_code.rs",
        "src/detectors/core_utility.rs",
        "src/detectors/data_clumps.rs",
        "src/detectors/dead_store.rs",
        "src/detectors/deep_nesting.rs",
        "src/detectors/detector_context.rs",
        "src/detectors/django_security.rs",
        "src/detectors/engine.rs",
        "src/detectors/feature_envy.rs",
        "src/detectors/function_context.rs",
        "src/detectors/god_class.rs",
        "src/detectors/hierarchical_surprisal.rs",
        "src/detectors/implicit_coercion.rs",
        "src/detectors/inconsistent_returns.rs",
        "src/detectors/insecure_deserialize.rs",
        "src/detectors/insecure_random.rs",
        "src/detectors/large_files.rs",
        "src/detectors/lazy_class.rs",
        "src/detectors/long_methods.rs",
        "src/detectors/long_parameter.rs",
        "src/detectors/message_chain.rs",
        "src/detectors/middle_man.rs",
        "src/detectors/missing_await.rs",
        "src/detectors/missing_docstrings.rs",
        "src/detectors/mutable_default_args.rs",
        "src/detectors/n_plus_one.rs",
        "src/detectors/prototype_pollution.rs",
        "src/detectors/refused_bequest.rs",
        "src/detectors/regex_dos.rs",
        "src/detectors/regex_in_loop.rs",
        "src/detectors/secrets.rs",
        "src/detectors/shotgun_surgery.rs",
        "src/detectors/single_char_names.rs",
        "src/detectors/string_concat_loop.rs",
        "src/detectors/surprisal.rs",
        "src/detectors/sync_in_async.rs",
        "src/detectors/taint/centralized.rs",
        "src/detectors/todo_scanner.rs",
        "src/detectors/wildcard_imports.rs",
        "src/git/enrichment.rs",
        "src/mcp/tools/graph_queries.rs",
        "src/predictive/mod.rs",
    ]

    src_dir = os.path.dirname(os.path.abspath(__file__))
    fixed = 0
    for f in files:
        filepath = os.path.join(src_dir, f)
        if os.path.exists(filepath) and fix_file(filepath):
            print(f"Fixed: {f}")
            fixed += 1

    print(f"\nFixed {fixed} files")


if __name__ == '__main__':
    main()
