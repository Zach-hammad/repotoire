# Detector Accuracy Pass 3 — Design Doc

## Goal

Eliminate ~75 false positives across 9 detectors through deep semantic heuristic refactors (3 detectors) and targeted pattern fixes (6 detectors). This brings Django findings from 366 → ~291 and improves the overall score beyond 98.4/A+.

## Context

**Pass 1** (Round 1): Fixed 8 detectors, reduced Django findings from 833 → 616.
**Pass 2** (Round 2): Fixed 15 detectors, reduced Django findings from 616 → 366.
**Pass 3** (this round): 9 detectors, targeting ~75 remaining FPs.

### Methodology

Same as Passes 1 & 2: manually audit each finding against Django source code, identify false positive patterns, implement fixes with unit tests, validate on Flask/FastAPI/Django.

### Benchmark Projects

| Project | Pass 2 Score | Pass 2 Findings |
|---------|-------------|-----------------|
| Django  | 98.4/A+     | 366             |
| Flask   | 93.0        | 25              |
| FastAPI | 97.9        | 120             |

---

## Phase 1: Deep Refactors (3 Detectors, ~26 FPs)

### 1. SecretDetector — Variable vs Literal Distinction

**File:** `repotoire-cli/src/detectors/secrets.rs`
**FPs:** 12 High severity, all false positives
**Root Cause:** The Generic Secret pattern `(secret|password|passwd|pwd)\s*[=:]\s*[^\s]{8,}` matches ANY value after a secret keyword — including variable references, settings reads, and parameter passing.

#### Audit Results

| Line | File | Code | Why FP |
|------|------|------|--------|
| 142 | django/core/mail/__init__.py | `password=auth_password,` | Variable reference |
| 99  | django/core/mail/__init__.py | `password=auth_password,` | Variable reference |
| 107 | django/middleware/csrf.py | `csrf_secret = request.META["CSRF_COOKIE"]` | Request data read |
| 240 | django/middleware/csrf.py | `csrf_secret = request.COOKIES[...]` | Cookie read |
| 350 | django/contrib/auth/forms.py | `username=username, password=password` | Parameter passing |
| 541 | django/contrib/auth/forms.py | `old_password = self.cleaned_data["old_password"]` | Form data read |
| 306 | django/db/backends/oracle/base.py | `password=self.settings_dict["PASSWORD"],` | Settings read |
| 277 | django/db/backends/oracle/base.py | `password=self.settings_dict["PASSWORD"],` | Settings read |
| 140 | django/contrib/auth/base_user.py | `yield self._get_session_auth_hash(secret=fallback_secret)` | Variable reference |
| 95  | django/contrib/auth/base_user.py | `self._password = raw_password` | Variable assignment |
| 28  | django/utils/crypto.py | `secret = settings.SECRET_KEY` | Settings read |
| 39  | django/core/mail/backends/smtp.py | `self.password = settings.EMAIL_HOST_PASSWORD if password is None else password` | Settings/param |

**Common Pattern:** Every FP has a **variable, attribute access, or settings read** as the value — never a string literal.

#### Fix — Add value-type filtering to Generic Secret pattern

In `scan_file()` method (around line 201), after the match is found for the "Generic Secret" pattern, add checks to distinguish literal values from variable references:

