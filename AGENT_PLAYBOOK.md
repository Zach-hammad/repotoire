# AGENT_PLAYBOOK.md

How Zach, Zero, and Sloth work together on Repotoire.

## Who Does What

**Zach** — Decides what matters. Approves releases. Breaks ties.

**Zero** — Builds things. Architecture, implementation, refactors, tests, QA, releases. Does whatever needs doing.

**Sloth** — Deep code review, adversarial QA, docs quality. Fresh eyes on what Zero ships.

Roles overlap. Whoever's best positioned picks it up. No territorial BS.

## How We Work

- Pick 1-2 focus areas at a time. Don't spray.
- Ship incrementally. Commit early, verify often.
- Every claim needs proof — command + output, not "I think it works."
- If Sloth's QA contradicts Zero's claim, QA wins until proven otherwise.

## Priority Order

1. **Correctness** — Wrong outputs destroy trust. Nothing else matters if findings are wrong.
2. **Reliability** — CLI flags, cache parity, clean output. Users shouldn't hit gotchas.
3. **Performance** — Fast enough to not annoy. Don't gold-plate.
4. **Features** — Only after 1-3 are solid.

## Update Format

When reporting status, include:
1. What changed (with commit hash)
2. Proof (command + output)
3. What could still break
4. Decisions needed from Zach

## Conflict Resolution

- Reproduce the issue independently before arguing about it
- QA evidence > implementation claims
- If it's a judgment call, Zach decides

## Ship Principle

Users trust Repotoire because outputs are accurate and reliable.
No trust, no product.
