# Repotoire as Architectural Intelligence for Code — Design Spec

**Date:** 2026-04-16
**Status:** Design approved; ready to decompose into an implementation plan.
**Author:** Brainstorming session between Zach Hammad and Claude (Opus 4.7).

---

## Section 1 — Identity and Positioning

**One-sentence identity:**

> *Repotoire is architectural intelligence for code — a queryable layer over your codebase's shape, called from bash or an agent's tool loop.*

**What using it feels like:** it answers architectural questions *on demand, in seconds* — the kind of questions that currently become Jira tickets, Slack threads, or three hours of grepping. *"What's the blast radius of this change? Which parts of this codebase are load-bearing? Where should this new module live?"* Those get answered in a tool call.

**Primary audience: agents, with humans as a first-class secondary.** Same data, two interfaces. Agents query the graph to ground their reasoning; humans consume the same computations rendered as a narrative and as direct CLI queries. The agent-first ordering matters because it determines:

- The flagship demo is Claude Code / Cursor invoking repotoire tools mid-edit, not a terminal screenshot.
- The README opens with an agent-task demo, not a score report.
- The MCP server has architectural parity with the CLI — never a "subset."
- Distribution is through the agent ecosystem (MCP registries, Claude Code plugins, Cursor tool directory), not through "compete with SonarQube" channels.

**The agent-first bet is load-bearing and acknowledged as such.** If agents don't bite, the positioning is upside-down. Research supports the bet: RepoGraph (+32.8% SWE-bench), RefactorBench (+43.9% from state representations), and the market vacuum is real — CodeScene paywalled, Structure101 locked in Sonar Enterprise, Qwiet acquired.

**What we are NOT:**

1. **Not a PR gate.** `fail-on` stays as an opt-in flag, not the headline. CI integration is a feature, not the identity.
2. **Not a SaaS dashboard** in v1. OSS single binary + MCP server. Hosted tier is a later, separate decision.
3. **Not a drop-in ESLint/Ruff replacement.** We don't try to win the per-line syntax layer.

**Security: table stakes, included, not the differentiator.** Repotoire ships 23 security detectors with SSA-based taint analysis — real work, not stubs. But CodeQL and Semgrep Pro are 5+ years ahead on deep cross-function taint; we won't catch them there. We say "SAST baseline included" and put the marketing energy behind architectural intelligence, which nobody else has in OSS.

**Category we're claiming:** *architectural intelligence for code* — the empty lane left by CodeScene (closed + ~€20/author/mo), Structure101 (Sonar Enterprise only), and Qwiet (acquired into Harness, no OSS successor). A new category name is a bet, but "observability" beat "monitoring" the same way: new word because new mental model.

---

## Section 2 — The Fact Layer

The architectural seam that makes dual-renderer (narrative CLI + MCP) coherent instead of two products glued together. The invariant: **both consumers read from the same data source.** They may materialize different slices — the narrative renders a full `ReportFacts`; MCP tools issue targeted queries against the same underlying primitives — but neither consumer re-derives graph facts. No drift possible.

### The eight fact categories

All first-class, all always computed:

| # | Fact | Derived from | Role |
|---|---|---|---|
| 1 | Architectural bottlenecks | Betweenness centrality | Hero — no OSS competitor |
| 2 | Temporal hotspots | Churn × complexity × bus factor | Hero — CodeScene-killer |
| 3 | Hidden couplings | Git co-change ≥ threshold | Hero — unique |
| 4 | Bus factor risks | Author concentration | Hero — engineering leader signal |
| 5 | Community misplacements | Louvain vs directory | Hero — "where does this belong?" |
| 6 | Cycles | SCC on imports | Always computed, surfaced via findings + MCP |
| 7 | PageRank drift | Weighted PR delta over time | Always computed, secondary surface |
| 8 | Detector findings | 110 existing detectors | Sibling lane; never narrative headline |

### The shared data structure

```rust
pub struct ReportFacts {
    pub score: HealthScore,
    pub bottlenecks: FactSet<Bottleneck>,
    pub hotspots: FactSet<Hotspot>,
    pub hidden_couplings: FactSet<HiddenCoupling>,
    pub bus_factor_risks: FactSet<BusFactorRisk>,
    pub community_misplacements: FactSet<CommunityMisplacement>,
    pub cycles: FactSet<Cycle>,
    pub pagerank_drifts: FactSet<PageRankDrift>,
    pub findings: FactSet<Finding>,
    pub metadata: ReportMetadata,
}

pub enum FactSet<T> {
    Computed(Vec<T>),
    InsufficientData { reason: &'static str },
    Disabled { reason: &'static str },
}
```