```rust
// Inside the Generic Secret value-type filtering block (around line 258)
if pattern.name == "Generic Secret" {
    // Extract the value part after = or :
    let value_part = if let Some(eq_pos) = line.find('=') {
        line[eq_pos + 1..].trim()
    } else if let Some(colon_pos) = line.find(':') {
        line[colon_pos + 1..].trim()
    } else {
        ""
    };

    if !value_part.is_empty() {
        // EXISTING: Skip function/class calls and collection literals
        // ...

        // NEW: Skip variable references (not string literals)
        // A hardcoded secret MUST have a string literal as the value
        let first_char = value_part.chars().next().unwrap_or(' ');
        if !matches!(first_char, '"' | '\'' | '`') {
            // Value doesn't start with a quote — it's a variable reference,
            // attribute access, settings read, or expression
            continue;
        }

        // NEW: Skip settings/config reads
        if value_part.starts_with("settings.")
            || value_part.starts_with("self.settings")
            || value_part.starts_with("request.")
            || value_part.starts_with("self.cleaned_data")
            || value_part.starts_with("self._")
            || value_part.starts_with("os.environ")
        {
            continue;
        }
    }
}
```

**Key insight:** Real hardcoded secrets always have a string literal as the value (`password = "secret123"`). Variable references (`password = auth_password`), settings reads (`secret = settings.SECRET_KEY`), and attribute accesses (`self._password = raw_password`) are NOT hardcoded secrets.

**Edge case:** We must be careful not to skip the EXISTING specific-format patterns (AWS keys, GitHub tokens, etc.) which match the key format itself, not the `keyword = value` pattern. This fix only applies to the "Generic Secret" pattern.

---

### 2. XxeDetector — Safe Parser Recognition

**File:** `repotoire-cli/src/detectors/xxe.rs`
**FPs:** 7 High severity, all false positives
**Root Cause:** The detector already checks for protection patterns near parse calls (line 231-238), but Django's `DefusedExpatParser` is defined with different keywords than those in `get_protection_patterns("py")`.

#### Audit Results

| Line | File | Code | Why FP |
|------|------|------|--------|
| 7    | django/core/serializers/xml_serializer.py | `from xml.dom import minidom, pulldom` | Import only |
| 21   | django/core/serializers/xml_serializer.py | Internal function | Not parsing |
| 27   | django/core/serializers/xml_serializer.py | `getattr(minidom, "_in_document")` | Not parsing |
| 30   | django/core/serializers/xml_serializer.py | `minidom._in_document = lambda...` | Not parsing |
| 35   | django/core/serializers/xml_serializer.py | `minidom._in_document = original_fn` | Not parsing |
| 227  | django/core/serializers/xml_serializer.py | `pulldom.parse(self.stream, self._make_parser())` | Protected by DefusedExpatParser |
| 237  | globals.js | `"DOMParser": false,` | Static data, not code |

**Root Cause Analysis:**
1. The `xxe_pattern()` regex matches `minidom`, `pulldom`, `DOMParser` — catching imports and non-parsing references
2. Django defines a `DefusedExpatParser` class with `feature_external_ges`, `DTDForbidden`, `EntitiesForbidden` — none of which are in the Python protection patterns list
3. JavaScript static data files shouldn't be scanned for XXE

#### Fix 1 — Add custom safe parser detection to Python protection patterns

In `get_protection_patterns()` (line 37), expand the Python patterns:

```rust
"py" => vec![
    "resolve_entities=False",
    "no_network=True",
    "defusedxml",
    "forbid_dtd=True",
    "forbid_entities=True",
    // NEW: Django-style custom safe parser indicators
    "feature_external_ges",       // ExpatParser feature for external entities
    "feature_external_pes",       // ExpatParser feature for parameter entities
    "DTDForbidden",               // Raised when DTD detected
    "EntitiesForbidden",          // Raised when entities detected
    "ExternalReferenceForbidden", // Raised on external references
    "defused",                    // Generic "defused" keyword in class/function names
],
```

#### Fix 2 — Filter non-parsing matches from xxe_pattern

The regex currently matches `minidom`, `pulldom`, `DOMParser` etc. which catch imports and data references. Add a post-match filter:

```rust
// After xxe_pattern match (around line 230)
if !xxe_pattern().is_match(line) {
    continue;
}

// NEW: Skip import-only lines (no parsing happens on import)
let trimmed = line.trim();
if trimmed.starts_with("from ") || trimmed.starts_with("import ") {
    continue;
}

// NEW: Skip lines that reference XML modules but don't call parse functions
// Only flag lines with actual parsing calls: parse(, load(, parseString(, etc.
let has_parse_call = line.contains(".parse(")
    || line.contains(".parseString(")
    || line.contains(".parseXML(")
    || line.contains("XMLParser(")
    || line.contains("DocumentBuilder")
    || line.contains("SAXParser(")
    || line.contains("XMLReader(");
