"use client"

/**
 * Pricing Section - Clerk Billing Integration
 *
 * This component displays pricing information for the marketing page.
 * When Clerk Billing is fully configured, replace the static tiers
 * with Clerk's <PricingTable /> component.
 *
 * Migration Note (2026-01):
 * - Kept static pricing display for SEO and marketing
 * - CTA buttons now link to sign-up (Clerk handles checkout)
 * - Enable PricingTable when Clerk Billing is configured
 */

import { useState, useEffect, useRef } from "react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

// Clerk Billing PricingTable - uncomment when configured
// import { PricingTable } from '@clerk/nextjs';

const tiers = [
  {
    name: "Team",
    price: { monthly: "19", annual: "15" },
    description: "For engineering teams",
    features: ["Unlimited repos", "Team dashboard", "Code ownership", "Bus factor alerts", "PR quality gates", "90-day history"],
    cta: "Start 7-Day Free Trial",
    href: "/sign-up?plan=team",
    highlighted: true,
    trial: "7 days free, then $15/dev/mo (annual)",
    perDev: true,
  },
  {
    name: "Enterprise",
    price: { monthly: "custom", annual: "custom" },
    description: "For large organizations",
    features: ["Everything in Team", "SSO/SAML", "Audit logs", "Custom integrations", "SLA guarantee", "Dedicated support"],
    cta: "Contact Sales",
    href: "/contact",
    highlighted: false,
  },
]

export function Pricing() {
  const [annual, setAnnual] = useState(true)
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
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <h2
            className={`text-3xl sm:text-4xl tracking-tight text-foreground mb-4 opacity-0 ${isVisible ? "animate-fade-up" : ""}`}
          >
            <span className="font-serif italic text-muted-foreground">Simple</span>{" "}
            <span className="text-gradient font-display font-semibold">pricing</span>
          </h2>
          <p className={`text-muted-foreground mb-6 opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}>
            Try free for 7 days. Cancel anytime.
          </p>

          <div
            className={`inline-flex items-center gap-1 bg-muted rounded-full p-1 opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}
            role="radiogroup"
            aria-label="Billing frequency"
          >
            <button
              onClick={() => setAnnual(false)}
              className={cn(
                "px-4 py-2 rounded-full text-sm font-medium transition-colors",
                !annual ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
              role="radio"
              aria-checked={!annual}
              aria-label="Monthly billing"
            >
              Monthly
            </button>
            <button
              onClick={() => setAnnual(true)}
              className={cn(
                "px-4 py-2 rounded-full text-sm font-medium transition-colors",
                annual ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
              role="radio"
              aria-checked={annual}
              aria-label="Annual billing, 20% discount"
            >
              Annual <span className="text-primary ml-1" aria-hidden="true">-20%</span>
            </button>
          </div>
        </div>

        {/* Static pricing cards (for SEO) - Replace with <PricingTable /> when Clerk Billing is configured */}
        <div className="grid md:grid-cols-2 gap-6 max-w-3xl mx-auto">
          {tiers.map((tier, i) => (
            <div
              key={tier.name}
              className={cn(
                "card-elevated rounded-xl p-6 flex flex-col opacity-0",
                tier.highlighted && "border-primary/30",
                isVisible ? "animate-scale-in" : "",
              )}
              style={{ animationDelay: `${250 + i * 100}ms` }}
            >
              {tier.highlighted && (
                <div className="text-xs text-primary font-display font-medium mb-3 uppercase tracking-wider">
                  Most Popular
                </div>
              )}
              <div className="mb-5">
                <h3 className="text-xl font-display font-semibold text-foreground">{tier.name}</h3>
                <p className="text-sm text-muted-foreground mt-1">{tier.description}</p>
              </div>

              <div className="mb-6">
                <span className="text-4xl font-display font-bold text-foreground">
                  ${annual ? tier.price.annual : tier.price.monthly}
                </span>
                <span className="text-muted-foreground text-sm">/month</span>
                {tier.trial && (
                  <p className="text-sm text-primary mt-2 font-medium">{tier.trial}</p>
                )}
              </div>

              <ul className="space-y-3 mb-6 flex-1">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-center gap-2.5 text-sm">
                    <svg
                      className="w-4 h-4 text-primary shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth="2.5"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
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
          ))}
        </div>
      </div>
    </section>
  )
}