The `FactSet` wrapper is load-bearing: an empty `Computed(vec![])` means "nothing found" (healthy); `InsufficientData` means "can't say." Agents reading `bottlenecks` know whether absence is good news or missing signal.

Every fact carries **four things only**:

1. **Pointer-native citation** — `CodeLocation { file, line_start, line_end, symbol }` — renders as `[src/order/processor.rs:42-89]` (92% agent-citation accuracy per research, arXiv:2512.12117).
2. **Ego-graph neighborhood** — 1-hop refs to related facts by default (e.g., a `Bottleneck` carries `incoming_callers: Vec<CodeLocation>` and `outgoing_callees: Vec<CodeLocation>`); 2-hop depth available via query parameter on the MCP side but **not** materialized by default.
3. **Structured magnitudes** — raw numerical evidence (betweenness value, co-change frequency, z-score) as numbers, not buckets. Agents reason over numbers; humans get buckets via the narrator.
4. **Per-category severity** (Low/Medium/High/Critical) **calibrated within that category, not across categories**. Cross-category severity ranking is deferred to v1.5 — requires calibration data we don't have.

**Not in the fact struct:** pre-rendered prose. Narration is a consumer-side concern. Each renderer (terminal narrative, MCP response, HTML, SARIF, future Slack digest) owns its own `Narrator` that takes a fact and produces text. Changing tone doesn't change the fact struct.

### Pipeline placement

The 8-stage engine becomes 9:

```
collect → parse → graph → git_enrich → calibrate → detect → postprocess → score → synthesize
                                                                                    ^ new
```

`synthesize` reads frozen graph + git data + score + findings, produces `ReportFacts`. Does not render prose. Expected cost: low milliseconds on a warm graph. Benchmark-validated at v1 ship.

### Incremental behavior

The findings-level incremental cache already exists (`detectors/incremental_cache.rs`). `ReportFacts` inherits it: on a single-file change, the graph rebuilds from cached parse results, graph primitives recompute (cheap on unchanged structure), and synthesis produces a fresh `ReportFacts`. The cache cap (100k files, oldest-first eviction) bounds this.

Agents can also issue **fact-layer queries that bypass `ReportFacts` entirely** when they want targeted data — e.g., `blast_radius(fn)` goes straight to the graph's transitive-closure primitive, never materializing bottlenecks for the whole repo.

---

## Section 3 — Agent Interface: Code-Analysis MCP Profile v1

A named, documented MCP profile that packages opinionated defaults for code analysis without leaving wire-compatibility. Every MCP client on the market continues to work. Profile-aware clients get ergonomics upgrades. The profile is a first-class artifact (versioned, conformance-tested) — not a fork, not a loose convention.

### The seven tools

| # | Tool | Agent task | Cold / Warm |
|---|---|---|---|
| 1 | `architectural_context` | *"What's the structural role of this file/function?"* | 10s (scales ~5s/100k LOC) / <100ms |
| 2 | `blast_radius` | *"If I change this, what else is affected?"* | same cold / <300ms |
| 3 | `suggest_module` | *"Where should this new thing live, given these anchor symbols?"* | same cold / <500ms |
| 4 | `cycle_check` | *"Would this import introduce a cycle?"* | same cold / <50ms |
| 5 | `shape_diff` | *"What did my changes do to the architectural shape?"* | cold + diff-compute / <1s |
| 6 | `query_facts` | *"Show me the worst items in category X."* | same cold / <200ms |
| 7 | `explain` | *"Structured reasoning about this fact, that I can cite back."* | same cold / <50ms |

Full tool contracts: see brainstorming session archive. Each returns a response envelope with `schema`, `citations`, `data`, `availability`, `narrator_hint`, `error`.

### Profile-defining choices

**1. Tool packaging: Code Execution when supported, vanilla-schema fallback when not.**
For clients that implement Anthropic's Code Execution with MCP pattern (Claude Code, Claude Desktop): tools ship as TypeScript modules in a well-known directory. Agents discover the tool set by reading the directory and compose calls as code. **Measured impact on supporting clients: ~98% context reduction.** For clients that don't advertise code-execution support (Cursor, Windsurf, Zed, Codex): vanilla MCP `listTools` / `callTool` with full JSON Schema. Client advertises capabilities at `initialize` time; server picks the best path.

