import { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Repotoire vs ESLint: Linter vs Graph-Powered Code Intelligence",
  description:
    "Compare Repotoire and ESLint — traditional linting vs graph-powered architectural analysis. What each tool catches and when to use both.",
  alternates: {
    canonical: "/compare/repotoire-vs-eslint",
  },
  openGraph: {
    title: "Repotoire vs ESLint: Linter vs Graph-Powered Code Intelligence",
    description:
      "Compare Repotoire and ESLint — traditional linting vs graph-powered architectural analysis. What each tool catches and when to use both.",
    url: "https://www.repotoire.com/compare/repotoire-vs-eslint",
    type: "website",
    siteName: "Repotoire",
  },
}

const comparisonData = [
  { feature: "Analysis approach", repotoire: "Graph-powered cross-file analysis", eslint: "Per-file rule-based linting" },
  { feature: "Languages", repotoire: "9 (Python, TS/JS, Rust, Go, Java, C#, C, C++)", eslint: "JavaScript / TypeScript (via parser)" },
  { feature: "Rules / Detectors", repotoire: "106 pure Rust detectors", eslint: "250+ built-in rules, thousands via plugins" },
  { feature: "Cross-file analysis", repotoire: "Yes (graph algorithms across entire codebase)", eslint: "No (per-file only)" },
  { feature: "Architectural analysis", repotoire: "Yes (circular deps, god classes, bottlenecks, coupling)", eslint: "No" },
  { feature: "Code style / formatting", repotoire: "No (not a formatter)", eslint: "Yes (with stylistic plugins)" },
  { feature: "Auto-fix", repotoire: "AI-powered fix suggestions", eslint: "Deterministic auto-fix (--fix)" },
  { feature: "Plugin ecosystem", repotoire: "Built-in detectors", eslint: "Massive (React, Vue, accessibility, import rules, etc.)" },
  { feature: "Configuration", repotoire: "repotoire.toml / .repotoirerc.json", eslint: "eslint.config.js (flat config)" },
  { feature: "Setup", repotoire: "Single binary, zero config", eslint: "npm install, config file required" },
  { feature: "Performance", repotoire: "~1.4s incremental, parallel Rust", eslint: "Fast per-file, slower on large codebases" },
  { feature: "CI/CD integration", repotoire: "GitHub Action, SARIF output", eslint: "Universal (any CI, formatters, SARIF via plugin)" },
  { feature: "Pricing", repotoire: "Free CLI, Pro plans coming", eslint: "Free and open source" },
]

