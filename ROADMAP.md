# Repotoire Product Roadmap

*Last updated: 2026-02-06*

This roadmap synthesizes findings from UX review, technical debt audit, competitive strategy analysis, and security assessment.

---

## Vision

**"The AI-age code quality platform."**

While AI coding tools make developers faster, they also introduce 4x more code duplication and accelerate technical debt accumulation (GitClear 2025 Research). Repotoire is the watchdog that keeps AI-assisted codebases healthy.

**Positioning:** Free CLI for individual developers, paid cloud for teams who need visibility, collaboration, and enforcement.

---

## Q1 2026: Foundation

### 🚀 Immediate (This Week)

| Priority | Task | Status | Owner |
|----------|------|--------|-------|
| P0 | PyPI release with manylinux wheels | 🔄 Building | CI |
| P0 | Roll exposed Stripe + Fly tokens | ⏳ Pending | Zach |
| P0 | Webhook secret fail-closed | ⏳ Pending | - |

### 📦 Short-Term (Weeks 1-4)

#### GitHub PR Integration (Table Stakes)
**Why:** Every competitor has this. It's expected. Without it, we can't be part of the CI/CD workflow.

- [ ] GitHub App for PR status checks
- [ ] `repotoire ci` command for GitHub Actions
- [ ] Quality gate pass/fail based on grade threshold
- [ ] PR comment with findings summary
- [ ] Badge for README (health grade)

*Competitor context: Qlty, Codacy, SonarCloud all have this. Non-negotiable.*

#### CLI UX Overhaul
**Why:** User research shows confusion between `ingest` and `analyze`. First-run experience is unclear.

**Changes:**
| Current | New | Rationale |
|---------|-----|-----------|
| `ingest` is primary | `analyze` is primary | Users want to "analyze", not "ingest" |
| Manual workflow discovery | `repotoire init` guides setup | Reduce time-to-value |
| No status visibility | `repotoire status` shows state | Users need orientation |
| Errors lack context | Rich error messages with fixes | Reduce support burden |

**New Commands:**
```bash
repotoire init          # Guided first-run setup
repotoire status        # Show auth, last analysis, health summary
repotoire doctor        # Diagnose environment issues
```

**Command Consolidation:**
- `analyze` auto-ingests if no prior analysis exists
- `ingest` becomes alias for `analyze --parse-only`
- Add examples section to every `--help`

#### Quick Technical Debt Wins
**Why:** Low effort, high safety impact. Can ship alongside features.

- [ ] Add logging to 30+ silent `except: pass` blocks (30 min)
- [ ] Replace `print()` with `logger` in `graph/schema.py` (15 min)
- [ ] Standardize short flags (`-q` = quiet, `-o` = output, `-f` = format)
- [ ] Document shell completion in `--help`

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

### High Priority (Q1)

| Task | Location | Effort | Impact |
|------|----------|--------|--------|
| Split CLI monolith | `cli/__init__.py` (6,835 lines) | 4-6 hrs | Maintainability |
| Extract `analyze()` helpers | `cli/__init__.py:1442` | 2 hrs | Testability |
| Extract `preview_fix()` helpers | `api/v1/routes/fixes.py:1173` | 1.5 hrs | Testability |
| Split `_init_schema()` | `graph/kuzu_client.py` (240 lines) | 1 hr | Readability |

### Medium Priority (Q2)

| Task | Location | Effort | Impact |
|------|----------|--------|--------|
| Remove deprecated `FalkorDBNode2VecEmbedder` | `ml/node2vec_embeddings.py` | 30 min | Cleanup |
| Standardize typing (Python 3.9+) | Multiple files | 1-2 hrs | Consistency |
| Address notification TODOs | `api/v1/routes/admin/*.py` | 2-4 hrs | Features |
| Audit `# type: ignore` comments | Multiple files | 1 hr | Type safety |

### CLI Module Split Plan

```
cli/
├── __init__.py          # Main CLI group, lazy imports
├── commands/
│   ├── auth.py          # login, logout, whoami, token
│   ├── analysis.py      # analyze, ingest, sync
│   ├── graph.py         # schema, inspect, visualize, query
│   ├── ml.py            # embeddings, hotspots, similar
│   ├── fixes.py         # auto_fix, fix_finding, preview
│   ├── config.py        # config get/set/list
│   └── admin.py         # internal/debug commands
├── formatters/
│   ├── table.py
│   ├── json.py
│   └── html.py
├── utils/
│   ├── console.py       # Rich console helpers
│   ├── progress.py      # Progress bars
│   └── errors.py        # Error handling
└── lazy.py              # Lazy import machinery
```

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
