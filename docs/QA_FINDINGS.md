# QA Findings: Comprehensive Multi-Language Testing

**Date:** 2026-03-18
**Scope:** All 9 supported languages, 107 detectors, CLI integration tests

## Summary

- **5 bugs fixed** (Tier 1)
- **9 language test suites created** with 85 integration tests (+ 12 existing CLI tests = 97 total)
- **1 dogfooding test** (self-analysis, `#[ignore]`)
- **17 detector language-support gaps** documented via negative assertions
- **All 97 tests pass**

---

## Bugs Fixed

| ID | Bug | Root Cause | Fix |
|----|-----|-----------|-----|
| WU-01 | `--format json` produces empty stdout | Test passed nonexistent `--no-git` flag; clap errored to stderr | Removed `--no-git` from test (git is auto-detected) |
| WU-02 | 5 thread join `.expect()` calls crash on panic | No error handling for worker thread panics | Replaced with `match` + `error!` logging + safe defaults |
| WU-03 | Detector failures logged at DEBUG (invisible) | `debug!` level in `runner.rs` | Elevated to `warn!` + added skip counter summary |
| WU-04 | `generator_misuse` claims JS/TS support | `file_extensions()` returned `["py","js","ts"]` but `detect()` only scans `.py` | Narrowed to `&["py"]` |
| WU-05 | Dead code referencing removed detectors | `voting_engine.rs` had weights for 9 nonexistent external-tool detectors | Removed phantom entries + blanket `#[allow(dead_code)]` |

---

## Per-Language Coverage Matrix

Tests verify which detectors actually fire for each language's fixture.

| Detector | Python | TypeScript | JavaScript | Rust | Go | Java | C# | C | C++ |
|----------|--------|-----------|------------|------|----|------|----|---|-----|
| EmptyCatchDetector | - | Y | Y | - | - | Y | Y | - | gap |
| DeepNestingDetector | - | Y | Y | Y | Y | Y | Y | Y | Y |
| MagicNumbersDetector | - | gap | - | - | Y | gap | Y | - | Y |
| DebugCodeDetector | - | gap | gap | - | gap | - | - | - | - |
| TodoScanner | - | Y | - | Y | - | - | Y | Y | Y |
| CommentedCodeDetector | - | - | - | Y | - | - | gap | Y | Y |
| SQLInjectionDetector | - | Y | - | - | Y | Y | - | - | - |
| XssDetector | - | Y | - | - | - | - | - | - | - |
| CommandInjectionDetector | - | - | - | - | gap | - | - | - | - |
| InsecureCryptoDetector | - | - | - | - | gap | gap | - | - | - |
| HardcodedIpsDetector | - | - | - | - | - | - | - | gap | gap |
| UnwrapWithoutContextDetector | - | - | - | Y | - | - | - | - | - |
| UnsafeWithoutSafetyComment | - | - | - | Y | - | - | - | - | - |
| CloneInHotPathDetector | - | - | - | Y | - | - | - | - | - |
| PanicDensityDetector | - | - | - | Y | - | - | - | - | - |
| XXEDetector | - | - | - | - | - | Y | - | - | - |
| LogInjectionDetector | - | - | - | - | - | Y | - | - | - |
| ExpressSecurityDetector | - | - | Y | - | - | - | - | - | - |
| ReactHooksDetector | - | Y(tsx) | - | - | - | - | - | - | - |
| CallbackHellDetector | - | Y | - | - | - | - | - | - | - |
| RegexDosDetector | - | - | Y | - | - | - | - | - | - |
| InsecureTlsDetector | Y | - | - | - | - | - | - | - | - |
| BroadExceptionDetector | Y | - | - | - | - | - | - | - | - |
| MutableDefaultArgsDetector | Y | - | - | - | - | - | - | - | - |
| SyncInAsyncDetector | Y | - | - | - | - | - | - | - | - |
| LongMethodsDetector | - | - | - | - | - | - | Y | - | - |
| DeadStoreDetector | - | - | - | - | - | - | - | Y | Y |
| LongParameterListDetector | - | - | - | - | - | Y | - | Y | - |

