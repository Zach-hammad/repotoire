"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"

export function Hero() {
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    setIsVisible(true)
  }, [])

  return (
    <section className="relative min-h-screen pt-32 pb-20 px-4 sm:px-6 lg:px-8 dot-grid">
      <div className="max-w-6xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          {/* Left: Copy */}
          <div>
            <h1
              className={`text-5xl sm:text-6xl lg:text-7xl tracking-tight text-foreground mb-6 leading-[1.05] opacity-0 ${
                isVisible ? "animate-fade-up" : ""
              }`}
            >
              <span className="font-serif italic text-muted-foreground">Your codebase,</span>
              <br />
              <span className="text-gradient font-display font-bold">understood.</span>
            </h1>

            <p
              className={`text-lg text-muted-foreground mb-10 max-w-md leading-relaxed opacity-0 ${
                isVisible ? "animate-fade-up delay-100" : ""
              }`}
            >
              Repotoire builds a knowledge graph of your code—finding architectural debt, code smells, and issues that
              linters miss.
            </p>

            <div
              className={`flex flex-col sm:flex-row gap-4 mb-8 opacity-0 ${
                isVisible ? "animate-fade-up delay-200" : ""
              }`}
            >
              <Button
                asChild
                size="lg"
                className="bg-primary hover:bg-primary/90 text-primary-foreground h-12 px-6 text-base font-display font-medium"
              >
                <Link href="/dashboard">Analyze Your Repo</Link>
              </Button>
              <Button
                asChild
                size="lg"
                variant="outline"
                className="h-12 px-6 text-base font-display border-border hover:bg-muted bg-transparent"
              >
                <Link href="#features">See Sample Report</Link>
              </Button>
            </div>

            <div className={`flex items-center gap-6 mb-8 opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}>
              <a
                href="https://github.com/repotoire/repotoire"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                <span className="font-display font-medium">GitHub</span>
              </a>
              <span className="w-px h-4 bg-border" />
              <span className="text-sm text-muted-foreground">Used by 500+ teams</span>
            </div>

            <div className={`opacity-0 ${isVisible ? "animate-fade-up delay-400" : ""}`}>
              <p className="text-xs text-muted-foreground mb-3 uppercase tracking-wider font-display">
                Trusted by engineers at
              </p>
              <div className="flex items-center gap-6 opacity-60">
                {["Stripe", "Vercel", "Linear", "Notion"].map((company) => (
                  <span key={company} className="text-sm font-display font-medium text-muted-foreground">
                    {company}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Right: Product card */}
          <div className={`opacity-0 ${isVisible ? "animate-slide-in-right delay-200" : ""}`}>
            <div className="card-elevated rounded-xl overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-border">
                <div className="flex items-center gap-3">
                  <div className="flex gap-1.5">
                    <span className="w-3 h-3 rounded-full bg-red-500/80" />
                    <span className="w-3 h-3 rounded-full bg-amber-500/80" />
                    <span className="w-3 h-3 rounded-full bg-emerald-500/80" />
                  </div>
                  <span className="text-sm font-mono text-muted-foreground">myapp/</span>
                </div>
                <span className="text-xs text-muted-foreground">2 min ago</span>
              </div>

              {/* Health Score */}
              <div className="p-5 border-b border-border">
                <div className="flex items-baseline justify-between mb-4">
                  <span className="text-sm text-muted-foreground font-display">Health Score</span>
                  <span className="text-4xl font-display font-bold text-foreground">72</span>
                </div>
                <div className="grid grid-cols-3 gap-4 text-xs">
                  <div>
                    <div className="text-muted-foreground mb-1.5">Structure</div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-emerald-500 rounded-full" style={{ width: "85%" }} />
                    </div>
                    <div className="text-muted-foreground mt-1">85%</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1.5">Quality</div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-amber-500 rounded-full" style={{ width: "68%" }} />
                    </div>
                    <div className="text-muted-foreground mt-1">68%</div>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1.5">Architecture</div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div className="h-full bg-red-500 rounded-full" style={{ width: "52%" }} />
                    </div>
                    <div className="text-muted-foreground mt-1">52%</div>
                  </div>
                </div>
              </div>

              {/* Issues */}
              <div className="p-5 space-y-2 font-mono text-sm">
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-muted/50 hover:bg-muted transition-colors cursor-pointer group">
                  <div className="flex items-center gap-3">
                    <span className="w-2 h-2 rounded-full bg-red-500" />
                    <span className="text-foreground">3 circular dependencies</span>
                  </div>
                  <span className="text-primary text-xs opacity-0 group-hover:opacity-100 transition-opacity font-display">
                    Fix →
                  </span>
                </div>
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-muted/50 hover:bg-muted transition-colors cursor-pointer group">
                  <div className="flex items-center gap-3">
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    <span className="text-foreground">847 dead exports</span>
                  </div>
                  <span className="text-primary text-xs opacity-0 group-hover:opacity-100 transition-opacity font-display">
                    Fix →
                  </span>
                </div>
                <div className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-muted/50 hover:bg-muted transition-colors cursor-pointer group">
                  <div className="flex items-center gap-3">
                    <span className="w-2 h-2 rounded-full bg-blue-500" />
                    <span className="text-foreground">12 bottleneck modules</span>
                  </div>
                  <span className="text-primary text-xs opacity-0 group-hover:opacity-100 transition-opacity font-display">
                    View →
                  </span>
                </div>
              </div>

              {/* Footer */}
              <div className="px-5 py-3 border-t border-border flex items-center justify-between bg-muted/30">
                <span className="text-xs text-muted-foreground">Ruff · Pylint · Mypy · Bandit · Semgrep</span>
                <Button
                  size="sm"
                  className="h-7 text-xs font-display bg-primary hover:bg-primary/90 text-primary-foreground"
                >
                  Apply AI Fix
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
