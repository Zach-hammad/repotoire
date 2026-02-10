# Post-Launch GitHub Issues

Create these issues after pushing to GitHub.

---

## Issue 1: Reduce noise - Central coordinator findings too aggressive

**Labels:** enhancement, noise-reduction

### Problem
Running repotoire on itself produces 207 medium "Central coordinator" findings. These aren't actionable - parser/router/controller functions are *supposed* to be central.

### Current behavior
Every function with moderate in/out degree gets flagged as a potential smell.

### Expected behavior
- Smarter detection that recognizes orchestrator patterns
- Or higher default thresholds
- Or severity downgrade to "info" for borderline cases

### Impact
Users learn to ignore findings when there's too much noise. A tool that finds 200 "meh" issues is worse than one that finds 10 real issues.

### Suggested fix
- Pattern recognition for orchestrator code (parsers, routers, handlers)
- Configurable thresholds via .repotoire.toml
- "strict" vs "relaxed" mode

---

## Issue 2: Add inline suppression comments

**Labels:** enhancement, ux

### Problem
No way to suppress specific findings with inline comments.

### Expected behavior
```python
# repotoire: ignore
def complex_but_intentional_function():
    ...
```

Or:
```python
def func():  # repotoire: ignore[feature-envy]
    ...
```

### Use cases
- False positives that can't be fixed
- Intentional patterns (e.g., God class for legacy compatibility)
- Gradual adoption (ignore existing issues, catch new ones)

---

## Issue 3: LOC and complexity metrics sometimes show as 0

**Labels:** bug

### Problem
Some findings show `Lines of code: 0` and `Complexity: 1` even for substantial functions.

### Example
```
Feature Envy: parse_source
- Out-degree: 20
- Complexity: 1
- Lines of code: 0
```

### Expected
Accurate LOC and cyclomatic complexity from the graph.

### Root cause
Likely incomplete graph enrichment - need to verify Function nodes have `loc` and `complexity` properties populated.

---

## Issue 4: Configurable detector thresholds via .repotoire.toml

**Labels:** enhancement, configuration

### Problem
Default thresholds are hardcoded. Users can't tune without forking.

### Expected behavior
```toml
# .repotoire.toml
[detectors.feature-envy]
min_outdegree = 25

[detectors.architectural-bottleneck]
exclude_patterns = ["parsers/", "routes/", "handlers/"]

[detectors.god-class]
enabled = false
```

### Benefits
- Project-specific tuning
- Gradual adoption
- Reduces noise without code changes

---

## Issue 5: Add "strict" vs "relaxed" analysis modes

**Labels:** enhancement, ux

### Problem
Current defaults produce many findings. Good for thorough analysis, overwhelming for first-time users.

### Proposed modes

**Relaxed (default for new users):**
- Only critical/high severity
- Higher thresholds
- Focus on security + obvious smells

**Strict (opt-in):**
- All severities
- Lower thresholds
- Comprehensive analysis

### Usage
```bash
repotoire analyze .              # relaxed (default)
repotoire analyze . --strict     # everything
repotoire analyze . --security   # security only
```

---

## Issue 6: Smarter orchestrator pattern detection

**Labels:** enhancement, detection

### Problem
Detectors flag parsers, routers, controllers as "Feature Envy" or "God Class" when they're doing their job.

### Patterns to recognize
- `*_parser.*`, `*_router.*`, `*_handler.*`, `*_controller.*`
- Functions named `parse_*`, `route_*`, `handle_*`, `dispatch_*`
- Files in `parsers/`, `routes/`, `handlers/`, `controllers/`

### Suggested fix
- Auto-detect orchestrator patterns
- Lower severity or skip entirely for these
- Document the heuristics

---

## Issue 7: Walkdir still used in some detectors

**Labels:** bug, tech-debt

### Problem
Fixed in v0.2.9, but verify all file-scanning code uses `ignore` crate consistently.

### Files to audit
- All detectors in `src/detectors/`
- Any code using `walkdir::WalkDir`

### Expected
All file walking should use `walk_source_files()` utility or `ignore::WalkBuilder` directly.

---

Copy these to GitHub Issues after `git push`.
