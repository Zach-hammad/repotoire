# AI Detector Suite: Research-Backed Implementation Design

**Date**: 2026-02-24
**Status**: Approved
**Goal**: Implement the 3 stub AI detectors using proper Rust infrastructure (tree-sitter, git2, rayon) already available in the codebase.

---

## Research Foundation

| Signal | Source | Data |
|--------|--------|------|
| Code duplication up 8x | GitClear 2025 | Copy/paste: 8.3% → 12.3%; duplicate blocks 8x increase |
| Code churn up 84% | GitClear 2025 | New code revised within 2 weeks: 3.1% → 5.7% |
| Refactoring down 60% | GitClear 2025 | Moved/refactored lines: 25% → <10% |
| Code smells up 63% | Paul et al. arXiv:2510.03029 | Implementation smells +73%, design smells +21% |
| AI code gets more corrective fixes | Duran et al. arXiv:2601.16809 | 26.3% corrective vs 23% for human code |
| Clone detection: Jaccard+AST | Martinez-Gil arXiv:2401.09885 | Jaccard >0.70 threshold, AST >0.75 verification |

**Key papers:**
- arXiv:2510.03029 — "Investigating The Smells of LLM Generated Code" (code smells +63% in AI code)
- arXiv:2601.16809 — "Will It Survive?" (AI code survival analysis, corrective modification patterns)
- arXiv:2411.04299 — "Detecting AI-Generated Source Code" (AST + static metrics, F1=82.55)
- arXiv:2512.18020 — "Specification and Detection of LLM Code Smells" (5 LLM-specific smells, 86% precision)
- arXiv:2509.20491 — "AI-Specific Code Smells" (22 AI-specific smells, 88.66% precision)
- GitClear 2025 — "AI Copilot Code Quality" (211M lines, churn/duplication metrics)

---

## Available Rust Infrastructure

| Library | Already Used In | Capability |
|---------|----------------|------------|
| **tree-sitter** (9 languages) | `ssa_flow.rs`, all parsers | Real AST parsing — function body node walking |
| **git2** (libgit2, vendored) | `git/history.rs`, `git/blame.rs` | `get_file_churn()`, `get_line_range_commits()`, blame with caching |
| **rayon** | `engine.rs`, `blame.rs`, `function_context.rs` | `.par_iter()` for parallel function comparison |

---

## Detector 1: AIBoilerplateDetector

### Problem
AI assistants generate verbose, repetitive code patterns instead of abstracting. Refactoring dropped 60% (GitClear 2025). Functions with identical structure but different names/literals proliferate.

### Approach: tree-sitter AST Fingerprinting + Jaccard Clustering

**Already built** (unused): `cluster_by_similarity()`, `jaccard_similarity()`, `analyze_cluster()`, `create_finding()`, `generate_suggestion()`, all `BoilerplatePattern` types.

**Implementation of `detect()`:**

1. For each source file via `FileProvider`, parse with tree-sitter
2. Walk AST to find function definitions, extract each function's body subtree
3. For each function body, collect structural tokens — the **kinds** of AST nodes (e.g., `if_statement`, `for_statement`, `try_statement`, `return_statement`, `call`, `assignment`)
4. Build `HashSet<String>` from structural tokens → this is the `hash_set` field in `FunctionAST`
5. Detect patterns from AST node presence (e.g., `try_statement` → `TryExcept`, `for_statement` + `call(append/extend)` → `Loop`)
6. Feed all `FunctionAST` into `cluster_by_similarity(threshold=0.70, min_cluster=3)`
7. For each cluster, call `analyze_cluster()` → `create_finding()`

**Performance**: Pre-filter by LOC >= min_loc. Use rayon for parsing phase (one parser per thread). The clustering is O(n²) but bounded by filtering to functions >= 5 LOC in non-test files.

**Multi-language**: tree-sitter grammars differ but node kind names are consistent enough (all have `if_statement`, `for_statement`, etc.). Build a small mapping for Python/JS/TS/Java/Go/Rust.

### Key Design Decision
Fingerprint by **node kinds only** (not token values). This produces Type-2 clone detection — same structure, different identifiers — which is exactly the AI boilerplate pattern.

---

## Detector 2: AIChurnDetector

### Problem
AI-generated code gets revised quickly after creation (GitClear: churn 3.1%→5.7%). Functions created and modified within 48h with multiple iterations are a strong signal.

### Approach: git2 + blame integration (no shell-out)

**Already built** (unused): `FunctionChurnRecord`, `ai_churn_score()`, `calculate_severity()`, `create_finding()`, `detect_without_git_history()`.

