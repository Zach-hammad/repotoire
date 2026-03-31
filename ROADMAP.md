# Repotoire Product Roadmap

*Last updated: 2026-03-21*

This roadmap synthesizes findings from UX review, technical debt audit, competitive strategy analysis, and security assessment.

---

## Vision

**"The AI-age code quality platform."**

While AI coding tools make developers faster, they also introduce 4x more code duplication and accelerate technical debt accumulation (GitClear 2025 Research). Repotoire is the watchdog that keeps AI-assisted codebases healthy.

**Positioning:** Free CLI for individual developers, paid cloud for teams who need visibility, collaboration, and enforcement.

---

## Recently Shipped (Q1 2026)

### ✅ Telemetry & Ecosystem Benchmarks (March 2026)
- PostHog event tracking (opt-in, respects DO_NOT_TRACK)
- Pre-computed benchmarks from 56 open-source repos on R2 CDN (benchmarks.repotoire.com)
- `repotoire benchmark` command — compare scores against ecosystem
- `repotoire config telemetry on/off/status`

### ✅ Detector Noise Reduction (March 2026)
- Analyzed 9,469 merged PRs from 98 repos to determine which detectors produce actionable findings
- Split 110 detectors into 77 default (security, bugs, perf, architecture) + 33 deep (`--all-detectors`)
- Rearchitected 9 noisy detectors (ShotgunSurgery→ChangeCoupling, LazyClass→RedundantClass, AIComplexitySpike→ComplexityOutlier, etc.)
- Average findings: 1,526 → ~87 per repo

### ✅ Scoring Recalibration (March 2026)
- Replaced density-based penalties (findings/kLOC) with flat severity weights (Critical=5, High=2, Medium=0.5, Low=0.1)
- Scores now differentiate: fd=96(A), repotoire=92(A-), flask=85(B)

### ✅ First Impression Experience (March 2026)
- Redesigned text output: themed "What stands out" + "Quick wins" + score delta + first-run tips
- Graph-powered HTML report: SVG architecture map, hotspot treemap, bus factor visualization, narrative story, finding cards with inline code snippets, README badge snippet
- `ReportContext` pipeline with `GraphData`, `GitData`, `FindingSnippet` structs
- `--relaxed` deprecated (replaced by `--severity high`)

---

### ✅ Detector Language Audit (March 2026)
- Audited 17 detector language-support gaps across all 9 languages
- Added `bypass_postprocessor()` trait method for security detectors to skip GBDT false-positive filter
- Fixed extension-loop scoping, content access patterns, cross-line context checks
- 9 per-language integration test suites (97 tests, all passing)
- Self-analysis dogfooding validation passing

### ✅ GitHub Action (March 2026)
- Composite GitHub Action published as `Zach-hammad/repotoire-action@v1` (separate repo)
- Inputs: `version`, `path`, `format`, `fail-on`, `diff-only`, `config`, `args`, `comment`
- Outputs: `score`, `grade`, `findings-count`, `critical-count`, `high-count`, `sarif-file`, `json-file`, `exit-code`
- PR diff mode: auto-detects `pull_request` events, runs `repotoire diff` against base SHA
- PR commenting: posts summary table + top 5 findings, updates in place
- SARIF upload compatible with `github/codeql-action/upload-sarif@v3`
- JSON sidecar support (`--json-sidecar`) to avoid running analysis twice
- 5 CI test jobs, release-please automation, floating `v1` tag

---

## Q1 2026: Foundation (Remaining)

### 📦 GitHub PR Integration (Remaining)

- [ ] GitHub App for PR status checks (check run annotations — requires GitHub App, not just Actions)
- [x] GitHub Action for CI (`Zach-hammad/repotoire-action@v1`)
- [x] Quality gate pass/fail based on grade threshold (`fail-on` input)
- [x] PR comment with findings summary (`comment` input)
- [x] Badge for README (health grade) — shipped as part of HTML report

---

## Q2 2026: Differentiation

### 🎯 AI Code Quality Detectors (Major Differentiator)

**Why:** GitClear's 2025 research shows AI coding assistants cause:
- 4x increase in code duplication
- Higher churn rates (code written then quickly modified/deleted)
- More "copy-paste" patterns

**No one owns this narrative.** First mover advantage is massive.

**Detectors to Build:**
| Detector | What It Catches | Difficulty |
|----------|-----------------|------------|
| `ai-duplicate-block` | Near-identical code blocks (AI tends to copy-paste solutions) | Medium |
| `ai-churn-pattern` | Code with high modification frequency post-creation | Medium |
| `ai-boilerplate-explosion` | Excessive boilerplate that could be abstracted | Easy |
| `ai-inconsistent-style` | Style inconsistencies within same file/module | Medium |
| `ai-missing-tests` | New code added without corresponding tests | Easy |
| `ai-complexity-spike` | Sudden complexity increases in previously simple functions | Medium |

**Marketing Angle:**
> "Copilot makes you faster. Repotoire makes sure you don't accumulate technical debt 4x faster too."

### 🆓 Free Cloud Tier

**Why:** Removes signup friction. Let users experience cloud value before paying.

**Limits:**
- 1 repository
- 1 user
- 7-day history
- No team features
- Community support only

**Upgrade triggers:**
- Add second repo
- Invite team member
- Access 30+ day history
- Need priority support

### 🧠 Knowledge Risk Intelligence (Bus Factor)

**Why:** Rare feature. Only TechMiners competes here. Valuable for:
- M&A due diligence ("what's the risk if key devs leave?")
- Team restructuring ("who knows what?")
- Succession planning

