# Detector Gap Analysis: Repotoire vs. Industry Standards

**Date:** 2026-02-23
**Scope:** All 112+ Repotoire detectors audited against Fowler's refactoring catalog, OWASP Top 10 (2021), CWE, SonarQube, and ESLint/TypeScript-ESLint.

---

## Table of Contents

1. [Fowler's 22 Code Smells Mapping](#1-fowlers-22-code-smells-mapping)
2. [OWASP Top 10 (2021) Mapping](#2-owasp-top-10-2021-mapping)
3. [CWE Coverage for Security Detectors](#3-cwe-coverage-for-security-detectors)
4. [SonarQube Notable Gaps](#4-sonarqube-notable-gaps)
5. [ESLint / TypeScript-ESLint Gaps](#5-eslint--typescript-eslint-gaps)
6. [Recommended New Detectors](#6-recommended-new-detectors)

---

## 1. Fowler's 22 Code Smells Mapping

Martin Fowler's *Refactoring: Improving the Design of Existing Code* (2nd edition) defines 22 classic code smells. The table below maps each to Repotoire's detector catalog.

**Coverage Legend:**
- **Full** -- Dedicated detector with graph-aware analysis
- **Partial** -- Covered indirectly or with limited scope
- **Missing** -- No detector currently addresses this smell

| # | Fowler Code Smell | Repotoire Detector(s) | Coverage | Notes |
|---|---|---|---|---|
| 1 | **Mysterious Name** | `AINamingPatternDetector`, `SingleCharNamesDetector` | Partial | SingleCharNames catches extremely short names. AINamingPattern flags AI-generated placeholder names (e.g., `handle_data`). No general detector for misleading or unclear names in human-written code. |
| 2 | **Duplicated Code** | `DuplicateCodeDetector`, `AIDuplicateBlockDetector` | Full | DuplicateCodeDetector uses structural similarity hashing. AIDuplicateBlockDetector targets AI-generated copy-paste blocks. |
| 3 | **Long Function** | `LongMethodsDetector` | Full | Configurable LOC thresholds with adaptive calibration. |
| 4 | **Long Parameter List** | `LongParameterListDetector` | Full | Graph-aware with configurable thresholds and project-type multipliers. |
| 5 | **Global Data** | `GlobalVariablesDetector` | Full | Detects global mutable state across Python, JS/TS, and Go. |
| 6 | **Mutable Data** | `MutableDefaultArgsDetector` | Partial | Only covers mutable default arguments in Python. Does not detect general mutable shared state (e.g., mutable class-level collections, unrestricted setters). |
| 7 | **Divergent Change** | -- | Missing | No detector for classes that change for multiple unrelated reasons. Related: `ShotgunSurgeryDetector` detects the inverse pattern. |
| 8 | **Shotgun Surgery** | `ShotgunSurgeryDetector` | Full | Graph-based detector that identifies changes requiring modifications across many files/modules. |
| 9 | **Feature Envy** | `FeatureEnvyDetector` | Full | Graph-aware with FunctionContext role analysis. Skips utilities, orchestrators, and facades. |
| 10 | **Data Clumps** | `DataClumpsDetector` | Full | Call-graph-enhanced detection of repeated parameter groups across functions. |
| 11 | **Primitive Obsession** | -- | Missing | No detector for excessive use of primitives instead of domain objects (e.g., passing `(lat: f64, lon: f64)` instead of `Coordinate`). |
| 12 | **Repeated Switches** | -- | Missing | No detector for duplicated switch/match statements on the same type discriminator. Could be implemented via AST pattern matching for repeated conditional structures. |
| 13 | **Loops** | -- | Partial | No detector that specifically flags imperative loops that should be replaced with pipeline operations (map/filter/reduce). `StringConcatLoopDetector` and `RegexInLoopDetector` catch specific anti-patterns inside loops but not the smell itself. |
| 14 | **Lazy Element** | `LazyClassDetector` | Full | Graph-aware: checks if methods are actually called, not just method count. |
| 15 | **Speculative Generality** | -- | Missing | No detector for unused abstractions, interfaces with single implementations, or abstract classes with no overrides. Partially covered by `DeadCodeDetector` for completely unused code. |
| 16 | **Temporary Field** | -- | Missing | No detector for fields that are only set in specific circumstances and null/undefined otherwise. |
| 17 | **Message Chains** | `MessageChainDetector` | Full | Source code pattern matching plus call graph analysis for cross-function delegation chains. |
| 18 | **Middle Man** | `MiddleManDetector` | Full | Graph-aware delegation ratio analysis per target class. |
| 19 | **Insider Trading** | `InappropriateIntimacyDetector` | Full | Detects classes with excessive mutual dependencies (Fowler renamed this from "Inappropriate Intimacy" in the 2nd edition). |
| 20 | **Large Class** | `GodClassDetector`, `LargeFilesDetector` | Full | GodClassDetector combines method count, LOC, and complexity with class-role analysis. LargeFilesDetector catches oversized files. |
| 21 | **Alternative Classes with Different Interfaces** | -- | Missing | No detector for classes that do the same thing but expose different APIs. Would require semantic similarity analysis. |
| 22 | **Data Class** | -- | Partial | `LazyClassDetector` can catch classes with minimal behavior, but there is no specific detector for classes that are pure data holders with no behavior when behavior is warranted. |
| -- | **Refused Bequest** | `RefusedBequestDetector` | Full | Graph-enhanced with polymorphic usage and inheritance depth analysis. (Dropped from Fowler's 2nd edition but still widely referenced.) |
| -- | **Comments (Deodorant)** | `CommentedCodeDetector`, `MissingDocstringsDetector`, `TodoScanner` | Partial | Covers commented-out code and missing docs. Does not detect "deodorant comments" -- comments that exist solely to explain convoluted code that should be refactored. |

### Summary

| Coverage | Count | Percentage |
|----------|-------|------------|
| Full | 13 | 59% |
| Partial | 4 | 18% |
| Missing | 5 | 23% |

**Key gaps:** Divergent Change, Primitive Obsession, Repeated Switches, Speculative Generality, and Temporary Field.

---

## 2. OWASP Top 10 (2021) Mapping

Each OWASP 2021 category is mapped to Repotoire detectors with associated CWE IDs.

| # | OWASP Category | Repotoire Detector(s) | CWE IDs Covered | Coverage | Notes |
|---|---|---|---|---|---|
| A01 | **Broken Access Control** | `PathTraversalDetector` (CWE-22), `CorsMisconfigDetector` (CWE-942), `InsecureCookieDetector` (CWE-1004, CWE-614), `DjangoSecurityDetector` (CWE-352), `ExpressSecurityDetector` (CWE-306) | CWE-22, CWE-306, CWE-352, CWE-614, CWE-942, CWE-1004 | Partial | Missing: Insecure Direct Object Reference (IDOR, CWE-639), privilege escalation (CWE-269), forced browsing (CWE-425), missing function-level access control (CWE-285). |
| A02 | **Cryptographic Failures** | `InsecureCryptoDetector` (CWE-327, CWE-328), `InsecureRandomDetector` (CWE-330), `CleartextCredentialsDetector` (CWE-312), `InsecureTlsDetector` (CWE-295), `JwtWeakDetector` (CWE-327), `HardcodedIpsDetector` (CWE-798), `SecretDetector` (CWE-798) | CWE-295, CWE-312, CWE-327, CWE-328, CWE-330, CWE-798 | Strong | Missing: Insufficient key length (CWE-326), missing encryption of sensitive data in transit (CWE-319). Otherwise comprehensive. |
| A03 | **Injection** | `SQLInjectionDetector` (CWE-89), `CommandInjectionDetector` (CWE-78), `XssDetector` (CWE-79), `EvalDetector` (CWE-94), `NosqlInjectionDetector` (CWE-943), `UnsafeTemplateDetector` (CWE-1336), `LogInjectionDetector` (CWE-117), `XxeDetector` (CWE-611), `PrototypePollutionDetector` (CWE-1321), `SsrfDetector` (CWE-918), `GHActionsInjectionDetector` (CWE-78) | CWE-78, CWE-79, CWE-89, CWE-94, CWE-117, CWE-611, CWE-918, CWE-943, CWE-1321, CWE-1336 | Strong | Excellent injection coverage. All detectors enhanced with graph-based taint analysis. Missing: LDAP injection (CWE-90), XPath injection (CWE-643), header injection (CWE-113). |
| A04 | **Insecure Design** | `GodClassDetector`, `CircularDependencyDetector`, `ArchitecturalBottleneckDetector`, `ModuleCohesionDetector`, `ShotgunSurgeryDetector` | N/A (design-level) | Partial | Repotoire's architecture detectors address structural design issues. Missing: threat modeling integration, secure design pattern validation, business logic flaw detection. |
| A05 | **Security Misconfiguration** | `DjangoSecurityDetector` (CWE-16, CWE-215), `ExpressSecurityDetector` (CWE-693, CWE-770, CWE-400, CWE-209), `CorsMisconfigDetector` (CWE-942), `DebugCodeDetector` (CWE-489), `TestInProductionDetector` (CWE-489), `HardcodedIpsDetector` (CWE-798) | CWE-16, CWE-209, CWE-215, CWE-489, CWE-693, CWE-770, CWE-798, CWE-942 | Partial | Good for Django and Express. Missing: default credentials (CWE-1188), unnecessary features enabled (CWE-1188), misconfigured HTTP headers (CSP, HSTS, X-Frame-Options beyond Helmet detection), cloud-specific misconfigurations. |
| A06 | **Vulnerable and Outdated Components** | `DepAuditDetector` | Multiple (via OSV.dev) | Full | Parses lockfiles (package-lock.json, Cargo.lock, requirements.txt, go.sum) and queries OSV.dev vulnerability database. Multi-ecosystem coverage. |
| A07 | **Identification and Authentication Failures** | `JwtWeakDetector` (CWE-327), `SecretDetector` (CWE-798), `CleartextCredentialsDetector` (CWE-312), `InsecureCookieDetector` (CWE-614), `ExpressSecurityDetector` (auth middleware check) | CWE-312, CWE-327, CWE-614, CWE-798 | Partial | Missing: weak password policies (CWE-521), credential stuffing protection (CWE-307 -- brute force), session fixation (CWE-384), missing MFA checks. |
| A08 | **Software and Data Integrity Failures** | `InsecureDeserializeDetector` (CWE-502), `PickleDeserializationDetector` (CWE-502), `TorchLoadUnsafeDetector` (CWE-502), `GHActionsInjectionDetector` (CWE-78), `DepAuditDetector` | CWE-78, CWE-502 | Partial | Good deserialization coverage across Python (pickle, yaml.load), Java (ObjectInputStream), Ruby (Marshal), PHP (unserialize). Missing: unsigned CI/CD artifact verification, integrity checking for downloads, subresource integrity (SRI). |
| A09 | **Security Logging and Monitoring Failures** | `LogInjectionDetector` (CWE-117) | CWE-117 | Minimal | Only detects log injection. Missing: insufficient logging of security events, missing audit trails, alerting on suspicious activity, sensitive data in logs. |
| A10 | **Server-Side Request Forgery (SSRF)** | `SsrfDetector` (CWE-918) | CWE-918 | Full | Taint-analysis-enhanced detection of SSRF via HTTP clients (requests, fetch, axios, urllib, etc.). |

### Summary

| Coverage | Count | Percentage |
|----------|-------|------------|
| Full / Strong | 4 | 40% |
| Partial | 5 | 50% |
| Minimal | 1 | 10% |

---

## 3. CWE Coverage for Security Detectors

Each security-focused detector with its mapped CWE IDs.

| Detector | CWE ID(s) | CWE Name | Taint-Enhanced |
|---|---|---|---|
| `SQLInjectionDetector` | CWE-89 | SQL Injection | Yes |
| `CommandInjectionDetector` | CWE-78 | OS Command Injection | Yes |
| `XssDetector` | CWE-79 | Cross-site Scripting | Yes |
| `EvalDetector` | CWE-94, CWE-78, CWE-502 | Code Injection, OS Command Injection, Deserialization of Untrusted Data | Yes |
| `PathTraversalDetector` | CWE-22 | Path Traversal | Yes |
| `SsrfDetector` | CWE-918 | Server-Side Request Forgery | Yes |
| `UnsafeTemplateDetector` | CWE-79, CWE-1336 | XSS, Server-Side Template Injection | No |
| `PickleDeserializationDetector` | CWE-502 | Deserialization of Untrusted Data | No |
| `InsecureDeserializeDetector` | CWE-502 | Deserialization of Untrusted Data | No |
| `SecretDetector` | CWE-798 | Use of Hard-coded Credentials | No |
| `CleartextCredentialsDetector` | CWE-312 | Cleartext Storage of Sensitive Information | No |
| `HardcodedIpsDetector` | CWE-798 | Use of Hard-coded Credentials | No |
| `InsecureCryptoDetector` | CWE-327, CWE-328 | Use of a Broken or Risky Cryptographic Algorithm, Reversible One-Way Hash | No |
| `InsecureRandomDetector` | CWE-330 | Use of Insufficiently Random Values | No |
| `InsecureTlsDetector` | CWE-295 | Improper Certificate Validation | No |
| `InsecureCookieDetector` | CWE-614, CWE-1004 | Sensitive Cookie Without Secure Flag, Sensitive Cookie Without HttpOnly Flag | No |
| `JwtWeakDetector` | CWE-327 | Use of a Broken or Risky Cryptographic Algorithm | No |
| `CorsMisconfigDetector` | CWE-942 | Permissive Cross-domain Policy | No |
| `XxeDetector` | CWE-611 | Improper Restriction of XML External Entity Reference | No |
| `NosqlInjectionDetector` | CWE-943 | Improper Neutralization of Special Elements in Data Query Logic | No |
| `LogInjectionDetector` | CWE-117 | Improper Output Neutralization for Logs | No |
| `PrototypePollutionDetector` | CWE-1321 | Improperly Controlled Modification of Object Prototype Attributes | No |
| `RegexDosDetector` | CWE-1333 | Inefficient Regular Expression Complexity | No |
| `GHActionsInjectionDetector` | CWE-78 | OS Command Injection (via GitHub Actions) | No |
| `DjangoSecurityDetector` | CWE-16, CWE-89, CWE-215, CWE-352, CWE-798 | Configuration, SQL Injection, Information Exposure Through Debug Information, CSRF, Hard-coded Credentials | No |
| `ExpressSecurityDetector` | CWE-209, CWE-306, CWE-400, CWE-693, CWE-770 | Information Exposure Through Error Message, Missing Authentication, Uncontrolled Resource Consumption, Protection Mechanism Failure, Allocation of Resources Without Limits | No |
| `DepAuditDetector` | Multiple (via OSV.dev) | Varies per advisory | No |
| `TorchLoadUnsafeDetector` | CWE-502 | Deserialization of Untrusted Data | No |

### Additional CWE coverage from non-security detectors

| Detector | CWE ID | CWE Name |
|---|---|---|
| `EmptyCatchDetector` | CWE-390 | Detection of Error Condition Without Action |
| `UnreachableCodeDetector` | CWE-561 | Dead Code |
| `DeadCodeDetector` | CWE-561 | Dead Code |
| `DeadStoreDetector` | CWE-563 | Assignment to Variable without Use |
| `MutableDefaultArgsDetector` | CWE-1188 | Insecure Default Initialization of Resource |
| `InconsistentReturnsDetector` | CWE-394 | Unexpected Status Code or Return Value |
| `UnhandledPromiseDetector` | CWE-755 | Improper Handling of Exceptional Conditions |
| `InfiniteLoopDetector` | CWE-835 | Loop with Unreachable Exit Condition |
| `SyncInAsyncDetector` | CWE-400 | Uncontrolled Resource Consumption |
| `DebugCodeDetector` | CWE-489 | Active Debug Code |
| `TestInProductionDetector` | CWE-489 | Active Debug Code |
| `UnsafeWithoutSafetyCommentDetector` | CWE-119 | Improper Restriction of Operations within the Bounds of a Memory Buffer |
| `MutexPoisoningRiskDetector` | CWE-667 | Improper Locking |

### Unique CWE IDs Covered: 34

Full list: CWE-16, CWE-22, CWE-78, CWE-79, CWE-89, CWE-94, CWE-117, CWE-119, CWE-209, CWE-215, CWE-295, CWE-306, CWE-312, CWE-327, CWE-328, CWE-330, CWE-352, CWE-390, CWE-394, CWE-400, CWE-489, CWE-502, CWE-561, CWE-563, CWE-611, CWE-614, CWE-667, CWE-693, CWE-755, CWE-770, CWE-798, CWE-835, CWE-918, CWE-942, CWE-943, CWE-1004, CWE-1188, CWE-1321, CWE-1333, CWE-1336.

---

## 4. SonarQube Notable Gaps

SonarQube has 600+ rules across languages. The following are high-impact rules categories where Repotoire has notable gaps. Rules already covered by Repotoire detectors are excluded.

### 4.1 Bug-Detection Rules (High Impact)

| SonarQube Rule Category | Example Rules | Repotoire Status | Priority |
|---|---|---|---|
| **Null/undefined dereference** | S2259 (Null pointer dereference), S3655 (Optional value access) | Missing | High -- one of the most common runtime crash causes. Requires type-aware analysis or SSA-based null tracking. |
| **Resource leaks** | S2095 (Resources should be closed), S5042 (File descriptors not closed) | Missing | High -- unclosed files, connections, streams. Detectable via AST pattern matching for `open()` without context manager/try-with-resources. |
| **Array/index out of bounds** | S3981 (Collection size check), S2583 (Conditions always true/false) | Missing | Medium -- requires data flow analysis. |
| **Identical conditions** | S1764 (Identical expressions on both sides of operator), S1862 (Same condition in if/else-if) | Missing | Medium -- detectable via AST comparison. |
| **Cognitive complexity** | S3776 (Cognitive complexity threshold) | Partial | Medium -- `DeepNestingDetector` and `GodClassDetector` address complexity but not SonarQube's specific cognitive complexity metric. |
| **Type coercion bugs** | S3403 (Strict equality), S1244 (Floating point equality) | Partial | Medium -- `ImplicitCoercionDetector` covers JS type coercion; `NanEqualityDetector` covers NaN comparison. Missing: floating point equality. |

### 4.2 Security Rules (High Impact)

| SonarQube Rule Category | Example Rules | Repotoire Status | Priority |
|---|---|---|---|
| **Open redirect** | S5146 (Server-side redirect with user input) | Missing | High -- CWE-601. Common web vulnerability. Detectable via taint analysis (user input to redirect/302 response). |
| **LDAP injection** | S2078 (LDAP injection) | Missing | Medium -- CWE-90. Less common but critical in enterprise codebases. |
| **HTTP response splitting** | S5167 (HTTP header injection) | Missing | Medium -- CWE-113. Detectable via taint analysis. |
| **Unvalidated redirects** | S5131 (Open redirect) | Missing | High -- see open redirect above. |
| **Information exposure** | S1313 (Hardcoded IP), S2068 (Hardcoded password) | Covered | -- `HardcodedIpsDetector`, `SecretDetector`, `CleartextCredentialsDetector`. |

### 4.3 Code Quality Rules (Medium Impact)

| SonarQube Rule Category | Example Rules | Repotoire Status | Priority |
|---|---|---|---|
| **Unused function parameters** | S1172 (Unused parameter) | Missing | Medium -- different from unused imports. Detectable via AST analysis. |
| **Unused local variables** | S1481 (Unused local variable) | Partial | Medium -- `DeadStoreDetector` catches assignments without use; `UnusedImportsDetector` handles imports. No general unused-local detector. |
| **Collapsible if statements** | S1066 (Nested ifs that can be merged) | Missing | Low -- pure readability improvement. |
| **Return of boolean literal from conditional** | S1126 (Return boolean instead of if/else true/false) | Missing | Low -- stylistic, easily auto-fixable. |
| **Overly complex boolean expressions** | S1067 (Too many boolean operators) | Missing | Low -- partially addressed by `DeepNestingDetector`. |
| **Missing break in switch** | S128 (Switch case fallthrough) | Missing | Medium -- can cause subtle bugs. Detectable via AST. |
| **Empty function body** | S1186 (Methods should not be empty) | Partial | Low -- `EmptyCatchDetector` catches empty catch blocks but not empty methods generally. |

### 4.4 Reliability Rules

| SonarQube Rule Category | Example Rules | Repotoire Status | Priority |
|---|---|---|---|
| **Thread safety** | S2885 (Non-thread-safe fields), S2696 (Instance field written in static method) | Partial | High -- Rust-specific `MutexPoisoningRiskDetector` exists. No Python/JS thread safety analysis. |
| **Race conditions** | S3060 (Concurrent map access) | Missing | High for Go/Rust -- detectable via AST pattern analysis. |
| **Hardcoded credentials in connection strings** | S2068 | Covered | -- `SecretDetector`, `CleartextCredentialsDetector`. |

---

## 5. ESLint / TypeScript-ESLint Gaps

Analysis of popular ESLint recommended rules and typescript-eslint strict rules that Repotoire's TS/JS analysis does not cover.

### 5.1 ESLint Core Recommended Rules -- Gaps

| ESLint Rule | Description | Repotoire Status | Priority |
|---|---|---|---|
| `no-undef` | Disallow undeclared variables | Missing | High -- requires scope analysis. |
| `no-unused-vars` | Disallow unused variables | Partial | Medium -- `UnusedImportsDetector` handles imports only. |
| `no-constant-condition` | Disallow constant conditions (`if (true)`) | Partial | Low -- `UnreachableCodeDetector` catches some cases. |
| `no-dupe-keys` | Disallow duplicate keys in objects | Missing | Medium -- can cause silent bugs. |
| `no-dupe-args` | Disallow duplicate function arguments | Missing | Medium -- language parser could flag. |
| `no-func-assign` | Disallow reassigning function declarations | Missing | Low. |
| `no-inner-declarations` | Disallow function/variable declarations in nested blocks | Missing | Low. |
| `no-irregular-whitespace` | Disallow non-standard whitespace | Missing | Low -- formatting concern. |
| `no-sparse-arrays` | Disallow sparse arrays `[1,,3]` | Missing | Low. |
| `no-unexpected-multiline` | Disallow confusing multiline expressions | Missing | Low -- ASI hazard. |
| `no-unsafe-negation` | Disallow negating the left operand of relational operators | Missing | Medium -- `!x instanceof Foo` is a common bug. |
| `no-prototype-builtins` | Disallow calling Object.prototype methods directly on objects | Missing | Medium -- related to `PrototypePollutionDetector` but different scope. |
| `use-isnan` | Require `isNaN()` instead of `=== NaN` | Partial | Low -- `NanEqualityDetector` covers Python/NumPy; no JS-specific coverage. |
| `valid-typeof` | Enforce comparing typeof to valid strings | Missing | Medium -- `typeof x === "stirng"` is a common typo. |

### 5.2 TypeScript-ESLint Strict Rules -- Gaps

| Rule | Description | Repotoire Status | Priority |
|---|---|---|---|
| `@typescript-eslint/no-explicit-any` | Disallow `any` type | Missing | High -- widely enforced in TS projects. |
| `@typescript-eslint/no-unsafe-assignment` | Disallow assigning `any` to typed variables | Missing | High -- type safety. |
| `@typescript-eslint/no-unsafe-member-access` | Disallow member access on `any` typed values | Missing | High. |
| `@typescript-eslint/no-unsafe-call` | Disallow calling `any` typed values | Missing | High. |
| `@typescript-eslint/no-unsafe-return` | Disallow returning `any` from functions | Missing | High. |
| `@typescript-eslint/no-floating-promises` | Require Promises to be awaited or returned | Partial | High -- `MissingAwaitDetector` + `UnhandledPromiseDetector` cover some cases. |
| `@typescript-eslint/no-misused-promises` | Disallow Promises in places not designed to handle them | Missing | High -- e.g., `if (asyncFn())`. |
| `@typescript-eslint/strict-boolean-expressions` | Require booleans in boolean contexts | Missing | Medium -- overlaps with `ImplicitCoercionDetector`. |
| `@typescript-eslint/no-unnecessary-condition` | Disallow conditionals that are always truthy/falsy | Missing | Medium -- requires type information. |
| `@typescript-eslint/no-non-null-assertion` | Disallow non-null assertions (`!`) | Missing | Medium. |
| `@typescript-eslint/prefer-nullish-coalescing` | Prefer `??` over `\|\|` for nullish checks | Missing | Low -- stylistic. |
| `@typescript-eslint/consistent-type-imports` | Enforce consistent type import style | Missing | Low -- stylistic. |

### 5.3 Popular Plugin Rules -- Gaps

| Plugin / Rule | Description | Repotoire Status | Priority |
|---|---|---|---|
| `eslint-plugin-react/jsx-no-target-blank` | Require `rel="noopener"` on external links | Missing | Medium -- security (reverse tabnabbing). |
| `eslint-plugin-react/no-direct-mutation-state` | Disallow direct state mutation | Missing | Medium -- React anti-pattern. |
| `eslint-plugin-react/no-array-index-key` | Disallow array index as React key | Missing | Low -- performance concern. |
| `eslint-plugin-import/no-cycle` | Detect import cycles | Covered | -- `CircularDependencyDetector`. |
| `eslint-plugin-import/no-unused-modules` | Detect unused exports | Missing | Medium -- related to `DeadCodeDetector` scope. |
| `eslint-plugin-import/no-default-export` | Disallow default exports | Missing | Low -- style preference. |
| `eslint-plugin-security/detect-object-injection` | Detect variable-keyed object access | Missing | Medium -- security concern. |
| `eslint-plugin-security/detect-non-literal-regexp` | Detect non-literal RegExp construction | Partial | Low -- `RegexDosDetector` covers dangerous patterns. |

### Summary

Repotoire's TS/JS analysis is strong on security-level concerns (injection, XSS, SSRF, React hooks rules) but lacks the TypeScript type-system-aware rules that `typescript-eslint` provides. This is expected given that Repotoire uses regex/AST pattern matching rather than TypeScript's type checker. The most impactful gap is the `no-explicit-any` family of rules, which requires TypeScript compiler integration.

---

## 6. Recommended New Detectors

Priority-ranked list of missing detectors worth implementing, considering impact, feasibility, and uniqueness (whether existing tools already cover them well).

### Tier 1: High Priority (Significant gap, high impact)

| # | Detector Name | Category | Addresses | Complexity | Rationale |
|---|---|---|---|---|---|
| 1 | **OpenRedirectDetector** | Security | CWE-601, OWASP A01 | Medium | Common web vulnerability. Extend existing taint analysis to trace user input to redirect responses. No current coverage. |
| 2 | **ResourceLeakDetector** | Bug | SonarQube S2095 | Medium | Unclosed files, connections, and streams are a top production bug category. AST pattern matching for `open()` without context manager, `Connection` without `close()`, etc. |
| 3 | **DivergentChangeDetector** | Code Smell | Fowler #7 | Medium | Uses git history + graph to find classes changed for multiple unrelated reasons. Complements existing `ShotgunSurgeryDetector` (its inverse). |
| 4 | **NullDereferenceDetector** | Bug | CWE-476, SonarQube S2259 | High | Requires data flow analysis. Could start with simple patterns: accessing `.property` on a value returned by a function that can return null/None/nil. |
| 5 | **SpeculativeGeneralityDetector** | Code Smell | Fowler #15 | Low | Detect interfaces with single implementation, abstract classes with no concrete subclasses, unused type parameters. Graph query: find interface nodes with exactly one IMPLEMENTS relationship. |

### Tier 2: Medium Priority (Valuable but either lower impact or partially covered)

| # | Detector Name | Category | Addresses | Complexity | Rationale |
|---|---|---|---|---|---|
| 6 | **PrimitiveObsessionDetector** | Code Smell | Fowler #11 | Medium | Detect functions taking 3+ same-type primitive parameters (e.g., `fn draw(x: f64, y: f64, w: f64, h: f64)`). Uses graph parameter type information. |
| 7 | **RepeatedSwitchDetector** | Code Smell | Fowler #12 | Medium | AST-based detection of switch/match statements on the same discriminator appearing in multiple locations. |
| 8 | **UnusedParameterDetector** | Code Quality | SonarQube S1172 | Low | Detect function parameters never referenced in the function body. Simple AST analysis. |
| 9 | **CognitiveComplexityDetector** | Code Quality | SonarQube S3776 | Medium | Implement SonarQube's cognitive complexity metric (different from cyclomatic complexity). Better correlates with human comprehension difficulty. |
| 10 | **IdenticalConditionDetector** | Bug | SonarQube S1764 | Low | Detect identical expressions on both sides of operators (`x == x`, `a && a`) and duplicate if/else-if conditions. Pure AST analysis. |
| 11 | **SwitchFallthroughDetector** | Bug | SonarQube S128, CWE-484 | Low | Detect missing break/return in switch cases (JS/TS, Go). Simple AST pattern. |
| 12 | **HeaderInjectionDetector** | Security | CWE-113 | Medium | Extend taint analysis to detect user input flowing to HTTP response headers. |

### Tier 3: Lower Priority (Nice to have, narrow scope, or well-covered by other tools)

| # | Detector Name | Category | Addresses | Complexity | Rationale |
|---|---|---|---|---|---|
| 13 | **TemporaryFieldDetector** | Code Smell | Fowler #16 | High | Detect fields that are only set in certain methods and checked for null elsewhere. Requires inter-method data flow analysis within a class. |
| 14 | **LdapInjectionDetector** | Security | CWE-90 | Low | Pattern-match for LDAP search with string concatenation. Narrow scope but critical in enterprise. |
| 15 | **TypeScriptAnyDetector** | Code Quality | TS-ESLint strict | Medium | Detect `any` type usage in TypeScript files. Could use regex `:\s*any\b` as a starting point but full coverage requires TS type resolution. |
| 16 | **FloatingPointEqualityDetector** | Bug | CWE-1077, SonarQube S1244 | Low | Detect direct floating point comparison (`==`, `!=`) instead of epsilon-based comparison. Simple regex/AST pattern. |
| 17 | **RaceConditionDetector** | Bug | CWE-362 | High | Detect concurrent access to shared state without synchronization. High complexity for general case; could start with Go-specific patterns (concurrent map access). |
| 18 | **SessionFixationDetector** | Security | CWE-384, OWASP A07 | Medium | Detect session IDs not being regenerated after authentication. Framework-specific patterns. |

### Implementation Effort Key

| Complexity | Estimated Effort | Description |
|---|---|---|
| Low | 1-3 days | Pattern matching or simple AST analysis, single language |
| Medium | 3-7 days | Requires graph queries, taint analysis extension, or multi-language support |
| High | 1-3 weeks | Requires data flow analysis, type inference, or cross-function tracking |

---

## Appendix: Full Detector Inventory

For reference, the complete list of 112+ Repotoire detectors organized by category.

### Code Smell Detectors (11)
`CircularDependencyDetector`, `GodClassDetector`, `LongParameterListDetector`, `DataClumpsDetector`, `DeadCodeDetector`, `FeatureEnvyDetector`, `InappropriateIntimacyDetector`, `LazyClassDetector`, `MessageChainDetector`, `MiddleManDetector`, `RefusedBequestDetector`

### AI-Specific Detectors (6)
`AIBoilerplateDetector`, `AIChurnDetector`, `AIComplexitySpikeDetector`, `AIDuplicateBlockDetector`, `AIMissingTestsDetector`, `AINamingPatternDetector`

### ML/Data Science Detectors (8)
`TorchLoadUnsafeDetector`, `NanEqualityDetector`, `MissingZeroGradDetector`, `ForwardMethodDetector`, `MissingRandomSeedDetector`, `ChainIndexingDetector`, `RequireGradTypoDetector`, `DeprecatedTorchApiDetector`

### Architecture Detectors (6)
`ArchitecturalBottleneckDetector`, `CoreUtilityDetector`, `DegreeCentralityDetector`, `InfluentialCodeDetector`, `ModuleCohesionDetector`, `ShotgunSurgeryDetector`

### Security Detectors (24)
`EvalDetector`, `PickleDeserializationDetector`, `SQLInjectionDetector`, `UnsafeTemplateDetector`, `SecretDetector`, `PathTraversalDetector`, `CommandInjectionDetector`, `SsrfDetector`, `RegexDosDetector`, `InsecureCryptoDetector`, `XssDetector`, `HardcodedIpsDetector`, `InsecureRandomDetector`, `CorsMisconfigDetector`, `XxeDetector`, `InsecureDeserializeDetector`, `CleartextCredentialsDetector`, `InsecureCookieDetector`, `JwtWeakDetector`, `PrototypePollutionDetector`, `NosqlInjectionDetector`, `LogInjectionDetector`, `InsecureTlsDetector`, `DepAuditDetector`

### Code Quality Detectors (22)
`EmptyCatchDetector`, `TodoScanner`, `DeepNestingDetector`, `MagicNumbersDetector`, `LargeFilesDetector`, `MissingDocstringsDetector`, `UnusedImportsDetector`, `CommentedCodeDetector`, `LongMethodsDetector`, `DuplicateCodeDetector`, `UnreachableCodeDetector`, `StringConcatLoopDetector`, `WildcardImportsDetector`, `MutableDefaultArgsDetector`, `GlobalVariablesDetector`, `ImplicitCoercionDetector`, `SingleCharNamesDetector`, `BroadExceptionDetector`, `BooleanTrapDetector`, `InconsistentReturnsDetector`, `DeadStoreDetector`, `HardcodedTimeoutDetector`

### Performance Detectors (3)
`SyncInAsyncDetector`, `NPlusOneDetector`, `RegexInLoopDetector`

### Async/Promise Detectors (3)
`MissingAwaitDetector`, `UnhandledPromiseDetector`, `CallbackHellDetector`

### Testing Detectors (1)
`TestInProductionDetector`

### Framework-Specific Detectors (3)
`ReactHooksDetector`, `DjangoSecurityDetector`, `ExpressSecurityDetector`

### Rust-Specific Detectors (7)
`UnwrapWithoutContextDetector`, `UnsafeWithoutSafetyCommentDetector`, `CloneInHotPathDetector`, `MissingMustUseDetector`, `BoxDynTraitDetector`, `MutexPoisoningRiskDetector`, `PanicDensityDetector`

### CI/CD Detectors (1)
`GHActionsInjectionDetector`

### Misc Detectors (3+)
`GeneratorMisuseDetector`, `InfiniteLoopDetector`, `DebugCodeDetector`, `SurprisalDetector` (conditional)
