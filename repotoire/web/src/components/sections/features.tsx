"use client"

import { useEffect, useRef, useState } from "react"

const detectors = [
  { name: "Circular Dependencies", output: "A → B → C → A", color: "red" },
  { name: "Dead Code", output: "847 unused exports", color: "amber" },
  { name: "Bottlenecks", output: "utils.ts → 234 deps", color: "blue" },
  { name: "Modularity", output: "Q = 0.42 (target: 0.7)", color: "purple" },
  { name: "Code Smells", output: "god class detected", color: "amber" },
  { name: "Type Coverage", output: "78% typed (12 gaps)", color: "teal" },
  { name: "Security", output: "SQL injection path", color: "red" },
  { name: "Complexity", output: "high churn hotspot", color: "orange" },
]

const colorMap: Record<string, string> = {
  red: "bg-red-500/10 border-red-500/20 text-red-400",
  amber: "bg-amber-500/10 border-amber-500/20 text-amber-400",
  blue: "bg-blue-500/10 border-blue-500/20 text-blue-400",
  purple: "bg-purple-500/10 border-purple-500/20 text-purple-400",
  teal: "bg-teal-500/10 border-teal-500/20 text-teal-400",
  orange: "bg-orange-500/10 border-orange-500/20 text-orange-400",
}

const dotColorMap: Record<string, string> = {
  red: "bg-red-500",
  amber: "bg-amber-500",
  blue: "bg-blue-500",
  purple: "bg-purple-500",
  teal: "bg-teal-500",
  orange: "bg-orange-500",
}

export function Features() {
  const sectionRef = useRef<HTMLElement>(null)
  const [isVisible, setIsVisible] = useState(false)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setIsVisible(true)
      },
      { threshold: 0.1 },
    )
    if (sectionRef.current) observer.observe(sectionRef.current)
    return () => observer.disconnect()
  }, [])

  return (
    <section ref={sectionRef} id="features" className="py-24 px-4 sm:px-6 lg:px-8 dot-grid">
      <div className="max-w-6xl mx-auto">
        <h2
          className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 text-center opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
        >
          <span className="font-serif italic text-muted-foreground">8 integrated</span>{" "}
          <span className="text-gradient font-display font-semibold">analysis tools</span>
        </h2>
        <p
          className={`text-muted-foreground max-w-xl mx-auto text-center mb-12 opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}
        >
          Graph algorithms + Ruff, Pylint, Mypy, Bandit, Semgrep working together.
        </p>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {detectors.map((detector, i) => (
            <div
              key={detector.name}
              className={`card-elevated rounded-xl p-5 opacity-0 hover:border-border/80 transition-colors ${isVisible ? "animate-scale-in" : ""}`}
              style={{ animationDelay: `${150 + i * 50}ms` }}
            >
              <div className="flex items-center gap-2 mb-3">
                <span className={`w-2 h-2 rounded-full ${dotColorMap[detector.color]}`} />
                <span className="text-sm font-medium text-foreground">{detector.name}</span>
              </div>
              <code
                className={`inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono ${colorMap[detector.color]}`}
              >
                {detector.output}
              </code>
            </div>
          ))}
        </div>

        <div
          className={`mt-16 card-elevated rounded-xl p-8 text-center opacity-0 ${isVisible ? "animate-fade-up delay-500" : ""}`}
        >
          <h3 className="text-2xl tracking-tight text-foreground mb-3">
            <span className="font-serif italic text-muted-foreground">AI-powered</span>{" "}
            <span className="text-gradient font-display font-semibold">auto-fix</span>
          </h3>
          <p className="text-muted-foreground max-w-lg mx-auto">
            Every issue comes with a GPT-4o generated fix using RAG over your codebase. Review and apply with one click.
          </p>
        </div>
      </div>
    </section>
  )
}