**2. Long operations: SEP-1686 Tasks with capability negotiation.**
Cold-start `architectural_context` and `shape_diff` emit Task handles to Task-aware clients with progress events, cancellation, and `retry_after_ms`. Task-unaware clients get a synchronous response — slower (the long operation blocks), but correct.

**3. Discovery: SEP-1649 Server Cards + SEP-1960 `.well-known/mcp` + profile version.**
Manifest includes `profile.id`, `profile.version` (SemVer), capability flags, per-tool SemVer. v1 commits to forward-compatibility for additive changes; breaking changes require v2.

**4. Structured error envelope.**
Every tool response carries a typed `error` field with `code`, `message`, `retryable`, `retry_after_ms`, `affected_path`. Vanilla MCP clients see `message` and carry on. Clients that parse `code` branch intelligently.

**5. Content types:** compact JSON-Graph (default), diff (opt-in, losslessly convertible to ACP), SARIF (opt-in via `format: "sarif"`).

**6. Transport:** stdio primary (single-user agent sessions); Streamable HTTP shipped for CI/hosted use cases. stdio's concurrency limits (0.64 req/s on Stacklok benchmark) are documented.

**7. Rate limiting + memoization:** keyed by `(tool_name, args_hash)`. Memoized results cached 10-min TTL. Warn log on 20× same query in 60s. `rate_limited` only on 50× same query in 10s.

**8. Conformance testing:** v1 ships with a minimal golden test suite (5–10 tests); v1.1 expands to 40–60; v1.2 adds cross-client automation.

**9. Plugin-author contract:** v2 WASM plugins must ship both a WASM binary and a TypeScript wrapper module for Code-Execution-mode clients; vanilla-schema clients get auto-generated JSON Schema from Rust types.

### What the profile explicitly does NOT fork

- Wire format: vanilla MCP JSON-RPC.
- Discovery manifest location: `.mcp.json` + `.well-known/mcp` per spec.
- Auth: transport-owned. Local stdio = process trust. Remote = OAuth 2.1.
- Tool-call request format: unchanged from MCP.
- Required client support: none. Profile degrades gracefully on every MCP client in existence.

### Fallback matrix

| Capability | Claude Code | Cursor / Windsurf | Zed / JetBrains | Arbitrary MCP |
|---|---|---|---|---|
| Code Execution | ✓ | fallback to schema | fallback to schema | fallback to schema |
| Tasks / progress | ✓ | fallback to sync | ✓ / ✓ | fallback to sync |
| Structured errors | ✓ | ✓ (reads `message`) | ✓ | ✓ (reads `message`) |
| Diff content type | ✓ | renders as text | ✓ (native ACP) | renders as text |
| SARIF on request | ✓ | ✓ (opt-in) | ✓ | ✓ |

---

## Section 4 — Human Interface (CLI)

Same data as the MCP, different renderer. Terminal UX is the marketing surface; must carry the architectural-intelligence identity without drowning the user in detector findings.

### Default output: one terminal screen, four sections

**Target: 80-column width, ~20 lines total.** One scrollless screen at default TTY size.

```
┌──────────────────────────────────────────────┐
│ repotoire-cli · B (82) · since last run: +1 −3 facts │
└──────────────────────────────────────────────┘

Shape
  OrderProcessor is on 47% of payment-flow call paths
  (src/order/processor.rs:42). Touched 340× by 2 people
  in 6 months — bus factor 1.

  auth.py and session.py co-change 89% of the time.
  They're one module pretending to be two.

  utils/ now reaches 73% of imports (was 51% in Q1).

Quick wins
  • Move load_token() from utils/ to auth/ (1 cycle dies)
  • Test OrderProcessor with 3 missing paths (see below)

Details
  3 bottlenecks · 5 hotspots · 2 hidden couplings · 1 cycle
  847 detector findings behind `repotoire findings --all`
  Full report: `repotoire analyze --format html`
```

**Delta semantics:** `+X new architectural facts, −Y resolved facts`. Scoped to fact layer from Section 2, not detector findings.

**"Last run" definition:** most recent successful analyze keyed by `(repo canonical path, config fingerprint, binary version)`. If any changed, delta line is suppressed; header prints `first run`.

### Command surface

