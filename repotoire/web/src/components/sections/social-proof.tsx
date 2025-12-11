"use client"

import { useEffect, useRef, useState } from "react"

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
    <section ref={sectionRef} className="py-24 px-4 sm:px-6 lg:px-8 border-t border-border">
      <div className="max-w-4xl mx-auto">
        <div className={`card-elevated rounded-xl p-8 md:p-10 mb-12 opacity-0 ${isVisible ? "animate-fade-up" : ""}`}>
          <div className="flex flex-col md:flex-row gap-6 items-start">
            <div className="flex-shrink-0">
              <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary/20 to-primary/5 flex items-center justify-center">
                <span className="text-lg font-display font-bold text-primary">JK</span>
              </div>
            </div>
            <div>
              <blockquote className="text-lg md:text-xl text-foreground mb-4 leading-relaxed">
                "We had circular dependencies silently breaking hot reload for 2 years. Repotoire found 47 of them in
                our monorepo.
                <span className="text-primary font-medium"> Fixed them all in one afternoon</span> with the AI
                auto-fix."
              </blockquote>
              <div className="flex items-center gap-3">
                <div>
                  <div className="font-display font-medium text-foreground">James Kim</div>
                  <div className="text-sm text-muted-foreground">Staff Engineer at Lattice</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}>
            <div className="text-2xl md:text-3xl font-display font-bold text-foreground mb-1">47</div>
            <div className="text-xs md:text-sm text-muted-foreground">cycles fixed in one repo</div>
          </div>

          <div
            className={`text-center border-x border-border opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}
          >
            <div className="text-2xl md:text-3xl font-display font-bold text-foreground mb-1">8s</div>
            <div className="text-xs md:text-sm text-muted-foreground">re-analysis (was 12 min)</div>
          </div>

          <div className={`text-center opacity-0 ${isVisible ? "animate-fade-up delay-400" : ""}`}>
            <div className="text-2xl md:text-3xl font-display font-bold text-foreground mb-1">18%</div>
            <div className="text-xs md:text-sm text-muted-foreground">bundle size reduction</div>
          </div>
        </div>
      </div>
    </section>
  )
}
