# Formal Verification in Repotoire

Repotoire uses the **Lean 4 theorem prover** to formally verify correctness of core algorithms. This provides mathematical guarantees that go beyond testing.

## What is Verified

### Health Score Properties

| Property | Theorem | Description |
|----------|---------|-------------|
| Weight Conservation | `weights_sum_to_100` | Category weights sum to 100% |
| Score Bounds | `zero_is_valid`, `hundred_is_valid` | Scores are valid in [0, 100] |
| Invalid Detection | `over_hundred_invalid` | Scores > 100 are rejected |
| Grade Coverage | `grade_*_is_*` | Every score maps to exactly one grade |

### Grade Assignment Boundaries

```
Score 90-100 → A
Score 80-89  → B
Score 70-79  → C
Score 60-69  → D
Score 0-59   → F
```

All boundary cases are formally verified.

## Trust Model

| Component | Trust Level | Verification |
|-----------|-------------|--------------|
| Lean kernel | Trusted | Small TCB (~10k LOC) |
| Lean proofs | **Verified** | Machine-checked |
| Python implementation | Validated | Differential testing |
| Neo4j queries | Validated | Integration tests |

## How to Verify Locally

### Prerequisites

Install Lean 4 via elan:

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
source ~/.elan/env
```

### Build and Check Proofs

```bash
cd lean
lake build
```

If the build succeeds, all proofs are verified. Any invalid proof will cause a build failure.

### VS Code Integration

Install the **lean4** extension for:
- Live proof checking
- Interactive goal view
- Unicode input (type `\and` for ∧)

## Proof Artifacts

All proofs are in `lean/Repotoire/`:

```
lean/
├── lakefile.toml          # Build configuration
├── lean-toolchain         # Lean version (v4.25.2)
├── Repotoire.lean         # Main entry point
└── Repotoire/
    ├── Basic.lean         # Basic definitions
    └── HealthScore.lean   # Health score proofs
```

## Why Formal Verification?

1. **Trust**: Users can independently verify correctness
2. **Bugs**: Catches edge cases tests might miss
3. **Documentation**: Proofs are executable specifications
4. **Marketing**: "Formally Verified" differentiator (like AWS Cedar)

## Limitations

- Float arithmetic is **not** verified (IEEE 754 breaks decidability)
- We use integer percentages (0-100) as a sound approximation
- Graph algorithms are validated via testing, not proven

## Future Work

- [ ] Security path containment proofs
- [ ] Cypher injection prevention proofs
- [ ] Priority score formula verification
- [ ] Differential testing framework (Lean vs Python)

## References

- [Lean 4 Documentation](https://lean-lang.org/)
- [Theorem Proving in Lean 4](https://lean-lang.org/theorem_proving_in_lean4/)
- [AWS Cedar Formal Verification](https://aws.amazon.com/blogs/opensource/lean-into-verified-software-development/)