| Command | v1 behavior |
|---|---|
| `analyze` (default) | New narrative output. `--all` shows full detector-findings table + every secondary fact category. Existing `--format html/json/sarif/markdown` unchanged. |
| `findings` | Unchanged. Full detector output, severity filtering, pagination. |
| `diff <ref>` | Unchanged. Findings-diff against a ref. |
| `show <fact> [target]` (new) | Verb-first drill-into-fact: `show bottleneck <file>`, `show blast-radius <fn>`, `show cycles`, `show bus-factor`, `show hotspots`, `show couplings`. Mirrors MCP tools — same query, different renderer. |
| `stats`, `watch`, `fix`, `init`, `doctor`, `benchmark`, `debt`, `calibrate`, `clean`, `feedback`, `train`, `graph`, `status`, `config`, `version` | Unchanged. |

### Unifying with existing ReportContext pipeline

The HTML reporter already uses `ReportContext { GraphData, GitData, FindingSnippet }`. `ReportFacts` is the **evolution** of that struct. Plumbing task: extend `ReportContext` into `ReportFacts` by adding the eight `FactSet<T>` fields alongside existing `GraphData`/`GitData`. All five reporters migrate. `report_with_context()` becomes `report_with_facts()`. One ingest point for all renderers.

### Flags — what the narrative doesn't need (and what it keeps)

- `--severity`, `--top`, `--page`, `--per-page`: still work on `findings` and `--all`; don't affect default narrative (fixed 3–5 shape items).
- `--skip-detector`, `--all-detectors`, `--min-confidence`, `--max-files`: unchanged.
- **`--fail-on` keeps working.** Affects exit code when flagged findings are present. Does not change what the narrative prints.
- `--format {text|json|html|sarif|markdown}`: unchanged. JSON output is the `ReportFacts` struct serialized — **identical shape to what an agent gets from the MCP `architectural_overview` tool.** Zero divergence.
- `--all`: safety valve. Narrative + full detector-findings table + every secondary fact category.
- `--quiet` / `-q`: **one line, `<grade> <score>`** (e.g. `B 82`). For scripts: `VAR=$(repotoire analyze --quiet)`.

### Watch mode

`repotoire watch` re-renders the **full narrative** on each cache event. Sub-100ms on cache hits (measured 0.055s warm-start). Same code path as manual invocation.

### Not in v1

- TUI dashboard.
- LSP server (Section 5).
- Configuration wizard overhaul.
- Inline autofix in terminal (`fix` command stays as-is).

---

## Section 5 — Infrastructure

The shared backbone that makes dual MCP + CLI surface work as one product, plus extensibility and distribution.

### Single-binary architecture

One binary (`repotoire`) hosts every surface: CLI, MCP server, LSP server, future daemon. All surfaces share:

- Same graph engine (CSR graph + hand-rolled interner + `ReportFacts` synthesis).
- Same incremental cache (content-hash keyed).
- Same `Narrator` layer (consumer-side, lazy-rendered).

Consequence: LSP hover, MCP tool call, CLI `show bottleneck` all run the same fact-layer query. **One source of truth; many renderers.**

### MCP server lifecycle

- **Process model:** `repotoire mcp serve` spawns stdio server. One process per client session.
- **Warm-state persistence within a session:** `ReportFacts` + `CodeGraph` + incremental cache live in-process for session lifetime.
- **Cold-start scaling:** ~5s per 100k parseable LOC. For repos >500k LOC, daemon mode (v1.5) becomes necessary.
- **Graceful degradation during cold analysis:** kick off initial `analyze` in a background thread (`std::thread::spawn` + `crossbeam-channel`); MCP tool calls return `analysis_in_progress` until done; agents retry naturally.

### LSP server — the #1 adoption lever

One server, three IDE ecosystems.

**v1 scope:**
- Diagnostics (detector findings as `textDocument/publishDiagnostics`).
- Hover: **additive** to language-specific LSPs (rust-analyzer, pyright, gopls). Hover contents ≤10 lines, complements don't compete.
- Code actions: **navigation-only in v1** (jump to related symbol / blast-radius target). No inline autofix.
- Watch-integration: file-save triggers incremental re-analysis + diagnostic re-publish. Target <200ms on 100k-LOC repos.

**Out of scope for v1:** completion, semantic tokens, workspace symbols, rename. rust-analyzer territory; don't compete.

