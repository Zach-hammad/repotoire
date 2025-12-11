"use client"

import { useEffect, useRef, useState } from "react"

export function ProblemSolution() {
  const sectionRef = useRef<HTMLElement>(null)
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setIsVisible(true)
      },
      { threshold: 0.2 },
    )
    if (sectionRef.current) observer.observe(sectionRef.current)
    return () => observer.disconnect()
  }, [])

  return (
    <section ref={sectionRef} className="py-24 px-4 sm:px-6 lg:px-8 border-t border-border">
      <div className="max-w-4xl mx-auto">
        <h2
          className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 text-center opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
        >
          <span className="font-serif italic text-muted-foreground">Linters check files.</span>
          <br />
          <span className="font-serif italic text-muted-foreground">Your bugs live in</span>{" "}
          <span className="text-gradient font-display font-semibold">relationships.</span>
        </h2>
        <p
          className={`text-muted-foreground text-center mb-16 max-w-2xl mx-auto opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}
        >
          Circular dependencies. Dead code spanning 10 files. Modules that everything depends on. These issues don't
          live in a single file—they live in how your code connects.
        </p>

        <div className={`grid md:grid-cols-2 gap-8 mb-16 opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}>
          {/* Traditional approach */}
          <div className="text-center">
            <div className="text-xs font-display font-medium text-muted-foreground mb-4 uppercase tracking-wider">
              Traditional linters see
            </div>
            <div className="flex justify-center gap-3 mb-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <div
                  key={i}
                  className="w-12 h-14 rounded bg-muted border border-border flex items-center justify-center"
                >
                  <span className="text-xs text-muted-foreground font-mono">.py</span>
                </div>
              ))}
            </div>
            <p className="text-sm text-muted-foreground">Isolated files. No context.</p>
          </div>

          {/* Repotoire approach */}
          <div className="text-center">
            <div className="text-xs font-display font-medium text-primary mb-4 uppercase tracking-wider">
              Repotoire sees
            </div>
            <div className="relative h-14 mb-4">
              <svg className="w-full h-full" viewBox="0 0 200 56">
                {/* Nodes */}
                <circle
                  cx="30"
                  cy="28"
                  r="8"
                  className="fill-primary/20 stroke-primary"
                  strokeWidth="1.5"
                />
                <circle
                  cx="70"
                  cy="12"
                  r="6"
                  className="fill-muted stroke-border"
                  strokeWidth="1"
                />
                <circle
                  cx="70"
                  cy="44"
                  r="6"
                  className="fill-muted stroke-border"
                  strokeWidth="1"
                />
                <circle
                  cx="110"
                  cy="28"
                  r="8"
                  className="fill-red-500/20 stroke-red-500"
                  strokeWidth="1.5"
                />
                <circle
                  cx="150"
                  cy="12"
                  r="6"
                  className="fill-muted stroke-border"
                  strokeWidth="1"
                />
                <circle
                  cx="150"
                  cy="44"
                  r="6"
                  className="fill-muted stroke-border"
                  strokeWidth="1"
                />
                <circle
                  cx="180"
                  cy="28"
                  r="6"
                  className="fill-muted stroke-border"
                  strokeWidth="1"
                />

                {/* Edges */}
                <line x1="38" y1="24" x2="64" y2="14" className="stroke-border" strokeWidth="1" />
                <line x1="38" y1="32" x2="64" y2="42" className="stroke-border" strokeWidth="1" />
                <line x1="76" y1="14" x2="102" y2="26" className="stroke-border" strokeWidth="1" />
                <line x1="76" y1="42" x2="102" y2="30" className="stroke-border" strokeWidth="1" />
                <line x1="118" y1="24" x2="144" y2="14" className="stroke-border" strokeWidth="1" />
                <line x1="118" y1="32" x2="144" y2="42" className="stroke-border" strokeWidth="1" />
                <line x1="156" y1="14" x2="174" y2="24" className="stroke-border" strokeWidth="1" />
                <line x1="156" y1="42" x2="174" y2="32" className="stroke-border" strokeWidth="1" />

                {/* Circular dependency indicator */}
                <path
                  d="M 76 12 Q 90 -5 104 12"
                  fill="none"
                  className="stroke-red-500"
                  strokeWidth="1.5"
                  strokeDasharray="3 2"
                />
              </svg>
            </div>
            <p className="text-sm text-foreground">A knowledge graph. Every connection mapped.</p>
          </div>
        </div>

        <div
          className={`card-elevated rounded-xl overflow-hidden opacity-0 ${isVisible ? "animate-scale-in delay-300" : ""}`}
        >
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left p-4 font-display font-medium text-muted-foreground"></th>
                <th className="text-center p-4 font-display font-medium text-muted-foreground">ESLint / Pylint</th>
                <th className="text-center p-4 font-display font-medium text-primary">Repotoire</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              <tr>
                <td className="p-4 text-foreground">Analysis scope</td>
                <td className="p-4 text-center text-muted-foreground">Single file</td>
                <td className="p-4 text-center text-foreground">Entire codebase graph</td>
              </tr>
              <tr>
                <td className="p-4 text-foreground">Circular deps</td>
                <td className="p-4 text-center">
                  <span className="text-red-500">✗</span>
                </td>
                <td className="p-4 text-center">
                  <span className="text-primary">✓</span>
                </td>
              </tr>
              <tr>
                <td className="p-4 text-foreground">Cross-file dead code</td>
                <td className="p-4 text-center">
                  <span className="text-red-500">✗</span>
                </td>
                <td className="p-4 text-center">
                  <span className="text-primary">✓</span>
                </td>
              </tr>
              <tr>
                <td className="p-4 text-foreground">Re-analysis speed</td>
                <td className="p-4 text-center text-muted-foreground">Full rescan</td>
                <td className="p-4 text-center text-foreground">Incremental (100x faster)</td>
              </tr>
              <tr>
                <td className="p-4 text-foreground">Auto-fix</td>
                <td className="p-4 text-center text-muted-foreground">Basic suggestions</td>
                <td className="p-4 text-center text-foreground">GPT-4o + RAG patches</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