export default function RepotoireVsESLintPage() {
  return (
    <article className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <header className="text-center mb-16">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-6 font-display font-bold">
            Repotoire vs ESLint
          </h1>
          <p className="text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
            ESLint is the industry-standard linter for JavaScript and TypeScript.
            Repotoire is a graph-powered analysis tool that finds cross-file architectural issues.
            They solve different problems &mdash; and work best together.
          </p>
        </header>

        {/* Comparison Table */}
        <section className="mb-16">
          <h2 className="text-2xl font-display font-bold text-foreground mb-6">
            Feature Comparison
          </h2>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left p-4 font-display font-semibold text-foreground">Feature</th>
                  <th className="text-left p-4 font-display font-semibold text-primary">Repotoire</th>
                  <th className="text-left p-4 font-display font-semibold text-muted-foreground">ESLint</th>
                </tr>
              </thead>
              <tbody>
                {comparisonData.map((row, i) => (
                  <tr key={row.feature} className={i % 2 === 0 ? "bg-muted/10" : ""}>
                    <td className="p-4 font-medium text-foreground">{row.feature}</td>
                    <td className="p-4 text-muted-foreground">{row.repotoire}</td>
                    <td className="p-4 text-muted-foreground">{row.eslint}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Different Tools, Different Jobs */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Different Tools, Different Jobs
          </h2>
          <div className="card-elevated rounded-xl p-6 space-y-4">
            <p className="text-muted-foreground leading-relaxed">
              <strong className="text-foreground">ESLint operates per-file.</strong> It parses each JavaScript
              or TypeScript file individually, applies rules to the AST, and reports violations. It&apos;s
              excellent at catching code style issues, potential bugs, unused variables, and enforcing
              team conventions. With its plugin ecosystem, you can add React hooks rules, accessibility
              checks, import ordering, and thousands of other checks.
            </p>
            <p className="text-muted-foreground leading-relaxed">
              <strong className="text-foreground">Repotoire operates across files.</strong> It builds a
              knowledge graph of your entire codebase &mdash; every function, class, module, and their
              relationships &mdash; then runs graph algorithms to find structural problems. Circular
              dependencies between modules, god classes with too many responsibilities, architectural
              bottlenecks that concentrate risk, hidden coupling revealed by git co-change patterns.
              These are issues that per-file analysis structurally cannot detect.
            </p>
          </div>
        </section>

        {/* What ESLint Catches */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            What ESLint Catches That Repotoire Doesn&apos;t
          </h2>
          <div className="card-elevated rounded-xl p-6">
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li className="flex items-start gap-2">
                <span className="text-muted-foreground/60 mt-0.5 shrink-0">&bull;</span>
                <span>Code style and formatting (semicolons, indentation, naming conventions)</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-muted-foreground/60 mt-0.5 shrink-0">&bull;</span>
                <span>Framework-specific rules (React hooks order, Vue template syntax, accessibility)</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-muted-foreground/60 mt-0.5 shrink-0">&bull;</span>
                <span>Import ordering and organization</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-muted-foreground/60 mt-0.5 shrink-0">&bull;</span>
                <span>Deterministic auto-fix for hundreds of rules</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-muted-foreground/60 mt-0.5 shrink-0">&bull;</span>
                <span>Highly customizable per-rule configuration</span>
              </li>
            </ul>
          </div>
        </section>

        {/* What Repotoire Catches */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            What Repotoire Catches That ESLint Can&apos;t
          </h2>
          <div className="card-elevated rounded-xl p-6">
            <ul className="space-y-2 text-sm text-muted-foreground">
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Circular dependencies detected via Tarjan&apos;s strongly connected components</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>God classes identified through fan-in/fan-out graph metrics</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Architectural bottlenecks via PageRank and betweenness centrality</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Hidden coupling from git co-change temporal analysis</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Community misplacement via Louvain clustering</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Single points of failure (articulation points in the call graph)</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5 shrink-0">&#10003;</span>
                <span>Cross-language analysis (same tool for Python, Rust, Go, Java, and more)</span>
              </li>
            </ul>
          </div>
        </section>

        {/* Using Both Together */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Using Both Together
          </h2>
          <div className="card-elevated rounded-xl p-6 space-y-4">
            <p className="text-muted-foreground leading-relaxed">
              Repotoire and ESLint are complementary tools. A recommended setup for JavaScript/TypeScript projects:
            </p>
            <div className="grid md:grid-cols-2 gap-4">
              <div className="bg-muted/20 rounded-lg p-4">
                <h3 className="font-display font-bold text-foreground text-sm mb-2">ESLint (on every save)</h3>
                <p className="text-xs text-muted-foreground">
                  Code style, per-file bugs, React hooks rules, import ordering, accessibility.
                  Runs in your editor with instant feedback.
                </p>
              </div>
              <div className="bg-muted/20 rounded-lg p-4">
                <h3 className="font-display font-bold text-foreground text-sm mb-2">Repotoire (in CI or pre-commit)</h3>
                <p className="text-xs text-muted-foreground">
                  Architectural health, circular dependencies, coupling analysis, bottleneck detection.
                  Runs on the full codebase to catch structural drift.
                </p>
              </div>
            </div>
            <pre className="bg-muted/30 rounded-lg p-4 font-mono text-xs text-muted-foreground overflow-x-auto">
{`# Example CI pipeline
- name: Lint (ESLint)
  run: npx eslint .

- name: Architecture check (Repotoire)
  uses: Zach-hammad/repotoire-action@v1
  with:
    fail-on: high`}
            </pre>
          </div>
        </section>

        {/* Setup Comparison */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Setup
          </h2>
          <div className="grid md:grid-cols-2 gap-6">
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-primary mb-3">Repotoire</h3>
              <pre className="bg-muted/30 rounded-lg p-3 font-mono text-xs text-muted-foreground overflow-x-auto">
{`# Install
cargo binstall repotoire

# Analyze (zero config)
repotoire analyze .`}
              </pre>
              <p className="text-xs text-muted-foreground mt-3">
                No configuration file needed. Works on any supported language immediately.
              </p>
            </div>
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-muted-foreground mb-3">ESLint</h3>
              <pre className="bg-muted/30 rounded-lg p-3 font-mono text-xs text-muted-foreground overflow-x-auto">
{`# Install
npm init @eslint/config@latest

# Lint
npx eslint .`}
              </pre>
              <p className="text-xs text-muted-foreground mt-3">
                Requires a config file. Highly configurable with plugins, presets, and overrides.
              </p>
            </div>
          </div>
        </section>

        {/* Verdict */}
        <section className="mb-16">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Verdict
          </h2>
          <div className="card-elevated rounded-xl p-6">
            <p className="text-muted-foreground leading-relaxed mb-4">
              This isn&apos;t an either/or choice. ESLint is the best tool for JavaScript/TypeScript
              per-file linting &mdash; it has an unmatched plugin ecosystem and deep framework integration.
              Repotoire is the best tool for understanding your codebase&apos;s architecture across all files
              and languages.
            </p>
            <p className="text-muted-foreground leading-relaxed">
              Use ESLint to keep individual files clean. Use Repotoire to keep your architecture healthy.
              Together, they cover both the micro (code style, per-file bugs) and macro (structure,
              dependencies, coupling) dimensions of code quality.
            </p>
          </div>
        </section>

        {/* CTA */}
        <section className="text-center">
          <div className="card-elevated rounded-xl p-8">
            <h2 className="text-2xl font-display font-bold text-foreground mb-3">
              See what ESLint can&apos;t see
            </h2>
            <p className="text-muted-foreground mb-6">
              Run Repotoire alongside ESLint and discover the architectural issues hiding in your codebase.
            </p>
            <pre className="bg-muted/30 rounded-lg p-4 font-mono text-sm text-muted-foreground mb-6 inline-block">
              cargo binstall repotoire &amp;&amp; repotoire analyze .
            </pre>
            <div className="flex items-center justify-center gap-4">
              <Link
                href="/docs/cli"
                className="inline-flex items-center justify-center rounded-md bg-primary px-6 py-3 text-sm font-display font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors"
              >
                Get Started Free
              </Link>
              <a
                href="https://github.com/Zach-hammad/repotoire"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center rounded-md border border-border px-6 py-3 text-sm font-display font-medium text-foreground hover:bg-muted/50 transition-colors"
              >
                View on GitHub
              </a>
            </div>
          </div>
        </section>
      </div>
    </article>
  )
}