**Legend:** Y = fires, gap = claims support but doesn't fire, - = not tested/not applicable

---

## Detector Language-Support Gaps (17 total)

These detectors declare language support via `file_extensions()` but their `detect()` implementation doesn't actually scan that language:

| Detector | Claimed | Actually Scans | Gap |
|----------|---------|---------------|-----|
| DebugCodeDetector | py,js,ts,jsx,tsx,rb,java,go | py,js,ts,jsx,tsx,rb,java | Go missing from `detect()` |
| MagicNumbersDetector | py,js,ts,jsx,tsx,java,go,rs,c,cpp,cs | subset | TS, Java not firing |
| InsecureCryptoDetector | py,js,ts,java,go,rs | subset | Go (`md5.New` vs `md5.new` case), Java |
| CommandInjectionDetector | py,js,ts,java,go,rs | subset | Go pattern too narrow |
| HardcodedIpsDetector | py,js,ts,java,go,rs,rb,php,cs | subset | C, C++ missing from scan loop |
| EmptyCatchDetector | py,js,ts,jsx,tsx,java,cs | subset | C++ not supported |
| CommentedCodeDetector | varies | varies | C# not firing |
| InsecureRandomDetector | js,ts | subset | Not firing for JS fixture |
| PrototypePollutionDetector | js,ts | subset | Not firing for TS fixture |
| ImplicitCoercionDetector | js,ts | subset | Not firing for TS fixture |
| InsecureDeserializeDetector | py,java | subset | Neither language firing |
| MutableDefaultArgsDetector | py | py | Gap was in test (now fixed) |
| BroadExceptionDetector | py | py | Gap was in test (now fixed) |
| SyncInAsyncDetector | py | py | Gap was in test (now fixed) |

---

## Test Files Created

### Fixtures (`repotoire-cli/tests/fixtures/`)
| File | Lines | Language | Key Issues |
|------|-------|---------|------------|
| `smells.ts` | ~200 | TypeScript | Empty catch, nesting, callbacks, SQL, XSS |
| `security.tsx` | ~100 | TSX | React hooks violations, dangerouslySetInnerHTML |
| `smells.js` | ~200 | JavaScript | Express security, CORS, regex DoS |
| `rust_smells.rs` | ~200 | Rust | unwrap, unsafe, clone, panic density |
| `smells.go` | ~155 | Go | SQL injection, command injection, md5 |
| `Smells.java` | ~200 | Java | XXE, SQL injection, deserialization |
| `Smells.cs` | ~200 | C# | Long methods, empty catch, nesting |
| `smells.c` | ~190 | C | Deep nesting, hardcoded IPs, dead stores |
| `smells.cpp` | ~220 | C++ | Empty catch, nesting, hardcoded IPs |
| `python_quality.py` | ~150 | Python | Mutable defaults, broad except, sync-in-async |

### Test Suites (`repotoire-cli/tests/`)
| File | Tests | Pass |
|------|-------|------|
| `lang_typescript.rs` | 17 | 17 |
| `lang_javascript.rs` | 9 | 9 |
| `lang_rust.rs` | 10 | 10 |
| `lang_go.rs` | 8 | 8 |
| `lang_java.rs` | 10 | 10 |
| `lang_csharp.rs` | 7 | 7 |
| `lang_c.rs` | 8 | 8 |
| `lang_cpp.rs` | 8 | 8 |
| `lang_python.rs` | 8 | 8 |
| `dogfood.rs` | 1 | 1 (`#[ignore]`) |
| `cli_flags_test.rs` | 12 | 12 |
| **Total** | **98** | **98** |

---

## Dogfooding Results