**Features:**
- [ ] Bus factor score per module/file
- [ ] Knowledge concentration heatmap
- [ ] "At risk" modules (single owner, high complexity)
- [ ] Knowledge transfer recommendations
- [ ] Ownership trends over time

---

## Q3 2026: Scale

### 🏢 Enterprise Features

| Feature | Description | Priority |
|---------|-------------|----------|
| SSO/SAML | Enterprise identity providers | High |
| Audit Logs | Compliance-ready activity logging | High |
| Custom Policies | Organization-specific rules | Medium |
| Private Runners | Self-hosted analysis for air-gapped envs | Medium |
| SLA | Guaranteed uptime, priority support | High |

### 🔌 IDE Integration

**VS Code Extension:**
- Real-time findings as you type
- Quick fixes from editor
- Health score in status bar
- "Explain this finding" with AI

**JetBrains Plugin:** (stretch goal)
- Same features as VS Code
- IntelliJ, PyCharm, WebStorm

### 📊 Advanced Analytics

- Technical debt velocity (is it growing or shrinking?)
- Developer productivity metrics (with privacy controls)
- Codebase health trends
- Benchmark against similar repos (anonymized)

---

## Technical Debt Paydown Plan

*Note: The Python-era monolith problems (39k LOC cli/__init__.py, etc.) were resolved during the Rust rewrite. The current codebase is well-modularized — largest file is `graph/primitives.rs` at 2,726 LOC.*

### Current Priorities

| Task | Location | Effort | Impact |
|------|----------|--------|--------|
| Extract graph primitives phases | `graph/primitives.rs` (2,726 LOC) | 2 hrs | Readability — split Phase A / Phase B into separate files |
| Extract graph building from CLI | `cli/analyze/graph.rs` (1,817 LOC) | 1-2 hrs | Testability — graph construction logic mixed with CLI concerns |
| Simplify engine orchestration | `engine/mod.rs` (1,738 LOC) | 1-2 hrs | Readability — extract `build_report_context` helpers |

---

## Security Roadmap

### Completed ✅
- Cypher injection prevention (Nov 2025 audit fixed)
- Clerk JWT authentication with signature verification
- Fernet encryption for GitHub tokens at rest
- Rate limiting with Redis backend
- Security headers (CSP, HSTS, X-Frame-Options)
- Multi-tenant isolation with defense-in-depth
- OAuth state tokens with one-time use

### Q1 2026
- [ ] **Webhook secret fail-closed** — Currently logs warning, should reject
- [ ] **Audit remaining f-string queries** — Ensure all use validated identifiers
- [ ] **Add `uv-secure` to CI pipeline** — Dependency vulnerability scanning

### Q2 2026
- [ ] **SOC 2 Type 1 preparation** — Document controls, policies
- [ ] **Penetration test** — Third-party security assessment
- [ ] **Bug bounty program** — Responsible disclosure policy

---

## Competitive Landscape

### Direct Competitors

| Competitor | Model | Pricing | Threat Level |
|------------|-------|---------|--------------|
| **Qlty** (Code Climate) | Free CLI + paid cloud | $20-30/dev/mo | 🔴 High |
| **Semgrep** | Free OSS + paid cloud | $13-20/dev/mo | 🟡 Medium |
| **Codacy** | Free tier + paid | $19/dev/mo | 🟡 Medium |
| **SonarQube** | Self-hosted + cloud | €30/mo - $35K/yr | 🟡 Medium |

### Our Advantages
1. **Graph-based analysis** — Deeper insights than AST-only tools
2. **AI code quality focus** — Unique positioning no one owns
3. **Bus factor analysis** — Rare, valuable for leadership
4. **BYOK model** — Users bring own AI keys, better margins
5. **Local-first** — Kuzu embedded, no Docker required

### Their Advantages (We Need to Match)
1. GitHub PR integration ← **Q1 priority**
2. VS Code extension ← **Q2 priority**
3. Free cloud tier ← **Q2 priority**
4. Established brand/trust ← **Marketing focus**

---

## Pricing Strategy

### Current

| Tier | Price | Target |
|------|-------|--------|
| **CLI** | Free forever | Individual devs |
| **Team** | $19/dev/mo ($15 annual) | Small-medium teams |
| **Enterprise** | Custom | Large orgs, compliance needs |

### Competitive Context
- Qlty: Free CLI, $20-30/dev cloud
- Semgrep: ~$13-20/dev
- Codacy: ~$19/dev
- LinearB: $50+/seat

**We're priced right.** The 20% annual discount ($15/dev) is competitive with SonarCloud ($14) and CodeClimate ($15-20).

### Future Consideration
- Usage-based pricing for AI features (tokens consumed)
- Repo-count tiers instead of per-seat
- Startup program (free for <10 employees)

---

## Success Metrics

### Q1 2026
- [ ] PyPI downloads: 1,000+
- [ ] GitHub stars: 500+
- [ ] CLI MAU: 100+
- [ ] First 5 paying teams

### Q2 2026
- [ ] PyPI downloads: 5,000+
- [ ] GitHub stars: 1,500+
- [ ] CLI MAU: 500+
- [ ] 20 paying teams
- [ ] First enterprise deal

### Q3 2026
- [ ] PyPI downloads: 15,000+
- [ ] GitHub stars: 3,000+
- [ ] ARR: $50K+
- [ ] First SOC 2 audit

---

## Appendix: Source Reports

- `SECURITY_AUDIT_2026-02-06.md` — Full security assessment
- `repotoire-strategy-report.md` — Competitive analysis and positioning
- UX Review — CLI usability findings (agent session)
- Tech Debt Audit — Code quality findings (agent session)

---

*This is a living document. Update as priorities shift.*