Reports (full bottleneck/blast-radius analyses) live behind a command palette entry, not as a code-action side-effect.

### Plugin extensibility — v1 constraint, v2 delivery

v1 ships without plugins. v2 ships a plugin story:

**v2 `WasmDetector` implements `Detector` by delegating to a host-function `GraphQueryHandle` that marshals queries across the WASM boundary.** Keep v1's trait signature narrow enough that this indirection is invisible to v1 detector authors.

Two routes in v2:
- **WASM plugins** (primary): Wasmtime embed, users write detectors in any WASM-compilable language.
- **YAML rule DSL** (complement): for pattern-matching detectors that don't need graph primitives.

### SBOM and supply-chain posture

v1 ships:
- Static musl build via `cargo binstall`. Target <25MB binary.
- SBOM generation in CI (`cargo cyclonedx`); SBOM attached to every GitHub release as a signed artifact.
- **SLSA Level 2 provenance** via GitHub Actions OIDC + sigstore. L3 as roadmap if procurement demands.
- Minimal transitive deps (Round 1 removed petgraph, lasso, redb — 125 → 109 deps, no C build deps).
- No network calls at analysis time by default. Telemetry is opt-in.

### Telemetry

**No surface (MCP server, LSP server, watch mode) emits telemetry events unless the user explicitly opted in** via `config telemetry on`. Privacy-first by default.

### Binary distribution

Priority order:
1. `cargo install repotoire` — canonical Rust, source build.
2. `cargo binstall repotoire` — prebuilt binaries for x86_64/aarch64 × linux-gnu/linux-musl/macos/windows.
3. GitHub Action — `Zach-hammad/repotoire-action@v1` (exists).
4. **Thin published Docker image (Alpine-based)** — for CI systems that prefer containers. Doesn't dilute single-binary pitch; it's packaging.
5. Homebrew formula (v1.1).
6. apt/rpm (v1.2 or deferred).

---

## Section 6 — Phasing and Roadmap

Four phases, calendar-flexible, gated on adoption signals. Each ends with a visible, testable milestone.

**Baseline:** current `main` ships Round 1 + Round 2 + DashMap perf work — cold 5.0s, warm 0.055s on 93k LOC, 1848 tests passing, 18 commits landed. **Phase 0 is net-new work on top of this; not a rewrite.**

### Phase 0 — Ship the core (shipping range 6–12 weeks, calendar-flexible)

Observable outcome: `cargo binstall repotoire` and `repotoire mcp serve` work end-to-end; default CLI output is the four-section narrative.

**Explicit Phase 0 policy:** no new detectors added. Consolidation or removal only. Detector count frozen at 110 until Phase 3.

**In scope:**
- `ReportContext → ReportFacts` refactor; all 5 reporters migrate.
- Narrative CLI output per Section 4 as `analyze`'s default.
- `repotoire show <fact> [target]` subcommands (6 new verbs).
- `repotoire mcp serve` speaking Code-Analysis MCP Profile v1.
- `.well-known/mcp` discovery manifest.
- Profile conformance suite v1: 5–10 golden tests.
- `repotoire mcp install` auto-configures `.mcp.json` for Claude Code / Cursor / Windsurf.
- README rewrite opening with agent-task demo.

**Phase 0.5 (one-day fix):** optimize `rust-unwrap-without-context` detector (measured 2.3s of 2.4s detect stage). Target <500ms. Cold-start improvement: ~500ms.

**Success signal:** dogfoods on 93k-LOC codebase + verified runs on 3 diverse external repos (flask, ripgrep, postgres-rs). No crashes. Conformance suite green.

**Deferred:** LSP server, daemon mode, WASM plugins, Homebrew, apt repos.

### Phase 1 — LSP + editor adoption

Observable outcome: inline squiggles + architectural-context hover across VS Code, Zed, JetBrains via one LSP server.

**In scope:**
- `repotoire lsp` stdio server.
- Coexistence with language LSPs — additive.
- VS Code + Zed + JetBrains plugins published same week.
- Watch-mode hook for diagnostics.
- Navigation-only code actions.

**Success signal:** **first external bug report from someone who isn't you or a known collaborator.** Install counts are lottery tickets; an organic bug report is the real "it escaped the builder" signal.

### Phase 2 — Plugin extensibility + enterprise polish

Observable outcome: WASM plugin ABI stable, documented, dogfooded via internally-built example plugin.

