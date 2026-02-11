"use client"

import { useEffect, useRef, useState } from "react"

const detectors = [
  { name: "Circular Dependencies", output: "A → B → C → A", color: "orange" },
  { name: "Dead Code", output: "847 unused exports", color: "amber" },
  { name: "Data Flow", output: "user input → SQL", color: "red" },
  { name: "Code Clones", output: "87% similar (3 files)", color: "purple" },
  { name: "Architecture", output: "api → core violation", color: "blue" },
  { name: "Type Coverage", output: "78% typed (12 gaps)", color: "teal" },
  { name: "Git History", output: "3 devs own 80% code", color: "primary" },
  { name: "Complexity", output: "cyclomatic: 47", color: "pink" },
]

const colorMap: Record<string, string> = {
  red: "bg-error/10 border-error/20 text-error",
  amber: "bg-warning/10 border-warning/20 text-warning",
  blue: "bg-info-semantic/10 border-info-semantic/20 text-info-semantic",
  purple: "bg-primary/10 border-primary/20 text-primary",
  teal: "bg-success/10 border-success/20 text-success",
  orange: "bg-warning/10 border-warning/20 text-warning",
  primary: "bg-primary/10 border-primary/20 text-primary",
  pink: "bg-primary/10 border-primary/20 text-primary",
}

const dotColorMap: Record<string, string> = {
  red: "bg-error",
  amber: "bg-warning",
  blue: "bg-info-semantic",
  purple: "bg-primary",
  teal: "bg-success",
  orange: "bg-warning",
  primary: "bg-primary",
  pink: "bg-primary",
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
    <section
      ref={sectionRef}
      id="features"
      className="py-24 px-4 sm:px-6 lg:px-8 dot-grid"
      aria-labelledby="features-heading"
    >
      <div className="max-w-6xl mx-auto">
        <h2
          id="features-heading"
          className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 text-center opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
        >
          <span className="font-serif italic text-muted-foreground">81 detectors,</span>{" "}
          <span className="text-gradient font-display font-semibold">9 languages</span>
        </h2>
        <p
          className={`text-muted-foreground max-w-xl mx-auto text-center mb-12 opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}
        >
          Python, TypeScript, Go, Java, Rust, C/C++, C#, Kotlin — all parsed with tree-sitter.
        </p>

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4" role="list" aria-label="Analysis tools">
          {detectors.map((detector, i) => (
            <div
              key={detector.name}
              role="listitem"
              className={`card-elevated rounded-xl p-5 opacity-0 hover:border-border/80 transition-colors ${isVisible ? "animate-scale-in" : ""}`}
              style={{ animationDelay: `${150 + i * 50}ms` }}
            >
              <div className="flex items-center gap-2 mb-3">
                <span className={`w-2 h-2 rounded-full ${dotColorMap[detector.color]}`} aria-hidden="true" />
                <span className="text-sm font-medium text-foreground">{detector.name}</span>
              </div>
              <code
                className={`inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono ${colorMap[detector.color]}`}
                aria-label={`Example output: ${detector.output}`}
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
            <span className="text-gradient font-display font-semibold">auto-fix (BYOK)</span>
          </h3>
          <p className="text-muted-foreground max-w-lg mx-auto">
            Every issue comes with an AI-generated fix using RAG over your codebase. Bring your own API key — OpenAI, Anthropic, or DeepInfra.
          </p>
        </div>
      </div>
    </section>
  )
}