if !has_parse_call {
    continue;
}
```

#### Fix 3 — Skip JavaScript static data files

```rust
// NEW: Skip .js files that are static data (globals, config listings)
if ext == "js" {
    // Skip lines that are just property declarations in objects
    let trimmed = line.trim();
    if trimmed.ends_with("false,") || trimmed.ends_with("true,") || trimmed.ends_with("false") {
        continue;
    }
}
```

---

### 3. PickleDeserializationDetector — Trusted Context Exclusion

**File:** `repotoire-cli/src/detectors/pickle_detector.rs`
**FPs:** 7 High severity, all false positives
**Root Cause:** The detector marks `pickle.load()`/`pickle.loads()` as "ALWAYS DANGEROUS" (line 144-146), but Django's cache backends use pickle for trusted server-side serialization.

#### Audit Results

| Line | File | Code | Why FP |
|------|------|------|--------|
| 29  | django/core/cache/backends/redis.py | `pickle.loads(data)` | Redis cache - trusted |
| 43  | django/core/cache/backends/locmem.py | `pickle.loads(pickled)` | In-memory cache |
| 73  | django/core/cache/backends/locmem.py | `pickle.loads(pickled)` | In-memory cache |
| 37  | django/core/cache/backends/filebased.py | `pickle.loads(zlib.decompress(f.read()))` | File cache |
| 70  | django/core/cache/backends/filebased.py | `pickle.loads(zlib.decompress(f.read()))` | File cache |
| 154 | django/core/cache/backends/filebased.py | `pickle.load(f)` | File cache |
| 96  | django/core/cache/backends/db.py | `pickle.loads(base64.b64decode(...))` | DB cache |

**Common Pattern:** All 7 are in `cache/backends/` — Django's cache framework serializing/deserializing its OWN trusted data.

#### Fix — Add trusted-context path exclusions

In `should_exclude()` or `scan_source_files()` (around line 170), add cache backend exclusions:

```rust
// In scan_source_files(), after the existing should_exclude check:
if self.should_exclude(&rel_path) {
    continue;
}

