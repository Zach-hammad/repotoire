"use client"

import { useEffect, useRef, useState } from "react"

export function HowItWorks() {
  const sectionRef = useRef<HTMLElement>(null)
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true)
        }
      },
      { threshold: 0.1 },
    )

    if (sectionRef.current) {
      observer.observe(sectionRef.current)
    }

    return () => observer.disconnect()
  }, [])

  return (
    <section
      ref={sectionRef}
      id="how-it-works"
      className="py-24 px-4 sm:px-6 lg:px-8 border-t border-border"
      aria-labelledby="how-it-works-heading"
    >
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-12">
          <h2
            id="how-it-works-heading"
            className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 opacity-0 ${
              isVisible ? "animate-fade-up" : ""
            }`}
          >
            <span className="font-serif italic text-muted-foreground">Setup in</span>{" "}
            <span className="text-gradient font-display font-semibold">5 minutes</span>
          </h2>
          <p
            className={`text-muted-foreground max-w-lg mx-auto opacity-0 ${
              isVisible ? "animate-fade-up delay-100" : ""
            }`}
          >
            Connect your repo, build the graph, get actionable insights.
          </p>
        </div>

        <div className="grid lg:grid-cols-3 gap-6" role="list" aria-label="Setup steps">
          {/* Step 1 */}
          <div role="listitem" className={`card-elevated rounded-xl p-6 opacity-0 ${isVisible ? "animate-scale-in delay-200" : ""}`}>
            <div className="flex items-center gap-3 mb-5">
              <span className="text-3xl font-serif italic text-muted-foreground/30" aria-hidden="true">01</span>
              <h3 className="text-lg font-display font-semibold text-foreground">Connect</h3>
            </div>
            <div className="bg-muted rounded-lg p-4 font-mono text-sm">
              <div className="text-muted-foreground"># Install CLI</div>
              <div className="text-primary mt-1">cargo install repotoire</div>
              <div className="text-muted-foreground mt-3">✓ Installed v0.1.44</div>
              <div className="text-muted-foreground">✓ 9 languages supported</div>
            </div>
          </div>

          {/* Step 2 */}
          <div role="listitem" className={`card-elevated rounded-xl p-6 opacity-0 ${isVisible ? "animate-scale-in delay-300" : ""}`}>
            <div className="flex items-center gap-3 mb-5">
              <span className="text-3xl font-serif italic text-muted-foreground/30" aria-hidden="true">02</span>
              <h3 className="text-lg font-display font-semibold text-foreground">Analyze</h3>
            </div>
            <div className="bg-muted rounded-lg p-4 font-mono text-sm">
              <div className="text-muted-foreground">Building knowledge graph...</div>
              <div className="mt-3 h-1.5 bg-background rounded-full overflow-hidden" role="progressbar" aria-valuenow={100} aria-valuemin={0} aria-valuemax={100} aria-label="Analysis complete">
                <div className="h-full w-full bg-primary rounded-full" />
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span className="text-muted-foreground">Nodes:</span> <span className="text-primary">12,458</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Edges:</span> <span className="text-primary">47,921</span>
                </div>
              </div>
            </div>
          </div>

          {/* Step 3 */}
          <div role="listitem" className={`card-elevated rounded-xl p-6 opacity-0 ${isVisible ? "animate-scale-in delay-400" : ""}`}>
            <div className="flex items-center gap-3 mb-5">
              <span className="text-3xl font-serif italic text-muted-foreground/30" aria-hidden="true">03</span>
              <h3 className="text-lg font-display font-semibold text-foreground">Fix</h3>
            </div>
            <div className="bg-muted rounded-lg p-4 text-sm">
              <div className="flex items-center gap-2 text-error font-medium mb-3">
                <span className="w-2 h-2 rounded-full bg-error" aria-hidden="true" />
                Circular dependency
              </div>
              <div className="text-xs text-muted-foreground mb-4 font-mono">auth.ts → user.ts → auth.ts</div>
              <button
                type="button"
                className="w-full bg-primary hover:bg-primary/90 text-primary-foreground text-sm py-2 rounded-lg font-display font-medium transition-colors"
                aria-label="Apply AI Fix for circular dependency"
              >
                Apply AI Fix
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
