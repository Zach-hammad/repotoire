"use client"

import { Button } from "@/components/ui/button"

export function Hero() {
  return (
    <section className="relative pt-28 pb-16 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-12 items-center">
          {/* Left: Copy */}
          <div>
            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-foreground mb-6 text-balance">
              Your codebase as a graph.
              <br />
              <span className="text-emerald-400">Every issue visible.</span>
            </h1>

            <p className="text-lg text-muted-foreground mb-8 max-w-lg">
              Repotoire builds a knowledge graph of your code—combining AST analysis, semantic understanding, and graph
              algorithms—to find architectural debt, code smells, and issues linters miss.
            </p>

            {/* CTAs */}
            <div className="flex flex-col sm:flex-row gap-3 mb-8">
              <Button
                size="lg"
                className="bg-emerald-500 hover:bg-emerald-600 text-white h-12 px-8 text-base font-medium"
              >
                Analyze Your Repo Free
              </Button>
              <Button size="lg" variant="outline" className="h-12 px-8 text-base bg-transparent">
                See Sample Report
              </Button>
            </div>

            <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted-foreground">
              <span>8 hybrid detectors</span>
              <span>·</span>
              <span>10-100x faster re-analysis</span>
              <span>·</span>
              <span>AI-powered auto-fix</span>
            </div>
          </div>

          <div className="rounded-lg border border-border bg-card overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-muted">
              <span className="text-sm font-medium text-foreground">myapp/</span>
              <span className="text-xs text-muted-foreground">Last scan: 2 min ago</span>
            </div>

            {/* Health Score */}
            <div className="p-4 border-b border-border">
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-muted-foreground">Health Score</span>
                <span className="text-2xl font-bold text-amber-400">72</span>
              </div>
              <div className="grid grid-cols-3 gap-4 text-xs">
                <div>
                  <div className="text-muted-foreground mb-1">Structure (40%)</div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-emerald-500 rounded-full" style={{ width: "85%" }} />
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground mb-1">Quality (30%)</div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-amber-400 rounded-full" style={{ width: "68%" }} />
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground mb-1">Architecture (30%)</div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-red-400 rounded-full" style={{ width: "52%" }} />
                  </div>
                </div>
              </div>
            </div>

            {/* Issues found */}
            <div className="p-4 space-y-3 font-mono text-xs">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-foreground">3 circular dependencies</span>
                </div>
                <button className="text-emerald-400 hover:underline">Fix all</button>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="text-foreground">12 architectural bottlenecks</span>
                </div>
                <button className="text-emerald-400 hover:underline">View</button>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  <span className="text-foreground">847 dead exports</span>
                </div>
                <button className="text-emerald-400 hover:underline">Fix all</button>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  <span className="text-foreground">23 modularity issues</span>
                </div>
                <button className="text-emerald-400 hover:underline">View</button>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-blue-500" />
                  <span className="text-foreground">156 code quality findings</span>
                </div>
                <button className="text-emerald-400 hover:underline">View</button>
              </div>
            </div>

            {/* Integrations */}
            <div className="px-4 py-3 border-t border-border bg-muted text-xs text-muted-foreground">
              Powered by: Ruff · Pylint · Mypy · Bandit · Semgrep + Graph Analysis
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
