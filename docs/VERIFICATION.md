# Formal Verification in Repotoire

Repotoire uses the **Lean 4 theorem prover** to formally verify correctness of critical algorithms. This document explains what is verified, how to run proofs, and the trust model.

## What is Verified

### Security Properties

| Property | Lean File | Description |
|----------|-----------|-------------|
| **Cypher Injection Prevention** | `CypherSafety.lean` | Proves `validate_identifier()` blocks all injection payloads |
| **Path Containment** | `PathSafety.lean` | Proves files outside repository boundary cannot be accessed |

### Scoring Properties

| Property | Lean File | Description |
|----------|-----------|-------------|
| **Health Score Bounds** | `HealthScore.lean` | Proves scores always in [0, 100] |
| **Weight Conservation** | `HealthScore.lean` | Proves category weights sum to 1.0 |
| **Grade Coverage** | `HealthScore.lean` | Proves every score maps to exactly one grade |
| **Grade Monotonicity** | `HealthScore.lean` | Proves higher scores never produce worse grades |

### Priority Score Properties

| Property | Lean File | Description |
|----------|-----------|-------------|
| **Priority Score Bounds** | `PriorityScore.lean` | Proves priority scores in [0, 100] |
| **Severity Monotonicity** | `PriorityScore.lean` | Proves higher severity produces higher priority |
| **Agreement Normalization** | `PriorityScore.lean` | Proves agreement normalized to [0, 1] |

### Threshold Properties

| Property | Lean File | Description |
|----------|-----------|-------------|
| **Threshold Ordering** | `Thresholds.lean` | Proves thresholds are properly ordered |
| **Threshold Monotonicity** | `Thresholds.lean` | Proves worse metrics never decrease severity |

### Risk Amplification Properties

| Property | Lean File | Description |
|----------|-----------|-------------|
| **Escalation Rules** | `RiskAmplification.lean` | Proves 2+ factors always escalate to CRITICAL |
| **Escalation Monotonicity** | `RiskAmplification.lean` | Proves escalation never decreases severity |

## Trust Model

| Component | Trust Level | Verification Method |
|-----------|-------------|---------------------|
| **Lean 4 kernel** | Trusted | Small TCB (~10k LOC), independently verified |
| **Lean proofs** | Machine-checked | Verified by Lean type checker |
| **Python implementation** | Validated | Differential testing against Lean specs |
| **Neo4j queries** | Validated | Integration tests |

### What We Trust

1. **Lean 4 kernel**: The type checker that verifies proofs (~10k lines of C++)
2. **Operating system**: File I/O, memory management
3. **Hardware**: CPU, memory

### What We Verify

1. **Algorithm correctness**: Mathematical properties of scoring, thresholds, escalation
2. **Security invariants**: Injection prevention, path containment
3. **Implementation match**: Python matches Lean specification (via differential testing)

## How to Verify Locally

### Prerequisites

```bash
# Install Lean 4 via elan
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh

# Verify installation
~/.elan/bin/lake --version
```

### Build and Check Proofs

```bash
cd lean

# Build all proofs (this type-checks everything)
~/.elan/bin/lake build

# Clean and rebuild
~/.elan/bin/lake clean && ~/.elan/bin/lake build
```

If the build succeeds, all proofs are verified. If any proof is invalid, the build fails.

### Run Differential Tests

```bash
# Install test dependencies
pip install hypothesis pytest-xdist

# Run all differential tests (1000 examples per property)
pytest tests/differential/ -v

# Run with more examples (thorough mode)
pytest tests/differential/ -v --hypothesis-profile=thorough

# Run in parallel
pytest tests/differential/ -n auto

# Run with specific seed (reproducible)
pytest tests/differential/ -v --hypothesis-seed=42
```

## Proof Artifacts

All proofs are in `lean/Repotoire/`:

```
lean/
├── lakefile.lean              # Build configuration
├── lean-toolchain             # Lean version (v4.26.0-rc2)
├── Repotoire.lean             # Main entry point
└── Repotoire/
    ├── Basic.lean             # Common definitions
    ├── HealthScore.lean       # Health scoring proofs
    ├── CypherSafety.lean      # Injection prevention proofs
    ├── Thresholds.lean        # Threshold monotonicity proofs
    ├── PriorityScore.lean     # Priority score proofs
    ├── PathSafety.lean        # Path containment proofs
    └── RiskAmplification.lean # Risk escalation proofs
```

