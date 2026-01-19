# Re-export all functions from the compiled Rust module
from .repotoire_fast import (
    scan_files,
    hash_file_md5,
    batch_hash_files,
    calculate_complexity_fast,
    calculate_complexity_batch,
    calculate_complexity_files,
    calculate_lcom_fast,
    calculate_lcom_batch,
    cosine_similarity_fast,
    batch_cosine_similarity_fast,
    find_top_k_similar,
    # Pylint rules not covered by Ruff (individual checks)
    check_too_many_attributes,        # R0902
    check_too_few_public_methods,     # R0903
    check_import_self,                # R0401
    check_too_many_lines,             # C0302
    check_too_many_ancestors,         # R0901
    check_attribute_defined_outside_init,  # W0201
    check_protected_access,           # W0212
    check_unused_wildcard_import,     # W0614
    check_undefined_loop_variable,    # W0631
    check_disallowed_name,            # C0104
    # Combined checks (parse once - faster)
    check_all_pylint_rules,           # All rules, single file
    check_all_pylint_rules_batch,     # All rules, multiple files in parallel
    # Graph algorithms (FalkorDB migration - replaces Neo4j GDS)
    graph_find_sccs,                  # Strongly connected components
    graph_find_cycles,                # Circular dependencies
    graph_pagerank,                   # PageRank importance scores
    graph_betweenness_centrality,     # Betweenness centrality
    graph_leiden,                     # Leiden community detection
    graph_leiden_parallel,            # Leiden with parallel option (REPO-215)
    graph_harmonic_centrality,        # Harmonic centrality (closeness)
    # Duplicate code detection (REPO-166)
    find_duplicates,                  # Rabin-Karp duplicate detection
    find_duplicates_batch,            # Batch duplicate detection
    tokenize_source,                  # Tokenize source code
    PyDuplicateBlock,                 # Duplicate block result class
    # Link prediction for call resolution
    graph_validate_calls,             # Validate calls by community
    graph_rank_call_candidates,       # Rank call candidates
    graph_batch_jaccard,              # Batch Jaccard similarity
    # Graph-based code smell detectors (REPO-433)
    graph_package_stability,          # Package stability metrics (Martin's metrics)
    detect_unstable_packages,         # Detect packages far from main sequence
    detect_hotspots,                  # Technical debt hotspot detection
    detect_layer_violations,          # Layered architecture violations
    detect_deep_call_chains,          # Deep call chain detection
    find_bottleneck_functions,        # Find bottleneck functions in call graph
    detect_hub_dependencies,          # Detect architectural hubs
    detect_change_coupling,           # Change coupling detection
    # Node2Vec random walks for graph embedding (REPO-247)
    node2vec_random_walks,            # Biased random walks for Node2Vec
    # Word2Vec skip-gram training (REPO-249)
    PyWord2VecConfig,                 # Configuration class for Word2Vec training
    train_word2vec_skipgram,          # Train Word2Vec, returns dict
    train_word2vec_skipgram_matrix,   # Train Word2Vec, returns flat matrix
    # Word2Vec parallel training (Hogwild! SGD)
    train_word2vec_skipgram_parallel,        # Parallel Word2Vec, returns dict
    train_word2vec_skipgram_parallel_matrix, # Parallel Word2Vec, returns flat matrix
    # Complete Node2Vec pipeline (REPO-250)
    graph_node2vec,                   # Full Node2Vec: random walks + skip-gram
    graph_random_walks,               # Random walks only (graph_* namespace)
    # Type inference for call graph resolution (PyCG-style)
    infer_types,                      # Infer types from source files
    resolve_method_call,              # Resolve method calls
    # Diff parsing for ML training data (REPO-244)
    parse_diff_changed_lines,         # Parse unified diff for changed lines
    parse_diff_changed_lines_batch,   # Batch parse diffs in parallel
    # Feature extraction for bug prediction (REPO-248)
    combine_features_batch,           # Combine embeddings + metrics in parallel
    normalize_features_batch,         # Z-score normalization in parallel
    # Function boundary detection for ML training data (REPO-245)
    extract_function_boundaries,      # Extract function name + line range
    extract_function_boundaries_batch, # Batch function boundary extraction
    # Parallel git commit processing for bug extraction (REPO-246)
    PyBuggyFunction,                  # Result class for buggy function data
    extract_buggy_functions_parallel, # Extract buggy functions from git history
    # SATD (Self-Admitted Technical Debt) scanning (REPO-410)
    scan_satd_batch,                  # Batch SATD scanning in parallel
    scan_satd_file,                   # Single file SATD scanning
    # Data Flow Graph and Taint Analysis (REPO-411)
    PyDataFlowEdge,                   # Data flow edge result class
    extract_dataflow,                 # Extract DFG from Python source
    extract_dataflow_batch,           # Batch DFG extraction in parallel
    PyTaintFlow,                      # Taint flow result class
    find_taint_flows,                 # Find taint flows (source -> sink)
    find_taint_flows_batch,           # Batch taint analysis in parallel
    get_default_taint_sources,        # Get default taint source patterns
    get_default_taint_sinks,          # Get default taint sink patterns
    get_default_sanitizers,           # Get default sanitizer patterns
    # Incremental SCC cache (REPO-412)
    PyIncrementalSCC,                 # Incremental SCC cache class
    incremental_scc_new,              # Initialize cache with edges
    find_sccs_one_shot,               # One-shot SCC computation (no caching)
    # Control Flow Graph (REPO-414)
    analyze_cfg,                      # Analyze CFG for unreachable code
    analyze_cfg_batch,                # Batch CFG analysis in parallel
    # Interprocedural infinite loop detection (REPO-414 Phase 1)
    analyze_cfg_interprocedural,      # CFG analysis with interprocedural detection
    analyze_interprocedural,          # Function summaries and call graph
    # Cross-file interprocedural analysis (REPO-414 Phase 2)
    analyze_cross_file,               # Cross-file infinite loop detection
    # Tree-sitter multi-language parallel parsing (Phase 2 performance)
    PyParsedFile,                     # Parsed file result class
    PyExtractedFunction,              # Extracted function class
    PyExtractedClass,                 # Extracted class class
    PyExtractedImport,                # Extracted import class
    parse_files_parallel,             # Parse multiple files in parallel
    parse_files_parallel_auto,        # Parse files with auto language detection
    parse_file_tree_sitter,           # Parse single file with tree-sitter
    get_supported_languages,          # Get list of supported languages
    # Data clump detection (REPO-404)
    find_clumps_fast,                 # Parallel data clump detection
    # SIMD-optimized similarity (REPO-403)
    cosine_similarity_simd,           # Single SIMD cosine similarity
    batch_cosine_similarity_simd,     # Batch SIMD cosine similarity
    batch_cosine_similarity_simd_flat, # Flat matrix SIMD similarity
    find_top_k_simd,                  # SIMD top-k similarity search
    # String operations (REPO-403)
    strip_line_numbers,               # Strip :N line number patterns
    batch_strip_line_numbers,         # Batch strip line numbers
    PyParsedQualifiedName,            # Parsed qualified name class
    parse_qualified_name,             # Parse qualified name
    batch_parse_qualified_names,      # Batch parse qualified names
    path_ends_with_suffix,            # Check path suffix match
    batch_find_suffix_matches,        # Batch suffix matching
    batch_find_first_suffix_matches,  # Batch first suffix matching
    # Voting consensus (REPO-405)
    calculate_consensus_batch,        # Parallel voting consensus
    # Indexed call resolution (REPO-406)
    resolve_calls_indexed,            # O(1) call resolution
    # Batch graph traversal (REPO-407)
    batch_traverse_bfs,               # Batch BFS traversal
    batch_traverse_dfs,               # Batch DFS traversal
    extract_subgraph_parallel,        # Parallel subgraph extraction
    # Findings serialization (REPO-408)
    serialize_findings_batch,         # Parallel findings serialization
    # Path expression cache (REPO-416)
    PyCacheStats,                     # Cache statistics class
    PyPathCache,                      # Path expression cache class
    build_transitive_closure,         # Build transitive closure cache
    find_cycles_cached,               # Find cycles using cache (100-1000x faster)
    batch_can_reach,                  # Batch reachability queries
    batch_reachable_from,             # Batch reachable-from queries
)

