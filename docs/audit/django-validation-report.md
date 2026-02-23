# Django Live Validation Report

**Date**: 2026-02-23
**Target**: Django web framework (django/django)

## Round 1 (Baseline)

**Overall Score**: 89.5 / 100 (B+)
**Total Findings**: 1,802

| Metric | Value |
|--------|-------|
| Structure Score | 97.7 |
| Quality Score | 68.4 |
| Architecture Score | 99.8 |
| Files: 3,001 | Functions: 2,381 | Classes: 7,373 | LOC: 538,683 |

### Findings by Severity

| Severity | Count |
|----------|-------|
| Critical | 64 |
| High | 226 |
| Medium | 716 |
| Low | 796 |
| **Total** | **1,802** |

### Key Detectors (Top 10 from sampled findings)

| Detector | Count | Category |
|----------|-------|----------|
| PrototypePollutionDetector | 6 | security |
| InsecureCookieDetector | 4 | security |
| JwtWeakDetector | 3 | security |
| Consensus[BroadException+RegexDos+Xxe+5more] | 1 | security |
| Consensus[RegexDos+Xss+CallbackHell+3more] | 1 | security |
| XxeDetector | 1 | security |
| InsecureRandomDetector | 1 | security |
| RegexDosDetector | 1 | security |
| DjangoSecurityDetector | 1 | security |
| CircularDependencyDetector | 1 | architecture |

### Codebase Metrics

| Metric | Value |
|--------|-------|
| Files | 3,001 |
| Functions | 2,381 |
| Classes | 7,373 |
| LOC | 538,683 |

### Analysis Notes

Django is a significantly larger codebase than Flask (538K LOC vs 18K LOC) or FastAPI (104K LOC). Notable characteristics:

- **High finding count (1,802)** is expected given the codebase size — 3.3 findings per 1,000 LOC, comparable to Flask (2.0/1K) and FastAPI (1.8/1K)
- **64 critical findings** — many are in vendored JavaScript libraries (jQuery, Select2, XRegExp) which ship with Django's admin interface. These include prototype pollution, ReDoS, and XXE vulnerabilities in the vendor code
- **Security-heavy finding profile** — the top 10 detectors are almost entirely security-focused, reflecting Django's role as a full-stack framework handling cookies, sessions, CSRF, authentication, and templating
- **Circular dependency web (87 files)** — Django's deeply interconnected architecture creates a large strongly connected component spanning apps, auth, GIS, DB, and utilities. This is a well-known architectural characteristic of the Django framework
- **JwtWeakDetector false positives** — the 3 JWT "algorithm none" findings are likely FPs on Django's signing module and auth tokens, which use HMAC-based signing (not JWT)
- **InsecureCookieDetector findings** — these target Django's session middleware and CSRF middleware cookie handling. Some are debatable since Django provides `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SECURE`, and `CSRF_COOKIE_HTTPONLY` settings that control these flags at the settings level rather than inline
- **Quality score (68.4)** is moderate, pulled down by the high volume of findings across the large codebase
- **Structure (97.7) and Architecture (99.8)** scores are strong despite the circular dependency finding
