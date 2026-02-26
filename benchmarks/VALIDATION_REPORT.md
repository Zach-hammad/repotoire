# Real-World Validation Report

**Date:** 2026-02-26
**Repotoire Version:** 0.3.113
**Projects:** Flask (Python), FastAPI (Python), Express (JavaScript)

---

## Summary

| Project | Language | LOC | Score | Grade | Findings | C | H | M | L |
|---------|----------|-----|-------|-------|----------|---|---|---|---|
| Flask | Python | 18,399 | 90.36 | A- | 26 | 4 | 8 | 8 | 6 |
| FastAPI | Python | 105,334 | 98.78 | A+ | 115 | 2 | 9 | 39 | 65 |
| Express | JavaScript | 21,346 | 88.52 | B+ | 96 | 1 | 8 | 32 | 55 |

**Score assessment:** All three scores fall in reasonable ranges for mature, well-maintained frameworks. FastAPI's near-perfect score reflects its clean architecture and high test coverage. Flask and Express score slightly lower due to legitimate complexity and older code patterns.

**Finding density:** Flask: 1.4/kLOC, FastAPI: 1.1/kLOC, Express: 4.5/kLOC.

---

## Fixes Applied (universally correct)

### 1. GlobalVariablesDetector: skip CommonJS `require()` imports

`var x = require('...')` is the standard CommonJS import pattern in any Node.js project. It is not mutable global state — it is a module import. The fix skips any module-scope `var`/`let` line containing `require(`.

**Eliminated ~5 FPs on Express** (the `require()` subset of the 43 global variable findings).

### 2. GeneratorMisuseDetector: skip single-yield in FastAPI/Starlette files

Single-yield generators are the idiomatic dependency injection pattern in FastAPI/Starlette. The detector already had a framework-aware check but required BOTH `try/finally` AND framework imports. Fixed to skip all single-yield generators when FastAPI/Starlette imports are present, since the DI pattern doesn't always use `try/finally`.

**Eliminated ~16 FPs on FastAPI.**

### 3. AIDuplicateBlockDetector: neutral "Structural duplicate" framing

"AI-style duplicate" and "AI-generated copy-paste" incorrectly attribute intent. The duplication is real, but the cause (AI vs. intentional design vs. copy-paste) cannot be determined by AST similarity alone. Changed to "Structural duplicate" and "structural duplication" — factually accurate for any codebase.

**Zero FPs eliminated, but removes misleading attribution.**

---

## Remaining False Positive Sources (need further work)

### GlobalVariablesDetector on module-scope `var app = express()` — ~40 findings on Express

Module-scope `var` in JavaScript that isn't a `require()` call (e.g., `var app = express()`, `var users = {}`) gets flagged. These are standard CommonJS patterns but harder to universally exclude — `var users = {}` at module scope IS technically mutable state. The detector's suggestion "Use const if immutable" is reasonable here, making these more style warnings than real issues. Consider downgrading severity.

### DebugCodeDetector on `print()` in tutorial/example files — ~17 findings on FastAPI

Tutorial files in `docs_src/` use `print()` for pedagogical purposes. We cannot hardcode `docs_src/` as a suppression path because that's FastAPI-specific. Potential solutions:
- Users can add `docs_src/` to their `.repotoireignore`
- Detect tutorial/example heuristics (many small files with simple functions, `if __name__` patterns)

### UnsafeTemplateDetector on framework API definitions — 3 FPs on Flask

Flags `render_template_string()` definition and `Environment` subclass in Flask's own source. The vulnerability is in user code calling these APIs, not the framework definition. Needs API-definition-vs-usage distinction.

### ExpressSecurityDetector on the Express framework itself — 13 findings

Flagging `lib/application.js` for missing `helmet` is like flagging `http.createServer`. Needs framework-library detection to distinguish "this IS the framework" from "this USES the framework."

### UnreachableCodeDetector on decorator-registered handlers — 5 FPs on FastAPI

Functions registered via `@app.get()` have zero direct callers. The decorator is the caller. Needs decorator-routing recognition.

---

## Legitimate Findings (True Positives)

### FastAPI
- **Architectural Bottleneck**: `jsonable_encoder` with complexity 35, called by 304 functions, betweenness centrality 1.0 — excellent finding
- **Large Files**: `routing.py` (4,685 lines), `applications.py` (4,692 lines)
- **Duplicate**: `make_not_authenticated_error` identical in two security modules

### Express
- **CommentedCodeDetector**: 10 findings in core `lib/` files — legitimate tech debt
- **InsecureCookieDetector**: Missing flags on cookie example
- **LargeFilesDetector**: `lib/response.js` (1,165 lines)

### Flask
- **InsecureCryptoDetector**: Potential SHA1 usage in sessions (borderline)

---

## Score Calibration Assessment

| Check | Expected | Actual | Verdict |
|-------|----------|--------|---------|
| Mature frameworks score 85+ | 85-100 | 88.5-98.8 | PASS |
| No perfect 100 scores | <100 | 88.5-98.8 | PASS |
| Python scores higher than older JS | Python > JS | 90.4/98.8 > 88.5 | PASS |
| Size-neutral scoring | Large != lower | FastAPI (105K) scores highest | PASS |

---

## Conclusion

Repotoire produces **reasonable scores** and identifies **genuinely valuable findings** (FastAPI's `jsonable_encoder` bottleneck is a standout). The three fixes applied in this session are universally correct and reduce FPs without compromising detection for other repos. The remaining FP sources require more nuanced solutions (framework-library detection, decorator-routing awareness, API-definition-vs-usage distinction) that are tracked for future work.
