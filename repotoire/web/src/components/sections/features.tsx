"use client"

export function Features() {
  return (
    <section id="features" className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4 text-center">8 hybrid detectors</h2>
        <p className="text-lg text-muted-foreground max-w-2xl mx-auto text-center mb-12">
          Graph algorithms + traditional tools working together. Each detector integrates Ruff, Pylint, Mypy, Bandit, or
          Semgrep with cross-file graph analysis.
        </p>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Detector 1 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Circular Dependencies</span>
              <span className="w-2 h-2 rounded-full bg-red-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Detects import cycles across any depth using Tarjan's algorithm on the dependency graph.
            </p>
            <code className="text-xs text-red-400">A → B → C → A</code>
          </div>

          {/* Detector 2 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Dead Code</span>
              <span className="w-2 h-2 rounded-full bg-amber-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Finds exports never imported anywhere. Cross-file analysis, not just single-file unused vars.
            </p>
            <code className="text-xs text-amber-400">847 unused exports</code>
          </div>

          {/* Detector 3 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Bottleneck Analysis</span>
              <span className="w-2 h-2 rounded-full bg-blue-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Identifies high-fanin modules that cause cascading test failures when changed.
            </p>
            <code className="text-xs text-blue-400">utils.ts → 234 dependents</code>
          </div>

          {/* Detector 4 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Modularity Index</span>
              <span className="w-2 h-2 rounded-full bg-purple-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Measures cohesion within and coupling between modules. Flags tightly coupled packages.
            </p>
            <code className="text-xs text-purple-400">Q = 0.42 (target: 0.7)</code>
          </div>

          {/* Detector 5 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Code Smells</span>
              <span className="w-2 h-2 rounded-full bg-amber-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Integrates Ruff + Pylint findings with semantic context from the knowledge graph.
            </p>
            <code className="text-xs text-amber-400">Long method, god class</code>
          </div>

          {/* Detector 6 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Type Coverage</span>
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Mypy integration with cross-module type flow analysis. Finds untyped boundaries.
            </p>
            <code className="text-xs text-emerald-400">78% typed (12 gaps)</code>
          </div>

          {/* Detector 7 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Security Scan</span>
              <span className="w-2 h-2 rounded-full bg-red-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Bandit + Semgrep rules enhanced with data flow analysis through the graph.
            </p>
            <code className="text-xs text-red-400">SQL injection path found</code>
          </div>

          {/* Detector 8 */}
          <div className="rounded-lg border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm font-medium text-foreground">Complexity Hotspots</span>
              <span className="w-2 h-2 rounded-full bg-amber-500" />
            </div>
            <p className="text-xs text-muted-foreground mb-2">
              Combines cyclomatic complexity with change frequency from git history.
            </p>
            <code className="text-xs text-amber-400">payment.py: high churn + complexity</code>
          </div>
        </div>

        {/* AI Auto-fix callout */}
        <div className="mt-12 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-6 text-center">
          <h3 className="text-lg font-semibold text-foreground mb-2">AI-powered auto-fix</h3>
          <p className="text-sm text-muted-foreground max-w-xl mx-auto">
            Every issue comes with a GPT-4o generated fix using RAG over your codebase. 70%+ approval rate in
            production. Apply with one click or export as PR.
          </p>
        </div>
      </div>
    </section>
  )
}
