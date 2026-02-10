"use client"

/**
 * Pricing Section - CLI-First Model
 *
 * Simple pricing focused on CLI usage:
 * - FREE: Full local analysis (no sign-up)
 * - PRO: AI features (BYOK or subscription)
 * - Teams: Dashboard + GitHub (coming soon)
 */

import { useState, useEffect, useRef } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Terminal, Sparkles, Check } from "lucide-react"

const tiers = [
  {
    name: "Free",
    price: { monthly: "0", annual: "0" },
    description: "For individual developers",
    icon: Terminal,
    features: [
      "Full code analysis",
      "47 built-in detectors",
      "Knowledge graph queries",
      "Git history analysis",
      "MCP server for AI assistants",
      "Unlimited local repos",
    ],
    cta: "Install CLI",
    href: "/docs/cli",
    highlighted: false,
    note: "No sign-up required",
  },
  {
    name: "Pro",
    price: { monthly: "0", annual: "0" },
    description: "AI-powered fixes",
    icon: Sparkles,
    features: [
      "Everything in Free",
      "RAG code Q&A",
      "AI fix generation",
      "Semantic code search",
      "Bring your own API keys",
      "Works with OpenAI or Anthropic",
    ],
    cta: "Get Started",
    href: "/docs/cli",
    highlighted: true,
    note: "Free with your own API keys",
  },
]

export function Pricing() {
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
    <section ref={sectionRef} id="pricing" className="py-24 px-4 sm:px-6 lg:px-8 border-t border-border">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-12">
          <h2
            className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
          >
            <span className="font-serif italic text-muted-foreground">Start free,</span>{" "}
            <span className="text-gradient font-display font-semibold">scale when ready</span>
          </h2>
          <p className={`text-muted-foreground opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}>
            Full code analysis. No credit card. No sign-up.
          </p>
        </div>

        {/* Pricing cards */}
        <div className="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto">
          {tiers.map((tier, i) => {
            const Icon = tier.icon
            return (
              <div
                key={tier.name}
                className={cn(
                  "card-elevated rounded-xl p-6 flex flex-col opacity-0 relative",
                  tier.highlighted && "border-primary/50 ring-1 ring-primary/20",
                  isVisible ? "animate-scale-in" : "",
                )}
                style={{ animationDelay: `${250 + i * 100}ms` }}
              >
                {tier.highlighted && (
                  <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 bg-primary text-primary-foreground text-xs font-display font-medium rounded-full">
                    Most Value
                  </div>
                )}

                <div className="flex items-center gap-3 mb-4">
                  <div className={cn(
                    "w-10 h-10 rounded-lg flex items-center justify-center",
                    tier.highlighted ? "bg-primary/10" : "bg-muted"
                  )}>
                    <Icon className={cn(
                      "w-5 h-5",
                      tier.highlighted ? "text-primary" : "text-muted-foreground"
                    )} />
                  </div>
                  <div>
                    <h3 className="text-xl font-display font-semibold text-foreground">{tier.name}</h3>
                    <p className="text-sm text-muted-foreground">{tier.description}</p>
                  </div>
                </div>

                <div className="mb-6">
                  <span className="text-4xl font-display font-bold text-foreground">Free</span>
                  <p className="text-sm text-muted-foreground mt-1">Forever</p>
                  {tier.note && (
                    <p className="text-sm text-primary mt-2 font-medium">{tier.note}</p>
                  )}
                </div>

                <ul className="space-y-3 mb-6 flex-1">
                  {tier.features.map((feature) => (
                    <li key={feature} className="flex items-center gap-2.5 text-sm">
                      <Check className="w-4 h-4 text-primary shrink-0" />
                      <span className="text-muted-foreground">{feature}</span>
                    </li>
                  ))}
                </ul>

                <Button
                  asChild
                  className={cn(
                    "w-full h-10 font-display font-medium",
                    tier.highlighted
                      ? "bg-primary hover:bg-primary/90 text-primary-foreground"
                      : "bg-muted hover:bg-muted/80 text-foreground",
                  )}
                >
                  <Link href={tier.href}>{tier.cta}</Link>
                </Button>
              </div>
            )
          })}
        </div>

        {/* BYOK callout */}
        <div className={cn(
          "mt-12 text-center opacity-0",
          isVisible ? "animate-fade-up delay-500" : ""
        )}>
          <div className="inline-flex items-center gap-2 px-4 py-2 bg-muted/50 rounded-full text-sm text-muted-foreground">
            <Sparkles className="w-4 h-4" />
            <span>
              <strong>BYOK:</strong> Use Pro features free with your own OpenAI or Anthropic API keys
            </span>
          </div>
        </div>
      </div>
    </section>
  )
}