Self-analysis (`repotoire analyze` on its own codebase, `cargo test --test dogfood -- --ignored`):
- **Score: 89.7 (grade B+)** — well above the >50 threshold
- **849 findings from 37 unique detectors**
- **Deterministic**: two cold-start runs produced identical scores and finding counts
- **Runtime**: ~87s for 93k+ lines of Rust
- `UnwrapWithoutContextDetector` fires (21 findings) — confirming it catches `.expect()` patterns

**Top detectors on self-analysis:**

| Detector | Count |
|----------|-------|
| LongMethodsDetector | 98 |
| DataClumpsDetector | 79 |
| DuplicateCodeDetector | 50 |
| DeadStoreDetector | 49 |
| AIDuplicateBlockDetector | 49 |
| AIMissingTestsDetector | 49 |
| AIComplexitySpikeDetector | 43 |
| InfluentialCodeDetector | 43 |
| LongParameterListDetector | 42 |
| DeepNestingDetector | 39 |

---

## Root Cause Analysis: Why Detectors Don't Fire

Detailed investigation by QA workers revealed specific root causes for detector gaps:

| Detector | Language | Root Cause |
|----------|----------|-----------|
| DebugCodeDetector | Go | `detect()` scans `["py","js","ts","jsx","tsx","rb","java"]` — Go omitted from loop |
| InsecureCryptoDetector | Go | Regex matches `md5.new` (lowercase) but Go uses `md5.New` (PascalCase) |
| InsecureCryptoDetector | Java | `is_hash_mention_not_usage()` filter skips lines with "weak"/"unsafe" in names; string masking hides `"DES"` from cipher regex; GBDT postprocessor filters remaining matches |
| CommandInjectionDetector | Go | Pre-filter doesn't include Go's `exec.Command()` pattern; requires `r.FormValue` on same line |
| CommandInjectionDetector | Java | Pre-filter doesn't include `Runtime.exec()` pattern |
| HardcodedIpsDetector | C, C++ | `files_with_extensions` scan loop at `hardcoded_ips.rs:99` omits `"c"` and `"cpp"` |
| EmptyCatchDetector | C++ | Extension list only includes `py,js,ts,jsx,tsx,java,cs` |
| CommentedCodeDetector | C# | `commented_code.rs:144` omits `"cs"` from internal extension scan loop |
| MagicNumbersDetector | TS, Java | GBDT postprocessor filters all findings; adaptive threshold calibration on single-file repos doesn't trigger |
| InsecureDeserializeDetector | Python, Java | Python: `HAS_SERIALIZE` flag checks for Python/JS keywords but doesn't match `pickle.loads` pattern; Java: requires user-input indicators on same line as `ObjectInputStream` |
| PrototypePollutionDetector | TS | Tree-sitter string masking replaces patterns before regex runs |
| CorsMisconfigDetector | JS | Tree-sitter string masking replaces `'*'` before CORS regex runs |
| MissingMustUseDetector | Rust | 4 `pub fn -> Result` present but detector produces 0 findings; possible postprocessing filter or confidence threshold issue |
| MissingAwaitDetector | Python | Regex patterns are JS/TS-focused (fetch, axios, aio*); doesn't match Python async patterns |

---

## Remaining Issues (Not Fixed)

1. **17 detector language-support gaps** — detectors claim languages they don't actually scan (see root cause table above)
2. **GBDT postprocessor over-filtering** — MagicNumbersDetector and InsecureCryptoDetector produce findings internally but GBDT postprocessor removes them for Java; worth investigating thresholds
3. **String masking interference** — tree-sitter string masking removes detector-relevant content for PrototypePollution (TS) and CorsMisconfig (JS) before detection regex runs
4. **Pre-existing clippy warnings** — ~4 warnings in main library code (unused fields), ~60 clippy errors across codebase
5. **No integration tests for**: framework detection, incremental cache behavior, graph-based detectors (require multi-file repos)
6. **Dogfooding test is slow** — marked `#[ignore]`, runs only with `--ignored` flag (~87s)