**In scope:**
- WASM plugin ABI via Wasmtime embed.
- One internally-built example plugin (port an existing detector to WASM).
- YAML rule DSL for pattern-only detectors.
- TypeScript wrapper generator for Code-Execution-mode clients.
- Plugin registry format (GitHub index.json).
- SBOM + SLSA L2 on releases.
- Conformance suite v1.1 (40–60 tests).

**Success signal:** ABI v1.0 stable and documented; example plugin loads/runs against same test suite as built-in detectors; one external person builds a plugin against docs (even if trivial).

### Phase 3 — Pick one primary bet, ship toward 1.0

Phase 2 signals reveal direction. Phase 3 commits to one; others become Phase 4+ candidates.

**Primary-bet candidates (choose one based on Phase 2 signal):**
- **A. Cross-repo / monorepo graph analysis.**
- **B. Daemon mode / shared graph across concurrent agent sessions.**
- **C. Benchmark leaderboard ("Code Health Hall of Fame").**

**Always-in-Phase-3-regardless:**
- Propose "Named MCP Profiles" meta-SEP to MCP community.
- Conformance suite v1.2: cross-client compatibility automation.
- Documentation push: real docs site, not just README.

**1.0 release gate (Phase 3 exit):**
- MCP profile v1.0 stability committed.
- Plugin ABI v1.0 stability committed.
- Binary semver: `repotoire 1.0.0` with named supported platforms.
- SBOM / SLSA L2 on 1.0 release tag.

### Phase transition mechanism

Not calendar-gated, not purely signal-gated. **Every 8 weeks regardless of phase, write a one-page status note** (public GitHub discussion or blog post): what shipped, what didn't, which signal is being watched. Forces honest self-assessment and creates commitment footprint. If three consecutive status notes show a phase stalled, the spec re-opens.

### What never moves phases

- Wire-forking MCP. Non-negotiable.
- SaaS-first product direction. Free-forever-core is the trust covenant.
- Paywalling any currently-free feature. OpenGrep trauma; don't.
- Shipping detector #111 before consolidating existing 110.

---

## Section 7 — Distribution and Go-to-Market

### Core positioning-to-channel map

| Audience | Channel | What they consume |
|---|---|---|
| Coding agents (Claude Code, Cursor, Windsurf, Zed, Codex) | MCP registries (Anthropic, smithery.ai, pulsemcp.com), per-client `.mcp.json` | Code-Analysis MCP Profile v1 |
| IDE-using developers | VS Code Marketplace, Zed extensions, JetBrains Marketplace | `repotoire lsp` via editor plugin |
| CI teams | GitHub Action, `cargo binstall`, eventually apt/brew | `repotoire analyze --format sarif` in pipelines |
| CISOs / procurement | GitHub Security tab, SBOM, SLSA L2 | Single-binary install, zero runtime deps |
| Staff engineers | HN, blog posts, referral | Narrative CLI output, benchmarks on their repo |

Agent-ecosystem distribution is the central bet. Everything else is downstream of the agent story working.

### Launch sequence (tied to Section 6 phases)

**Phase 0 launch:**
1. Ship to MCP registries first. Submit to Anthropic MCP registry, smithery.ai, pulsemcp.com the day Phase 0 tests pass on three repos. No HN, no Twitter, no fanfare.
2. Write Claude Code plugin manifest + Cursor tool directory entry.
3. GitHub repo page = single source of truth. Pinned: "Install for Claude Code" / "Install for Cursor" / "Install for Zed." README top: agent-task demo GIF.
4. No blog post yet. Let the first 10–50 users arrive organically.

**Phase 1 launch:**
5. VS Code Marketplace + Zed + JetBrains publish same week. Three simultaneous releases → "we're everywhere" framing.
6. Blog post: *"The OSS tool that replaces what Sonar bought and CodeScene paywalls."* Lead with Structure101 acquisition + CodeScene pricing comparison.
7. HN Show post same week. Framing: *"Structure101 went closed, CodeScene is $20/author/mo, here's the OSS graph-native architectural analyzer you can run from your agent."*

**Phase 2 launch:**
8. Publish plugin ABI docs (one landing page + one worked example).
9. Engage MCP spec community: submit "Named MCP Profiles" meta-SEP. Attend MCP Dev Summit 2026 if it happens. Get repotoire cited in spec community.
10. Respond to any enterprise contact; feedback → Phase 3 bet selection. Don't commercialize.

