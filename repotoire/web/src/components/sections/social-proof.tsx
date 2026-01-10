"use client"

import { useEffect, useRef, useState } from "react"
import { Code2, Zap, Shield, GitBranch } from "lucide-react"

export function SocialProof() {
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
      className="py-24 px-4 sm:px-6 lg:px-8 border-t border-border"
      aria-labelledby="social-proof-heading"
    >
      <div className="max-w-4xl mx-auto">
        {/* Product Highlights */}
        <div className={`text-center mb-12 opacity-0 ${isVisible ? "animate-fade-up" : ""}`}>
          <h2 id="social-proof-heading" className="text-2xl md:text-3xl font-display font-bold text-foreground mb-4">
            Built for Real Codebases
          </h2>
          <p className="text-muted-foreground max-w-2xl mx-auto">
            Graph-powered analysis that catches architectural issues linters miss
          </p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-6" role="list" aria-label="Product features">
          <div role="listitem" className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}>
            <div className="w-12 h-12 mx-auto mb-3 rounded-lg bg-primary/10 flex items-center justify-center" aria-hidden="true">
              <Code2 className="w-6 h-6 text-primary" />
            </div>
            <div className="text-sm font-display font-medium text-foreground mb-1">Python Support</div>
            <div className="text-xs text-muted-foreground">AST-based analysis</div>
          </div>

          <div role="listitem" className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}>
            <div className="w-12 h-12 mx-auto mb-3 rounded-lg bg-emerald-500/10 flex items-center justify-center" aria-hidden="true">
              <GitBranch className="w-6 h-6 text-emerald-500" />
            </div>
            <div className="text-sm font-display font-medium text-foreground mb-1">Graph Database</div>
            <div className="text-xs text-muted-foreground">Neo4j knowledge graph</div>
          </div>

          <div role="listitem" className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-400" : ""}`}>
            <div className="w-12 h-12 mx-auto mb-3 rounded-lg bg-amber-500/10 flex items-center justify-center" aria-hidden="true">
              <Zap className="w-6 h-6 text-amber-500" />
            </div>
            <div className="text-sm font-display font-medium text-foreground mb-1">8 Integrated Tools</div>
            <div className="text-xs text-muted-foreground">Ruff, Mypy, Bandit...</div>
          </div>

          <div role="listitem" className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-500" : ""}`}>
            <div className="w-12 h-12 mx-auto mb-3 rounded-lg bg-blue-500/10 flex items-center justify-center" aria-hidden="true">
              <Shield className="w-6 h-6 text-blue-500" />
            </div>
            <div className="text-sm font-display font-medium text-foreground mb-1">AI-Powered Fixes</div>
            <div className="text-xs text-muted-foreground">GPT-4o + RAG</div>
          </div>
        </div>
      </div>
    </section>
  )
}