## Differential Testing

The differential testing framework validates Python matches Lean using **property-based testing** with Hypothesis.

### Architecture

```
┌────────────────────┐     ┌────────────────────┐
│  Lean Spec         │     │  Python Impl       │
│  (verified)        │     │  (production)      │
└─────────┬──────────┘     └─────────┬──────────┘
          │                          │
          ▼                          ▼
┌─────────────────────────────────────────────────┐
│           Differential Test Runner              │
│  - Generate 1000+ random inputs per property    │
│  - Run Lean-equivalent Python functions         │
│  - Verify outputs match Lean specifications     │
│  - Report any discrepancies                     │
└─────────────────────────────────────────────────┘
```

### Test Coverage

| Module | Lean Theorems | Differential Tests | Examples/Test |
|--------|---------------|-------------------|---------------|
| `HealthScore` | 15 | 11 | 1000 |
| `PriorityScore` | 14 | 17 | 1000 |
| `CypherSafety` | 12 | 9 | 1000 |
| `PathSafety` | 16 | 17 | 1000 |
| `RiskAmplification` | 15 | 18 | 1000 |
| `Thresholds` | 12 | 9 | 1000 |

### Hypothesis Profiles

| Profile | Examples | Use Case |
|---------|----------|----------|
| `dev` | 100 | Quick local testing |
| `ci` | 1000 | CI pipeline (default) |
| `thorough` | 10000 | Pre-release validation |
| `debug` | 10 | Debugging failures |

```bash
# Use specific profile
pytest tests/differential/ --hypothesis-profile=thorough
```

## Key Theorems

### Score Bounds (HealthScore.lean)

```lean
-- Final score is always valid (in [0, 100])
theorem final_score_bounded
    (s1 s2 s3 : Nat)
    (h1 : is_valid_score s1)
    (h2 : is_valid_score s2)
    (h3 : is_valid_score s3) :
    is_valid_score (final_score s1 s2 s3)
```

### Injection Prevention (CypherSafety.lean)

```lean
-- Injection characters are blocked
theorem no_injection_chars (s : String) (h : is_safe_identifier s) :
    ∀ c ∈ s.toList, ¬is_injection_char c
```

### Path Containment (PathSafety.lean)

```lean
-- If containment check passes, file is genuinely within repo
theorem containment_implies_prefix
    (file repo : PathComponents)
    (h : is_within_repo file repo = true) :
    is_prefix repo file = true
```

### Escalation Rules (RiskAmplification.lean)

```lean
-- 2+ additional factors always produce CRITICAL
theorem compound_risk_is_critical (original : Severity) (additional_count : Nat)
    (h : additional_count ≥ 2) :
    calculate_escalated_severity original additional_count = Severity.CRITICAL
```

## CI Integration

Proofs are verified on every push via GitHub Actions:

```yaml
# .github/workflows/lean.yml
name: Lean Proofs
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: leanprover/lean-action@v1
      - run: cd lean && lake build
```

Differential tests run as part of the standard test suite:

```yaml
# .github/workflows/test.yml
- name: Differential Tests
  run: pytest tests/differential/ -v --hypothesis-seed=${{ github.run_id }}
```

## Limitations

### What is NOT Verified

1. **Neo4j queries**: Cypher query correctness is tested, not proven
2. **External tool output**: Ruff, Pylint, Bandit output is trusted
3. **Parser correctness**: Python AST parsing is trusted
4. **I/O operations**: File reading, network calls are tested
5. **Concurrency**: Thread safety is tested, not proven

### Floating Point

Lean proofs use **natural numbers (Nat)** scaled by 100 to avoid floating-point complexity:
- Python: `0.4 + 0.3 + 0.3 = 1.0`
- Lean: `40 + 30 + 30 = 100`

Differential tests verify Python floating-point matches Lean integer arithmetic.

## Further Reading

- [Lean 4 Documentation](https://lean-lang.org/documentation/)
- [Lean 4 Theorem Proving](https://lean-lang.org/theorem_proving_in_lean4/)
- [Hypothesis Documentation](https://hypothesis.readthedocs.io/)
- [AWS Cedar Formal Verification](https://www.amazon.science/blog/how-we-built-cedar-with-automated-reasoning-and-differential-testing) (inspiration)
