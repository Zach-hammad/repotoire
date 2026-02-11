"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Terminal, Github } from "lucide-react"

export function FinalCTA() {
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
    <section
      ref={sectionRef}
      className="py-32 px-4 sm:px-6 lg:px-8 bg-primary/5 border-y border-primary/10"
      aria-labelledby="final-cta-heading"
    >
      <div className="max-w-3xl mx-auto text-center">
        <h2
          id="final-cta-heading"
          className={`text-4xl sm:text-5xl lg:text-6xl tracking-tight text-foreground mb-6 text-balance opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
        >
          <span className="font-display font-bold">Ready to understand</span>
          <br />
          <span className="font-serif italic text-muted-foreground">your codebase?</span>
        </h2>

        <p
          className={`text-lg text-muted-foreground mb-6 max-w-xl mx-auto opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}
        >
          One command. Full analysis. No sign-up required.
        </p>

        {/* Terminal command */}
        <div
          className={`max-w-md mx-auto mb-8 opacity-0 ${isVisible ? "animate-fade-up delay-150" : ""}`}
        >
          <div className="bg-background border border-border rounded-lg p-4 font-mono text-sm text-left">
            <span className="text-primary">$</span>{" "}
            <span className="text-foreground">cargo install repotoire && repotoire analyze .</span>
          </div>
        </div>

        <div
          className={`flex flex-col sm:flex-row items-center justify-center gap-4 mb-8 opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}
        >
          <Button
            asChild
            size="lg"
            className="bg-primary hover:bg-primary/90 text-primary-foreground h-14 px-8 text-lg font-display font-medium"
          >
            <Link href="/docs/cli">
              <Terminal className="w-5 h-5 mr-2" />
              Get Started
            </Link>
          </Button>
          <Button
            asChild
            size="lg"
            variant="outline"
            className="h-14 px-8 text-lg font-display border-border hover:bg-muted bg-transparent"
          >
            <a 
              href="https://github.com/repotoire/repotoire"
              target="_blank"
              rel="noopener noreferrer"
            >
              <Github className="w-5 h-5 mr-2" />
              View Source
            </a>
          </Button>
        </div>

        <p className={`text-sm text-muted-foreground opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}>
          Open source · MIT License · Works offline
        </p>
      </div>
    </section>
  )
}