// NEW: Skip trusted serialization contexts
// Cache backends use pickle for server-side data that never comes from user input
if rel_path.contains("cache/backends/")
    || rel_path.contains("sessions/backends/")
{
    continue;
}
```

This is the correct fix because:
1. Cache backends only serialize data they created themselves
2. Session backends similarly only serialize trusted session data
3. The data sources (Redis, memcache, database, local files) are all server-controlled
4. No user input flows to `pickle.loads()` in these contexts

---

## Phase 2: Quick Fixes (6 Detectors, ~49 FPs)

### 4. GodClassDetector — Fix Test Path Detection for Relative Paths

**File:** `repotoire-cli/src/detectors/god_class.rs`
**FPs:** 17 Critical severity (all test classes)
**Root Cause:** Both the graph-based `is_test_path()` in `class_context.rs:556` AND the fallback check in `god_class.rs:519-527` look for `/tests/` with a leading slash. But file paths from the graph are relative (e.g., `tests/expressions/tests.py`), so `contains("/tests/")` never matches paths that START with `tests/`.

#### Affected Findings (17 of 20)

All test classes like:
- `tests/expressions/tests.py:103` — BasicExpressionsTests
- `tests/admin_views/tests.py:6179` — SeleniumTests
- `tests/admin_filters/tests.py:343` — ListFiltersTests
- `tests/lookup/tests.py:50` — LookupTests
- (13 more)

#### Fix 1 — class_context.rs `is_test_path()` (line 556)

```rust
fn is_test_path(&self, path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.contains("/test/")
        || lower.contains("/tests/")
        || lower.contains("/__tests__/")
        || lower.contains("/spec/")
        || lower.ends_with("_test.go")
        || lower.ends_with("_test.py")
        || lower.ends_with(".test.ts")
        || lower.ends_with(".test.js")
        || lower.ends_with(".spec.ts")
        || lower.ends_with(".spec.js")
        // NEW: Handle relative paths starting with test directories
        || lower.starts_with("tests/")
        || lower.starts_with("test/")
        || lower.starts_with("__tests__/")
        || lower.starts_with("spec/")
}
```

#### Fix 2 — god_class.rs fallback (line 519-527)

```rust
if ctx.is_none() {
    let lower_path = class.file_path.to_lowercase();
    if lower_path.contains("/test/") || lower_path.contains("/tests/")
        || lower_path.contains("/__tests__/") || lower_path.contains("/spec/")
        || lower_path.contains("test_") || lower_path.contains("_test.")
        // NEW: Handle relative paths starting with test directories
        || lower_path.starts_with("tests/")
        || lower_path.starts_with("test/")
    {
        debug!("Skipping test class: {}", class.name);
        continue;
    }
}
```

---

### 5. LazyClassDetector — Fix Test Path Detection

**File:** `repotoire-cli/src/detectors/lazy_class.rs`
**FPs:** 7 Medium severity (all test model classes)
**Root Cause:** Same as GodClass — relative paths not matching test path patterns.

#### Affected Findings

| File | Class | Why FP |
|------|-------|--------|
| tests/serializers/models/natural.py:114 | FKToNaturalKeyWithNullable | Test model |
| tests/serializers/models/natural.py:14 | NaturalKeyAnchor | Test model |
| tests/serializers/models/natural.py:69 | FKAsPKNoNaturalKey | Test model |
| tests/serializers/models/natural.py:28 | NaturalKeyThing | Test model |
| tests/fixtures_regress/models.py:155 | NaturalKeyWithFKDependency | Test model |
| tests/queries/models.py:499 | CommonMixedCaseForeignKeys | Test model |
| tests/bulk_create/models.py:155 | DbDefaultPrimaryKey | Test model |
| tests/serializers/models/natural.py:80 | SubclassNaturalKeyOptOutUser | Test model |
| tests/auth_tests/models/custom_user.py:122 | CustomUserCompositePrimaryKey | Test model |

Note: 2 of the 11 findings are NOT in test paths (django/contrib/postgres/lookups.py, django/db/models/fields/json.py) — these are real classes that should be checked separately.

#### Fix — Add test path check in detect()

The LazyClassDetector uses graph-based detection. Need to find where it iterates classes and add a test path check. Add to the detect method:

```rust
// In detect(), after getting the class from the graph:
let lower_path = class.file_path.to_lowercase();
if crate::detectors::base::is_test_path(&lower_path)
    || lower_path.starts_with("tests/")
    || lower_path.starts_with("test/")
{
    continue;
}
```

Also need to fix `base::is_test_path()` to handle relative paths:

```rust
// In base.rs is_test_path() (line 239), add:
|| lower.starts_with("tests/")
|| lower.starts_with("test/")
|| lower.starts_with("__tests__/")
|| lower.starts_with("spec/")
```

---

### 6. DebugCodeDetector — Info-Printing Utility Detection

**File:** `repotoire-cli/src/detectors/debug_code.rs`
**FPs:** 10 (9 in ogrinfo.py, 1 in archive.py)
**Root Cause:** The detector treats all `print()` calls as debug code. But `ogrinfo.py` is a GIS data inspection utility — `print()` IS the core functionality. Similarly, `archive.py:191` prints an error message during tar extraction (intentional user-facing output).

#### Fix 1 — Add info/inspection utility path exclusion

In `is_dev_only_path()` (line 49), add inspection utility patterns:

```rust
fn is_dev_only_path(path: &str) -> bool {
    let dev_patterns = [
        "/dev/",
        "/debug/",
        "/utils/debug",
        "/helpers/debug",
        "debug_",
        "_debug.",
        "/logging/",
        "/management/commands/",
        "/management/",
        "/cli/",
        "/cmd/",
        // NEW: Info/inspection utilities where print IS the feature
        "/utils/ogrinfo",
        "/utils/ogrinspect",
        "/utils/layermapping",
    ];
    dev_patterns.iter().any(|p| path.contains(p))
}
```

#### Fix 2 — Skip error-handling print() in except blocks

Add context-awareness for print() inside exception handlers:

```rust
// In the detect loop, before creating a finding:
// NEW: Skip print() in except/catch blocks (error reporting, not debug)
if trimmed.starts_with("print(") {
    // Check if we're inside an except block (look back for "except" at same or lower indent)
    let current_indent = line.len() - trimmed.len();
    for prev_idx in (0..i).rev() {
        let prev = lines[prev_idx].trim();
        if prev.is_empty() { continue; }
        let prev_indent = lines[prev_idx].len() - prev.len();
        if prev_indent < current_indent && prev.starts_with("except") {
            // This print is inside an except block — error reporting
            skip = true;
            break;
        }
        if prev_indent <= current_indent && !prev.is_empty() {
            break; // Reached same or lower indent, stop looking
        }
    }
}
```

---

### 7. StringConcatLoopDetector — Require Accumulation Pattern

**File:** `repotoire-cli/src/detectors/string_concat_loop.rs`
**FPs:** 7 of 10 findings (70% FP rate)
**Root Cause:** The detector flags ANY `+=` with a string inside a loop, but most Django cases are single concatenations per iteration (e.g., appending `" (None)"` once), not accumulation patterns.

#### Audit Results

| File | Line | FP/TP | Reason |
|------|------|-------|--------|
| inspectdb.py:209 | `field_type += "("` | FP | Single append, not accumulation |
| schema.py:228 | `definition += " " + ...` | TP | Real accumulation in loop |
| ogrinfo.py:52 | `output += " (None)"` | FP | Single conditional append |
| showmigrations.py:107 | `title += " (%s ...)"` | FP | One append per item |
| admin/sites.py:129 | `msg += "in app %r."` | FP | Error message building |
| postgresql/operations.py:374 | `prefix += " (%s)"` | FP | Single append |
| registry.py:163 | `message += " Did you mean?"` | FP | Single append with break |
| ogrinspect.py:50 | `mfield += "field"` | FP | Single append per field |
| static.py:92 | `url += "/"` | FP | Single character append |
| calendar.js:157 | `... += ...` | TP | JS string building |

#### Fix — Track whether the same variable is concatenated multiple times in the same loop

The real performance issue is when the SAME variable gets `+=` multiple times per loop iteration. A single `+=` per iteration is O(n) total, not O(n²).

```rust
// In detect(), when tracking string concat in loops:
// Instead of flagging the first += match, count how many times
// the same variable gets += in the same loop body

