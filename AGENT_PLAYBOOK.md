# AGENT_PLAYBOOK.md

Operational playbook for coordinating Zach + Sloth + Zero on Repotoire.

## Mission
Build Repotoire into the best COI tool for codebase graph analysis by maximizing:
1. Detection accuracy
2. CLI/output reliability
3. User trust and adoption

## Team Roles

### Zach (Decision Owner)
- Sets weekly priorities
- Approves release/no-release
- Resolves tradeoffs (speed vs quality, scope vs polish)

### Zero (Implementation Lead)
- Architecture and coding execution
- Test implementation and refactors
- Performance and systems-level fixes

### Sloth (QA + Product Lead)
- Adversarial QA and release gating
- UX contract validation (CLI semantics, output correctness)
- Product strategy, messaging consistency, docs quality

---

## Weekly Operating Rhythm

### Monday: Theme Selection
Pick 1–2 focus themes only.
Examples:
- Detector precision hardening
- CLI contract reliability
- Cache/fresh parity
- Performance + memory

### Daily: Build + Verify
- Zero ships implementation incrementally
- Sloth runs independent verification on each claim
- Zach reviews decisions that unblock next steps

### Friday: Release Gate Review
Status by gate:
- ✅ Green (ship)
- 🟡 Yellow (ship candidate with accepted risk)
- ❌ Red (no ship)

---

## Workstream Split

## A) Accuracy Moat (COI quality)
**Zero:** detector logic improvements, threshold tuning
**Sloth:** false-positive hunting, regression fixture expansion

KPIs:
- Precision by top 10 detectors
- False positive rate trend
- Reopen rate on “fixed” detector bugs

## B) Reliability Moat (CLI trust)
**Zero:** option wiring, output contracts, cache parity
**Sloth:** CLI contract matrix + machine-parse checks

KPIs:
- Contract pass rate (target: 100%)
- Cache vs fresh parity rate (target: 100%)
- JSON/SARIF parse success in CI (target: 100%)

## C) UX + Adoption Moat
**Zero:** product improvements
**Sloth:** docs, benchmark narratives, onboarding friction reduction

KPIs:
- Time-to-first-value
- First-run success rate
- Support/confusion issue volume

---

## Required Update Format (Both Agents)
Every status update must include:
1. **What changed**
2. **Proof** (exact command + output snippet)
3. **Risk** (what could still fail)
4. **Decision needed from Zach**

No “done” claims without proof.

---

## Claim Verification Protocol
If an agent claims a fix:
1. Sloth re-runs reproducer independently
2. Run on release binary and (if needed) debug binary
3. Test fresh and cached paths
4. Record pass/fail with exact command

Only then mark resolved.

---

## Conflict Rule
If agent claims and QA results conflict:
- QA outcome wins until reproducer proves otherwise
- Reopen ticket immediately
- Require commit hash + repro output from implementer

---

## Priority Rule
Always prioritize in this order:
1. Correctness and trust
2. Contract reliability
3. Performance
4. Feature expansion

---

## Prompt Template for Coordinated Runs
Use this exact pattern:

> Zero: implement [X] with tests.
> Sloth: independently verify using adversarial QA and report only reproducible failures.
> Both: return proof commands + outputs and a go/no-go recommendation.

---

## Ship Principle
Repotoire wins long-term if users trust outputs.
No trust, no moat.
