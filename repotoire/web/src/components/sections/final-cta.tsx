"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"

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
          className={`text-lg text-muted-foreground mb-10 max-w-xl mx-auto opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}
        >
          Connect your repo. Get a health score in 5 minutes. Fix issues with AI.
        </p>

        <div
          className={`flex flex-col sm:flex-row items-center justify-center gap-4 mb-8 opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}
        >
          <Button
            asChild
            size="lg"
            className="bg-primary hover:bg-primary/90 text-primary-foreground h-14 px-8 text-lg font-display font-medium"
          >
            <Link href="/dashboard">Start Free — No Credit Card</Link>
          </Button>
          <Button
            asChild
            size="lg"
            variant="outline"
            className="h-14 px-8 text-lg font-display border-border hover:bg-muted bg-transparent"
          >
            <Link href="/contact">Contact Us</Link>
          </Button>
        </div>

        <p className={`text-sm text-muted-foreground opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}>
          7-day free trial. No credit card required.
        </p>
      </div>
    </section>
  )
}