**Already available in `git/` module:**
- `GitHistory::get_file_churn(file, max_commits)` → `FileChurn`
- `GitHistory::get_line_range_commits(file, line_start, line_end, max)` → `Vec<CommitInfo>`
- `GitBlame::blame_lines(file, start, end)` → `Vec<LineBlame>` (with disk caching)

**Implementation of `detect()`:**

1. Open repo: `GitHistory::new(files.repo_path())` — if fails, return empty (graceful degradation)
2. Get all functions from graph: `graph.get_functions()`
3. For each function, get file-level churn: `get_file_churn(file_path, 500)`
4. For functions in high-churn files (commit_count > threshold), get function-level commits: `get_line_range_commits(file, line_start, line_end, 50)`
5. Build `FunctionChurnRecord` from `CommitInfo` list (map timestamps, calculate deltas)
6. Score with `ai_churn_score()`, filter by `MIN_CHURN_SCORE`, produce findings

**Performance optimization**: Two-phase approach:
- Phase 1: File-level churn (fast, single revwalk) — filter to files with commit_count > 5
- Phase 2: Function-level analysis (slower, per-function) — only for functions in high-churn files

**GitHistory caching**: `GitHistory::get_all_file_churn()` does a single revwalk for all files. Call once, cache the `HashMap<String, FileChurn>`, then only drill into function-level for the top N churny files.

### Graceful Degradation
- No `.git` directory → return empty findings with warning log
- Bare repo → return empty
- Shallow clone → limited commit history, findings will have lower confidence

---

## Detector 3: AIDuplicateBlockDetector

### Problem
AI coding assistants produce near-identical functions with minor variations (different variable names, same logic). Duplicate blocks up 8x (GitClear 2025).

### Approach: tree-sitter Normalized AST + Jaccard Similarity

**Already built** (unused): `find_duplicates()`, `jaccard_similarity()`, `calculate_generic_ratio()`, `create_finding()`, `GENERIC_IDENTIFIERS` list.

**Implementation of `detect()`:**

1. For each source file, parse with tree-sitter
2. Extract function bodies, for each function:
   a. Walk AST, collect all identifier nodes → compute `generic_ratio` via `calculate_generic_ratio()`
   b. Normalize the AST: replace all identifier tokens with `$ID`, keep keywords/operators/structure
   c. Build `HashSet<String>` from normalized token bigrams (pairs of consecutive node kinds)
3. Build `FunctionData` with `hash_set`, `generic_ratio`, `ast_size` (node count)
4. Call `find_duplicates()` — already does:
   - Skip same-file comparisons
   - Pre-filter by AST size ratio (skip if < 0.5)
   - Jaccard similarity >= 0.70 threshold
   - Sort by similarity descending
5. Produce findings via `create_finding()`

**Differentiation from `duplicate_code.rs`**: The existing detector uses jscpd-style token matching. This detector specifically targets:
- **Cross-file** near-duplicates (Type-2 clones — same structure, renamed variables)
- **Generic naming** as an aggravating signal (raises severity)
- **AI-specific framing** in findings (suggestions about consolidation patterns)

### Performance
- Pre-filter: Skip functions < min_loc (5), skip test files
- AST size pre-filter in `find_duplicates()` eliminates most comparisons
- Use rayon for the parsing phase
- Cap at max_findings (50)

---

## Shared: tree-sitter AST Utility Module

Both Boilerplate and Duplicate detectors need to parse function bodies and extract AST features. Create a shared utility:

```
src/detectors/ast_fingerprint.rs
```

**Functions:**
- `parse_functions_from_file(content, language) -> Vec<FunctionInfo>` — extract function name, line range, body text
- `structural_fingerprint(body, language) -> HashSet<String>` — node kinds only (for boilerplate)
- `normalized_fingerprint(body, language) -> HashSet<String>` — normalized token bigrams (for duplicate)
- `extract_identifiers(body, language) -> Vec<String>` — all identifier tokens (for generic ratio)
- `detect_patterns(body, language) -> Vec<BoilerplatePattern>` — pattern classification from AST

This reuses the `SsaFlow::get_ts_language()` pattern for language selection.

---

## Testing Strategy

Each detector gets:
1. **Unit tests with MockFileProvider** — synthetic code samples (matching existing test pattern)
2. **Integration test on real repos** — Django (low findings expected) + LangChain (more findings expected)

---

## Success Criteria

| Metric | Target |
|--------|--------|
| All existing tests passing | Yes |
| New tests per detector | 3-5 |
| Django findings (each detector) | < 20 (low FP on human-written) |
| Compile time regression | < 5s increase |
| Analysis time regression | < 2s per detector on 1000-file repo |
