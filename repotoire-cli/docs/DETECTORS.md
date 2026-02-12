# Detectors Reference

Repotoire includes **95+ detectors** across multiple categories. All detectors run locally with no external API dependencies (except optional AI fixes).

---

## Table of Contents

- [Security Detectors](#security-detectors)
- [Code Smells](#code-smells)
- [Architecture & Design](#architecture--design)
- [AI Code Pattern Detection](#ai-code-pattern-detection)
- [Performance](#performance)
- [Code Quality](#code-quality)
- [Framework-Specific](#framework-specific)
- [External Tool Integration](#external-tool-integration)
- [Configuration](#configuration)

---

## Security Detectors

These detectors find security vulnerabilities in your code.

| Detector | Description | Severity |
|----------|-------------|----------|
| `SQLInjectionDetector` | Detects SQL queries with string interpolation/concatenation (CWE-89) | Critical |
| `CommandInjectionDetector` | Finds shell command injection via `os.system()`, `subprocess` with user input | Critical |
| `PathTraversalDetector` | Detects directory traversal attacks (`../` in file paths) | Critical |
| `EvalDetector` | Finds dangerous `eval()`, `exec()`, and code execution with user input | Critical |
| `PickleDeserializationDetector` | Detects unsafe pickle/yaml/marshal deserialization | Critical |
| `XssDetector` | Cross-site scripting via unescaped HTML output | High |
| `SsrfDetector` | Server-side request forgery via user-controlled URLs | High |
| `XxeDetector` | XML External Entity injection in XML parsers | High |
| `InsecureDeserializeDetector` | Unsafe deserialization in multiple formats | High |
| `NosqlInjectionDetector` | NoSQL injection in MongoDB/Mongoose queries | High |
| `PrototypePollutionDetector` | JavaScript prototype pollution attacks | High |
| `SecretDetector` | Hardcoded API keys, passwords, tokens in code | High |
| `CleartextCredentialsDetector` | Passwords/secrets stored in plain text | High |
| `InsecureCryptoDetector` | Weak cryptographic algorithms (MD5, SHA1, DES) | Medium |
| `InsecureRandomDetector` | Predictable random number generation | Medium |
| `JwtWeakDetector` | Weak JWT configurations (none algorithm, weak secrets) | Medium |
| `InsecureCookieDetector` | Cookies without Secure/HttpOnly/SameSite flags | Medium |
| `CorsMisconfigDetector` | Overly permissive CORS configurations | Medium |
| `HardcodedIpsDetector` | Hardcoded IP addresses | Low |
| `LogInjectionDetector` | Log injection vulnerabilities | Low |
| `TaintDetector` | Graph-based taint analysis tracking data flow | Varies |
| `UnsafeTemplateDetector` | Unsafe template rendering (Jinja2, EJS, etc.) | High |

---

## Code Smells

Classic code smells that indicate design problems.

| Detector | Description | Severity |
|----------|-------------|----------|
| `GodClassDetector` | Classes with too many methods, lines, or complexity | High |
| `LongMethodsDetector` | Functions that are too long (default: >50 lines) | Medium |
| `LongParameterListDetector` | Functions with too many parameters (default: >6) | Medium |
| `DeepNestingDetector` | Deeply nested code blocks | Medium |
| `MessageChainDetector` | Long method chains (`a.b.c.d.e()`) | Low |
| `FeatureEnvyDetector` | Methods that use more data from other classes | Medium |
| `InappropriateIntimacyDetector` | Classes that access each other's internals excessively | Medium |
| `DataClumpsDetector` | Groups of data that appear together frequently | Low |
| `MiddleManDetector` | Classes that only delegate to other classes | Low |
| `LazyClassDetector` | Classes that don't do enough | Low |
| `RefusedBequestDetector` | Subclasses that don't use inherited methods | Low |
| `ShotgunSurgeryDetector` | Changes that require many small edits across files | Medium |
| `DuplicateCodeDetector` | Near-duplicate code blocks | Medium |

---

## Architecture & Design

Detectors that analyze the overall architecture of your codebase.

| Detector | Description | Severity |
|----------|-------------|----------|
| `CircularDependencyDetector` | Circular imports using Tarjan's SCC algorithm | High |
| `ArchitecturalBottleneckDetector` | Files/classes with high betweenness centrality | Medium |
| `DegreeCentralityDetector` | Hub nodes with excessive connections | Medium |
| `ModuleCohesionDetector` | Modules with low internal cohesion | Medium |
| `CoreUtilityDetector` | Identifies core vs utility code | Info |
| `InfluentialCodeDetector` | Code that affects many other parts | Info |

---

## AI Code Pattern Detection

Detectors specifically designed to find patterns common in AI-generated code.

| Detector | Description | Severity |
|----------|-------------|----------|
| `AIComplexitySpikeDetector` | Sudden complexity increases (AI tends to generate complex solutions) | Medium |
| `AIChurnDetector` | High churn rate (AI code often gets rewritten quickly) | Medium |
| `AIBoilerplateDetector` | Excessive boilerplate patterns typical of AI | Low |
| `AIDuplicateBlockDetector` | Near-duplicate blocks from copy-paste AI suggestions | Medium |
| `AINamingPatternDetector` | Generic naming patterns (`data`, `result`, `temp`) | Low |
| `AIMissingTestsDetector` | New functions without corresponding tests | Medium |

---

## Performance

Detectors that find performance issues.

| Detector | Description | Severity |
|----------|-------------|----------|
| `NPlusOneDetector` | N+1 query patterns in database access | High |
| `SyncInAsyncDetector` | Blocking I/O calls in async functions | High |
| `RegexInLoopDetector` | Regex compilation inside loops | Medium |
| `StringConcatLoopDetector` | String concatenation in loops (use StringBuilder) | Medium |
| `InfiniteLoopDetector` | Potential infinite loops | High |
| `RegexDosDetector` | ReDoS-vulnerable regular expressions | Medium |

---

## Code Quality

General code quality issues.

| Detector | Description | Severity |
|----------|-------------|----------|
| `DeadCodeDetector` | Unreachable/unused code (graph-based analysis) | Low |
| `UnreachableCodeDetector` | Code after return/throw statements | Low |
| `DeadStoreDetector` | Variables assigned but never used | Low |
| `EmptyCatchDetector` | Empty catch/except blocks | Medium |
| `BroadExceptionDetector` | Catching base Exception class | Low |
| `TodoScanner` | TODO/FIXME/HACK comments | Info |
| `CommentedCodeDetector` | Commented-out code blocks | Low |
| `MagicNumbersDetector` | Magic numbers without constants | Low |
| `LargeFilesDetector` | Files exceeding line thresholds | Low |
| `MissingDocstringsDetector` | Public functions without docstrings | Low |
| `UnusedImportsDetector` | Imports that are never used | Low |
| `WildcardImportsDetector` | `from x import *` patterns | Low |
| `GlobalVariablesDetector` | Global variable usage | Low |
| `MutableDefaultArgsDetector` | Mutable default arguments in Python | Medium |
| `SingleCharNamesDetector` | Single-character variable names | Low |
| `BooleanTrapDetector` | Boolean parameters without clear meaning | Low |
| `InconsistentReturnsDetector` | Functions with inconsistent return types | Medium |
| `HardcodedTimeoutDetector` | Hardcoded timeout values | Low |
| `ImplicitCoercionDetector` | JavaScript implicit type coercion | Low |
| `DebugCodeDetector` | Debug statements left in code | Low |
| `TestInProductionDetector` | Test code patterns in production files | Medium |

---

## Async & Promise Patterns

| Detector | Description | Severity |
|----------|-------------|----------|
| `MissingAwaitDetector` | Async function calls without await | High |
| `UnhandledPromiseDetector` | Promises without .catch() or try/catch | Medium |
| `CallbackHellDetector` | Deeply nested callbacks | Medium |
| `GeneratorMisuseDetector` | Incorrect generator/yield usage | Medium |

---

## Framework-Specific

| Detector | Description | Severity |
|----------|-------------|----------|
| `ReactHooksDetector` | React hooks rules violations | High |
| `DjangoSecurityDetector` | Django-specific security issues | High |
| `ExpressSecurityDetector` | Express.js security misconfigurations | High |
| `GHActionsInjectionDetector` | GitHub Actions command injection | Critical |

---

## External Tool Integration

These detectors wrap external tools and require those tools to be installed.

### Python Tools

| Detector | Tool | Description |
|----------|------|-------------|
| `BanditDetector` | [Bandit](https://bandit.readthedocs.io/) | Python security analysis |
| `RuffLintDetector` | [Ruff](https://github.com/astral-sh/ruff) | Fast Python linting (100x faster than Pylint) |
| `RuffImportDetector` | Ruff | Unused import detection |
| `MypyDetector` | [Mypy](https://mypy.readthedocs.io/) | Python type checking |
| `RadonDetector` | [Radon](https://radon.readthedocs.io/) | Python complexity metrics |
| `VultureDetector` | [Vulture](https://github.com/jendrikseipp/vulture) | Dead Python code detection |
| `PylintDetector` | [Pylint](https://pylint.org/) | Comprehensive Python linting |

### JavaScript/TypeScript Tools

| Detector | Tool | Description |
|----------|------|-------------|
| `ESLintDetector` | [ESLint](https://eslint.org/) | JavaScript/TypeScript linting |
| `TscDetector` | TypeScript | TypeScript type checking |
| `NpmAuditDetector` | npm | Dependency vulnerability scanning |

### Cross-Language Tools

| Detector | Tool | Description |
|----------|------|-------------|
| `SemgrepDetector` | [Semgrep](https://semgrep.dev/) | Security pattern matching |
| `JscpdDetector` | [jscpd](https://github.com/kucherenko/jscpd) | Cross-language duplicate detection |

---

## Configuration

### Per-Project Configuration

Configure detectors in `repotoire.toml`:

```toml
[detectors.god-class]
enabled = true
thresholds = { method_count = 30, loc = 600 }

[detectors.long-parameter-list]
thresholds = { max_params = 8 }

[detectors.sql-injection]
severity = "high"  # Downgrade from critical

[detectors.magic-numbers]
enabled = false  # Disable entirely
```

### Detector Names

Use kebab-case in config. These formats are equivalent:
- `god-class`
- `god_class`  
- `GodClassDetector`

### Threshold Options

Common threshold options by detector:

| Detector | Thresholds |
|----------|------------|
| `god-class` | `method_count` (default: 20), `loc` (default: 500), `complexity` (default: 100) |
| `long-parameter-list` | `max_params` (default: 6) |
| `long-methods` | `max_lines` (default: 50) |
| `deep-nesting` | `max_depth` (default: 4) |
| `message-chain` | `max_chain` (default: 4) |
| `large-files` | `max_lines` (default: 1000) |
| `complexity` | `threshold` (default: 10) |

### Inline Suppression

Suppress specific findings with comments:

```python
# repotoire: ignore
def legacy_function():
    pass
```

```javascript
// repotoire: ignore
const query = `SELECT * FROM ${table}`;  // Suppressed
```

Works with `#`, `//`, `/* */`, and `--` comment styles.

### CLI Flags

```bash
# Skip specific detectors
repotoire analyze . --skip-detectors god-class,magic-numbers

# Run only specific detectors  
repotoire analyze . --only-detectors sql-injection,secrets

# Include external tools (slower)
repotoire analyze . --thorough

# Show only high+ severity
repotoire analyze . --severity high
```

---

## Running Detectors

### Quick Start

```bash
# Run all default detectors (graph-based, fast)
repotoire analyze .

# Run with external tools (slower, more thorough)
repotoire analyze . --thorough
```

### Detector Engine Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     DetectorEngine                          │
│  - Runs independent detectors in parallel (rayon)          │
│  - Runs dependent detectors sequentially                    │
│  - Aggregates and deduplicates findings                    │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│ Graph-based      │ │ File-based   │ │ External Tool    │
│ (CircularDep,    │ │ (Secrets,    │ │ (Bandit, Ruff,   │
│  GodClass, etc.) │ │  SQL Inj.)   │ │  ESLint, etc.)   │
└──────────────────┘ └──────────────┘ └──────────────────┘
```

Graph-based detectors query the knowledge graph for structural patterns. File-based detectors scan source files directly. External tool detectors wrap CLI tools via subprocess.

### Performance

| Category | Speed | When to Use |
|----------|-------|-------------|
| Graph-based | Very fast (~100-400 files/sec) | Always |
| File-based | Fast (~50-100 files/sec) | Always |
| External tools | Slower (depends on tool) | `--thorough` flag |

---

## Writing Custom Detectors

Implement the `Detector` trait:

```rust
use repotoire_cli::detectors::{Detector, DetectorConfig};
use repotoire_cli::graph::GraphStore;
use repotoire_cli::models::Finding;
use anyhow::Result;

pub struct MyDetector {
    config: DetectorConfig,
}

impl Detector for MyDetector {
    fn name(&self) -> &str {
        "my-detector"
    }

    fn description(&self) -> &str {
        "Detects something important"
    }

    fn detect(&self, graph: &GraphStore) -> Result<Vec<Finding>> {
        let mut findings = Vec::new();
        
        // Query the graph
        // Analyze patterns
        // Create findings
        
        Ok(findings)
    }
}
```

Register in `DetectorEngineBuilder`:

```rust
let engine = DetectorEngineBuilder::new()
    .detector(Arc::new(MyDetector::new()))
    .build();
```