__all__ = [
    "scan_files",
    "hash_file_md5",
    "batch_hash_files",
    "calculate_complexity_fast",
    "calculate_complexity_batch",
    "calculate_complexity_files",
    "calculate_lcom_fast",
    "calculate_lcom_batch",
    "cosine_similarity_fast",
    "batch_cosine_similarity_fast",
    "find_top_k_similar",
    # Pylint rules not covered by Ruff (individual checks)
    "check_too_many_attributes",        # R0902
    "check_too_few_public_methods",     # R0903
    "check_import_self",                # R0401
    "check_too_many_lines",             # C0302
    "check_too_many_ancestors",         # R0901
    "check_attribute_defined_outside_init",  # W0201
    "check_protected_access",           # W0212
    "check_unused_wildcard_import",     # W0614
    "check_undefined_loop_variable",    # W0631
    "check_disallowed_name",            # C0104
    # Combined checks (parse once - faster)
    "check_all_pylint_rules",           # All rules, single file
    "check_all_pylint_rules_batch",     # All rules, multiple files in parallel
    # Graph algorithms (FalkorDB migration - replaces Neo4j GDS)
    "graph_find_sccs",                  # Strongly connected components
    "graph_find_cycles",                # Circular dependencies
    "graph_pagerank",                   # PageRank importance scores
    "graph_betweenness_centrality",     # Betweenness centrality
    "graph_leiden",                     # Leiden community detection
    "graph_leiden_parallel",            # Leiden with parallel option (REPO-215)
    "graph_harmonic_centrality",        # Harmonic centrality (closeness)
    # Duplicate code detection (REPO-166)
    "find_duplicates",                  # Rabin-Karp duplicate detection
    "find_duplicates_batch",            # Batch duplicate detection
    "tokenize_source",                  # Tokenize source code
    "PyDuplicateBlock",                 # Duplicate block result class
    # Link prediction for call resolution
    "graph_validate_calls",             # Validate calls by community
    "graph_rank_call_candidates",       # Rank call candidates
    "graph_batch_jaccard",              # Batch Jaccard similarity
    # Graph-based code smell detectors (REPO-433)
    "graph_package_stability",          # Package stability metrics (Martin's metrics)
    "detect_unstable_packages",         # Detect packages far from main sequence
    "detect_hotspots",                  # Technical debt hotspot detection
    "detect_layer_violations",          # Layered architecture violations
    "detect_deep_call_chains",          # Deep call chain detection
    "find_bottleneck_functions",        # Find bottleneck functions in call graph
    "detect_hub_dependencies",          # Detect architectural hubs
    "detect_change_coupling",           # Change coupling detection
    # Node2Vec random walks for graph embedding (REPO-247)
    "node2vec_random_walks",            # Biased random walks for Node2Vec
    # Word2Vec skip-gram training (REPO-249)
    "PyWord2VecConfig",                 # Configuration class for Word2Vec training
    "train_word2vec_skipgram",          # Train Word2Vec, returns dict
    "train_word2vec_skipgram_matrix",   # Train Word2Vec, returns flat matrix
    # Word2Vec parallel training (Hogwild! SGD)
    "train_word2vec_skipgram_parallel",        # Parallel Word2Vec, returns dict
    "train_word2vec_skipgram_parallel_matrix", # Parallel Word2Vec, returns flat matrix
    # Complete Node2Vec pipeline (REPO-250)
    "graph_node2vec",                   # Full Node2Vec: random walks + skip-gram
    "graph_random_walks",               # Random walks only (graph_* namespace)
    # Type inference for call graph resolution (PyCG-style)
    "infer_types",                      # Infer types from source files
    "resolve_method_call",              # Resolve method calls
    # Diff parsing for ML training data (REPO-244)
    "parse_diff_changed_lines",         # Parse unified diff for changed lines
    "parse_diff_changed_lines_batch",   # Batch parse diffs in parallel
    # Feature extraction for bug prediction (REPO-248)
    "combine_features_batch",           # Combine embeddings + metrics in parallel
    "normalize_features_batch",         # Z-score normalization in parallel
    # Function boundary detection for ML training data (REPO-245)
    "extract_function_boundaries",      # Extract function name + line range
    "extract_function_boundaries_batch", # Batch function boundary extraction
    # Parallel git commit processing for bug extraction (REPO-246)
    "PyBuggyFunction",                  # Result class for buggy function data
    "extract_buggy_functions_parallel", # Extract buggy functions from git history
    # SATD (Self-Admitted Technical Debt) scanning (REPO-410)
    "scan_satd_batch",                  # Batch SATD scanning in parallel
    "scan_satd_file",                   # Single file SATD scanning
    # Data Flow Graph and Taint Analysis (REPO-411)
    "PyDataFlowEdge",                   # Data flow edge result class
    "extract_dataflow",                 # Extract DFG from Python source
    "extract_dataflow_batch",           # Batch DFG extraction in parallel
    "PyTaintFlow",                      # Taint flow result class
    "find_taint_flows",                 # Find taint flows (source -> sink)
    "find_taint_flows_batch",           # Batch taint analysis in parallel
    "get_default_taint_sources",        # Get default taint source patterns
    "get_default_taint_sinks",          # Get default taint sink patterns
    "get_default_sanitizers",           # Get default sanitizer patterns
    # Incremental SCC cache (REPO-412)
    "PyIncrementalSCC",                 # Incremental SCC cache class
    "incremental_scc_new",              # Initialize cache with edges
    "find_sccs_one_shot",               # One-shot SCC computation (no caching)
    # Control Flow Graph (REPO-414)
    "analyze_cfg",                      # Analyze CFG for unreachable code
    "analyze_cfg_batch",                # Batch CFG analysis in parallel
    # Interprocedural infinite loop detection (REPO-414 Phase 1)
    "analyze_cfg_interprocedural",      # CFG analysis with interprocedural detection
    "analyze_interprocedural",          # Function summaries and call graph
    # Cross-file interprocedural analysis (REPO-414 Phase 2)
    "analyze_cross_file",               # Cross-file infinite loop detection
    # Tree-sitter multi-language parallel parsing (Phase 2 performance)
    "PyParsedFile",                     # Parsed file result class
    "PyExtractedFunction",              # Extracted function class
    "PyExtractedClass",                 # Extracted class class
    "PyExtractedImport",                # Extracted import class
    "parse_files_parallel",             # Parse multiple files in parallel
    "parse_files_parallel_auto",        # Parse files with auto language detection
    "parse_file_tree_sitter",           # Parse single file with tree-sitter
    "get_supported_languages",          # Get list of supported languages
    # Data clump detection (REPO-404)
    "find_clumps_fast",                 # Parallel data clump detection
    # SIMD-optimized similarity (REPO-403)
    "cosine_similarity_simd",           # Single SIMD cosine similarity
    "batch_cosine_similarity_simd",     # Batch SIMD cosine similarity
    "batch_cosine_similarity_simd_flat", # Flat matrix SIMD similarity
    "find_top_k_simd",                  # SIMD top-k similarity search
    # String operations (REPO-403)
    "strip_line_numbers",               # Strip :N line number patterns
    "batch_strip_line_numbers",         # Batch strip line numbers
    "PyParsedQualifiedName",            # Parsed qualified name class
    "parse_qualified_name",             # Parse qualified name
    "batch_parse_qualified_names",      # Batch parse qualified names
    "path_ends_with_suffix",            # Check path suffix match
    "batch_find_suffix_matches",        # Batch suffix matching
    "batch_find_first_suffix_matches",  # Batch first suffix matching
    # Voting consensus (REPO-405)
    "calculate_consensus_batch",        # Parallel voting consensus
    # Indexed call resolution (REPO-406)
    "resolve_calls_indexed",            # O(1) call resolution
    # Batch graph traversal (REPO-407)
    "batch_traverse_bfs",               # Batch BFS traversal
    "batch_traverse_dfs",               # Batch DFS traversal
    "extract_subgraph_parallel",        # Parallel subgraph extraction
    # Findings serialization (REPO-408)
    "serialize_findings_batch",         # Parallel findings serialization
    # Path expression cache (REPO-416)
    "PyCacheStats",                     # Cache statistics class
    "PyPathCache",                      # Path expression cache class
    "build_transitive_closure",         # Build transitive closure cache
    "find_cycles_cached",               # Find cycles using cache (100-1000x faster)
    "batch_can_reach",                  # Batch reachability queries
    "batch_reachable_from",             # Batch reachable-from queries
]
