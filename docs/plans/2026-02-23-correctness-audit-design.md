# Correctness Audit Design

**Goal:** Ensure all regex-based detectors use masked content to prevent false positives, fix lint warnings, remove dead code, add missing tests, and validate improvements against three real-world projects.

**Architecture:** Extend the existing tree-sitter masking layer (proven in round 2 FP reduction) to 23 additional detectors. Each migration is a one-line change at the regex-scanning call site. Structural/graph-based calls stay on raw content.

## Section 1: Masking Migration (23 Detectors)

Replace `global_cache().content(path)` with `global_cache().masked_content(path)` at regex-scanning call sites.

### Security Detectors (11)
| Detector | FP Risk | Pattern Examples |
|----------|---------|-----------------|
| cleartext_credentials | High | `password`, `secret`, `api_key` in logging |
| command_injection | High | `os.system`, `subprocess`, `exec` |
| cors_misconfig | Medium | `Access-Control-Allow-Origin` |
| django_security | High | `@csrf_exempt`, `DEBUG = True`, `SECRET_KEY` |
| eval_detector | High | `eval`, `exec`, `__import__` |
| insecure_cookie | Medium | `set_cookie()` flags |
| insecure_crypto | Medium | `md5`, `sha1`, `DES`, `RC4` |
| insecure_deserialize | Medium | `JSON.parse`, `yaml.load` |
| insecure_random | Medium | `Math.random`, `random.random` |
| jwt_weak | Medium | `algorithm`, `HS256`, `none` |
| log_injection | Medium | `logger.`, `log.`, `console.log` |

### Code Quality Detectors (12)
| Detector | FP Risk | Pattern Examples |
|----------|---------|-----------------|
| boolean_trap | Low | `true`, `false` in function args |
| dead_store | Medium | `let`/`var`/`const` assignments |
| hardcoded_timeout | Medium | `timeout`, `sleep`, `delay` + numbers |
| infinite_loop | Low | `while(true)`, `for(;;)` |
| magic_numbers | Medium | `\d{2,}` numeric literals |
| message_chain | Low | `.method().method()` chains |
| missing_await | Medium | `fetch(`, `axios.`, `.json()` |
| n_plus_one | Medium | `.filter(`, `SELECT`, `.get(` |
| sync_in_async | Medium | `time.sleep`, `readFileSync` |
| test_in_production | Medium | `import.*pytest`, `Mock(` |

### Detectors Confirmed Fine As-Is (28)
ai_complexity_spike, broad_exception, callback_hell, commented_code, core_utility, dead_code, deep_nesting, duplicate_code, empty_catch, express_security, generator_misuse, global_variables, implicit_coercion, large_files, lazy_class, react_hooks, regex_in_loop, single_char_names, string_concat_loop, unhandled_promise, unreachable_code, wildcard_imports, and others doing structural/graph analysis.

### Migration Rules
- Only switch the regex-scanning `content()` call; structural calls (line counting, import detection) stay raw
- Each detector reviewed individually during implementation
- If a positive test breaks because test content is in a string, change test file to `.rb` extension (no tree-sitter grammar = content passes through unmasked)

## Section 2: Cleanup

1. **Clippy fixes (8 warnings):** `cargo clippy --fix` — all auto-fixable style issues
2. **Remove orphaned file:** Delete `src/detectors/temporal_metrics.rs` (not in mod.rs, unused)
3. **Add missing tests:**
   - `ai_churn.rs` — basic positive/negative test
   - `single_char_names.rs` — basic detection test
   - `voting_engine.rs` — voting aggregation test

## Section 3: Expanded Validation

Rebuild release binary, validate against three projects:

| Project | Baseline | Purpose |
|---------|----------|---------|
| Flask | 89.4 (B+), 37 findings | Re-validate, expect improvement |
| FastAPI | 94.8 (A), 187 findings | Re-validate, expect improvement |
| Django | No baseline | New measurement, stress-test security detectors |

Success criteria:
- Flask/FastAPI scores stable or improved, finding counts decreased
- Django produces reasonable results (no obvious FP clusters)
- All 631+ tests pass
- Zero clippy warnings