**Phase 3 launch (depends on primary bet):**
- Cross-repo: partner with monorepo-heavy shops for pilots.
- Daemon: position as "the agent's codebase expert" for team-scale IDE use.
- Benchmark leaderboard: publish scoring of 56 famous OSS repos; rolling update.

### Trust-covenant public commitments

- **Public commitment in README and pinned GitHub discussion:** graph detectors, SARIF output, MCP profile, LSP server — **stay OSS forever**.
- **Monetization, if any, comes from above the line:** hosted SaaS dashboard, benchmark infrastructure, cross-repo hosted service. Clearly announced in advance; never retroactive.
- **Every core perf improvement lands in OSS binary first.** Hosted stuff ships from same commits.

Cultural commitment, not legal. Publicly stating it has teeth — if broken, fork happens in 48 hours.

### Mandatory second committer

Biggest risk: solo-author bus factor. Mitigation: recruit one committer within 6 months of Phase 1.

**Recruitment shape:** not a job listing. Find someone who files a genuinely good bug report or sends a thoughtful PR in Phase 0 or 1, offer commit access + design input. Pair programming, joint design docs. Goal: **one other human who would keep the project alive if primary author vanished for a quarter.**

### What explicitly does NOT happen

- No conference talks in Phase 0–1. Talks are shipping risk.
- No paid advertising, ever.
- No "VC launch." If funding becomes necessary: quiet, later, doesn't change public project.
- No weekly docs rewrites. Docs debt shows confidence.

### Signal-watch list

**Working signals:**
- Organic bug reports from people not in your contact list.
- MCP registry analytics showing repotoire in search results for code/architecture queries.
- HN comments referencing unprompted ("we used repotoire and found…").
- A second committer joins.

**Failing signals:**
- 90 days post-Phase-0 launch, zero organic bug reports.
- MCP registries have repotoire listed but zero cross-referencing posts.
- HN posts consistently sink below the fold.
- Solo author stops shipping commits for >6 weeks.

**If failing signals dominate after Phase 1, the positioning needs to change before more engineering time is spent.** The architectural-intelligence-for-agents bet may be wrong; reopen the spec at Section 1.

---

## Research backing

This design is informed by five rounds of web + ArXiv research conducted during the brainstorming session:

- **Competitive landscape:** CodeScene, Structure101 (Sonar-acquired), CodeQL, Semgrep, Qwiet (Harness-acquired), DeepSource, Codacy, Qodana pricing and moat analysis.
- **CPG / graph-native adoption:** Joern (academic-dominant), Apiiro (+104% ARR on architectural graph), commercial tradeoffs.
- **AI/LLM impact:** GitHub Copilot Autofix (460k auto-remediations 2025), Semgrep Assistant (96% researcher-agreement), IRIS (LLM + CodeQL hybrid).
- **Developer adoption patterns:** OpenGrep fork as market-retaliation signal, JetBrains 2025.2 LSP-API opening, DeepSource sub-5% FP rate.
- **Rust ecosystem positioning:** Ruff (~190M PyPI downloads/month), uv, Biome, Oxlint adoption trajectories; Rust-replaces-TS/Python-toolchain wave.
- **MCP ecosystem:** stdio performance benchmarks (Stacklok 0.64 req/s), Code Execution with MCP (98% context reduction on Claude-family), MCP Apps joint extension, Anthropic/LF/AAIF governance donation, 30+ MCP CVEs early 2026.
- **Protocol fork history:** LSP (vacuum-fill success), ACP (minimal adoption beyond Zed), gRPC-vs-REST (ecosystem gravity > tech), embrace-and-extend as dominant pattern.
- **ArXiv agent-tool research:** RepoGraph (+32.8% SWE-bench), RefactorBench (+43.9% from state representations), Natural Language Tools (+18.4pp vs JSON), CodeAct (+20pp), "Let Me Speak Freely" (-27.3pp from strict JSON), ProtocolBench (36.5% completion-time variance).

---

## Out of scope (this spec)

- Writing code. This spec becomes an implementation plan via the `writing-plans` skill next.
- Specific UI mockups for HTML/SVG reporters beyond pointing at the existing ReportContext pipeline.
- Specific wire-level JSON for every MCP tool — captured in Section 3 at an architectural level; full contracts will land in the implementation plan.
- Pricing or business-model decisions beyond "free-forever OSS core."