// NEW: Track concat variables per loop
let mut loop_concat_vars: HashMap<String, usize> = HashMap::new();

// When a string_concat match is found inside a loop:
if string_concat().is_match(line) {
    // Extract the variable name (left of +=)
    if let Some(var_name) = line.split("+=").next().map(|s| s.trim().to_string()) {
        *loop_concat_vars.entry(var_name).or_insert(0) += 1;
    }
}

// Only flag when the same variable has 2+ concatenations in the loop
// OR when a single concatenation is inside a tight inner loop
```

---

### 8. DjangoSecurityDetector — Expand ORM Internals Exclusion

**File:** `repotoire-cli/src/detectors/django_security.rs`
**FPs:** ~5 Medium/Critical (raw SQL in ORM internals)
**Root Cause:** Pass 2 added exclusions for `db/backends/` and `db/models/sql/`, but some ORM-internal paths were missed.

#### Remaining FP Findings

| File | Line | Code | Why FP |
|------|------|------|--------|
| django/db/models/constraints.py:182 | Raw SQL usage | ORM constraint implementation |
| django/db/models/fields/related_descriptors.py:1200 | Raw SQL usage | ORM descriptor implementation |
| django/db/models/query.py:1985 | Raw SQL usage | QuerySet internals |

#### Fix — Add missing ORM paths to exclusion list

Find the existing ORM path exclusion check and add the missing paths:

```rust
// In the raw SQL detection section, expand the ORM path exclusion:
let is_orm_internal = path_str.contains("db/backends/")
    || path_str.contains("db/models/sql/")
    || path_str.contains("db/models/constraints")
    || path_str.contains("db/models/fields/")
    || path_str.contains("db/models/query")       // QuerySet internals
    || path_str.contains("db/migrations/")
    || path_str.contains("contrib/postgres/");     // PostgreSQL extensions
