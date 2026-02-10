# Repotoire Rust Migration

## Goal
Rewrite the Python CLI as a pure Rust binary to eliminate Kuzu Python binding segfaults.

## Progress

### Core Infrastructure
- [x] Project structure (Cargo.toml, src/)
- [x] Models (Finding, Severity, HealthReport)
- [x] CLI skeleton (clap)
- [ ] Graph client (Kuzu Rust bindings)
- [ ] Parser framework (tree-sitter)

### Detectors (46 total)
- [ ] CircularDependencyDetector
- [ ] GodClassDetector  
- [ ] ModuleCohesionDetector
- [ ] CoreUtilityDetector
- [ ] InfluentialCodeDetector
- [ ] DegreeCentralityDetector
- [ ] ShotgunSurgeryDetector
- [ ] MiddleManDetector
- [ ] InappropriateIntimacyDetector
- [ ] DataClumpsDetector
- [ ] AsyncAntipatternDetector
- [ ] TypeHintCoverageDetector
- [ ] LongParameterListDetector
- [ ] MessageChainDetector
- [ ] TestSmellDetector
- [ ] GeneratorMisuseDetector
- [ ] LazyClassDetector
- [ ] RefusedBequestDetector
- [ ] ArgumentMismatchDetector
- [ ] PackageStabilityDetector
- [ ] TechnicalDebtHotspotDetector
- [ ] LayeredArchitectureDetector
- [ ] ... (24 more)

### Parsers
- [ ] Python (tree-sitter-python)
- [ ] TypeScript/JavaScript (tree-sitter-typescript)
- [ ] Rust (tree-sitter-rust)
- [ ] Go (tree-sitter-go)
- [ ] Java (tree-sitter-java)
- [ ] C/C++ (tree-sitter-c, tree-sitter-cpp)
- [ ] C# (tree-sitter-c-sharp)
- [ ] Kotlin (tree-sitter-kotlin)

### Reporters
- [ ] Text (terminal output)
- [ ] JSON
- [ ] SARIF (GitHub Code Scanning)
- [ ] HTML

### AI Features (PRO)
- [ ] OpenAI integration
- [ ] Anthropic integration
- [ ] Fix generation

## Architecture

```
repotoire-cli/
├── src/
│   ├── main.rs           # Entry point
│   ├── cli/              # Command handlers
│   │   ├── mod.rs
│   │   ├── analyze.rs
│   │   ├── findings.rs
│   │   ├── fix.rs
│   │   └── doctor.rs
│   ├── graph/            # Kuzu database
│   │   ├── mod.rs
│   │   ├── client.rs
│   │   ├── schema.rs
│   │   └── queries.rs
│   ├── detectors/        # Code smell detectors
│   │   ├── mod.rs
│   │   ├── base.rs       # Detector trait
│   │   ├── engine.rs     # Parallel executor
│   │   └── *.rs          # Individual detectors
│   ├── parsers/          # Tree-sitter parsers
│   │   ├── mod.rs
│   │   └── *.rs          # Per-language parsers
│   ├── pipeline/         # Ingestion pipeline
│   │   └── mod.rs
│   ├── reporters/        # Output formatters
│   │   ├── mod.rs
│   │   ├── text.rs
│   │   ├── json.rs
│   │   └── sarif.rs
│   └── models.rs         # Core types
└── Cargo.toml
```

## Porting Strategy

1. **Graph algorithms** - Already in Rust (repotoire-fast), just move over
2. **Parsers** - Tree-sitter is native Rust, minimal work
3. **Detectors** - Port one by one, test against Python output
4. **CLI** - clap is great, straightforward port

## Testing

Run both versions on same codebase, compare output:
```bash
# Python version
python -m repotoire analyze . --json > py_output.json

# Rust version  
./target/release/repotoire analyze . --json > rs_output.json

# Compare
diff py_output.json rs_output.json
```