```

---

### 9. InsecureCryptoDetector — Non-Cryptographic Usage Detection

**File:** `repotoire-cli/src/detectors/insecure_crypto.rs`
**FPs:** 3 of 5 findings
**Root Cause:** The detector already skips many non-cryptographic contexts (SQLite, checksums, etags, cache_key) but misses:
1. Template cache key generation (`cached.py:96` — SHA1 for cache keys)
2. Release checksum scripts (`do_django_release.py` — MD5/SHA1 for file integrity)

#### Audit Results

| File | Line | FP/TP | Reason |
|------|------|-------|--------|
| hashers.py:384 | SHA1 in PBKDF2 | TP | Legacy password hasher (intentional but weak) |
| hashers.py:669 | MD5 for passwords | TP | Legacy password hasher (intentional but weak) |
| cached.py:96 | SHA1 for cache keys | FP | Non-cryptographic — cache key generation |
| do_django_release.py:137 | MD5 for checksums | FP | Non-cryptographic — release file integrity |
| do_django_release.py:138 | SHA1 for checksums | FP | Non-cryptographic — release file integrity |

#### Fix — Expand non-cryptographic context detection

In `is_hash_mention_not_usage()` (line 20), add:

```rust
// Skip template/cache key generation (non-security hashing)
if lower.contains("generate_hash") || lower.contains("cache_key") || lower.contains("hexdigest") {
    // Check if context suggests non-security usage
    let context_lower = line.to_lowercase();
    if context_lower.contains("cache") || context_lower.contains("template") || context_lower.contains("loader") {
        return true;
    }
}

// Skip release/packaging scripts (checksum generation for file integrity)
// These compute verification hashes, not security hashes
```

Also add path-based exclusion for scripts/:

```rust
// In detect(), skip scripts/ directory (build/release tooling)
if path_str.contains("scripts/") || path_str.contains("script/") {
    continue;
}
```

---

## Summary

### Phase 1: Deep Refactors

| # | Detector | Fix | FPs Fixed | Complexity |
|---|----------|-----|-----------|------------|
| 1 | SecretDetector | Variable vs literal distinction | 12 | Medium |
| 2 | XxeDetector | Safe parser recognition + import filtering | 7 | Medium |
| 3 | PickleDeserializationDetector | Trusted-context path exclusion | 7 | Low |

### Phase 2: Quick Fixes

| # | Detector | Fix | FPs Fixed | Complexity |
|---|----------|-----|-----------|------------|
| 4 | GodClassDetector | Fix relative test path detection | 17 | Low |
| 5 | LazyClassDetector | Fix relative test path detection | 7-9 | Low |
| 6 | DebugCodeDetector | Info utility + except block exclusion | 10 | Low |
| 7 | StringConcatLoopDetector | Require accumulation pattern | 7 | Medium |
| 8 | DjangoSecurityDetector | Expand ORM path exclusions | 3-5 | Low |
| 9 | InsecureCryptoDetector | Non-cryptographic context expansion | 3 | Low |

### Expected Results

- **Total FPs eliminated:** ~73-77
- **Django findings:** 366 → ~291
- **Score improvement:** 98.4/A+ → likely 99+/A+
- **Test suite:** Must pass all existing 702 tests + new test cases

### Validation Plan

After all fixes:
1. `cargo test` — all tests pass
2. Run against Django: expect ~291 findings, score ≥ 99/A+
3. Run against Flask: verify no regressions (expect ~25 findings)
4. Run against FastAPI: verify no regressions (expect ~120 findings)

---

## Implementation Strategy

**Organize as 9 tasks (one per detector), following subagent-driven development:**
- Phase 1 (deep refactors) first: SecretDetector, XxeDetector, PickleDetector
- Phase 2 (quick fixes) second: GodClass, LazyClass, DebugCode, StringConcat, DjangoSecurity, InsecureCrypto
- Task 10: Validation pass on all three benchmark projects
- Each task includes: reading detector source, applying fix, adding unit tests, running `cargo test`
