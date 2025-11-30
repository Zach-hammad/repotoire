"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const tiers = [
  {
    name: "Free",
    price: { monthly: "0", annual: "0" },
    description: "For individual developers",
    features: ["1 repository", "All 8 detectors", "HTML reports", "CLI access"],
    cta: "Start Free",
    highlighted: false,
  },
  {
    name: "Team",
    price: { monthly: "99", annual: "79" },
    description: "For growing teams",
    features: ["Unlimited repos", "AI auto-fix", "GitHub Actions", "Natural language search", "Slack alerts"],
    cta: "Start 14-day Trial",
    highlighted: true,
  },
  {
    name: "Enterprise",
    price: { monthly: "Custom", annual: "Custom" },
    description: "For large organizations",
    features: ["Self-hosted option", "SSO/SAML", "Custom detectors", "Dedicated support"],
    cta: "Talk to Us",
    highlighted: false,
  },
]

export function Pricing() {
  const [annual, setAnnual] = useState(true)

  return (
    <section id="pricing" className="py-20 px-4 sm:px-6 lg:px-8">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl sm:text-4xl font-bold text-foreground mb-4">Pricing</h2>
          <p className="text-lg text-muted-foreground mb-6">Free forever for personal projects.</p>

          <div className="inline-flex items-center gap-1 bg-muted border border-border rounded-full p-1">
            <button
              onClick={() => setAnnual(false)}
              className={cn(
                "px-4 py-2 rounded-full text-sm font-medium transition-colors",
                !annual ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              Monthly
            </button>
            <button
              onClick={() => setAnnual(true)}
              className={cn(
                "px-4 py-2 rounded-full text-sm font-medium transition-colors",
                annual ? "bg-background text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              Annual <span className="text-emerald-400 ml-1">-20%</span>
            </button>
          </div>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {tiers.map((tier) => (
            <div
              key={tier.name}
              className={cn(
                "bg-card rounded-lg border p-6 flex flex-col",
                tier.highlighted ? "border-emerald-500 ring-1 ring-emerald-500" : "border-border",
              )}
            >
              {tier.highlighted && <div className="text-xs text-emerald-400 font-medium mb-2">Most Popular</div>}
              <div className="mb-4">
                <h3 className="text-xl font-semibold text-foreground">{tier.name}</h3>
                <p className="text-sm text-muted-foreground">{tier.description}</p>
              </div>

              <div className="mb-6">
                {tier.price.monthly !== "Custom" ? (
                  <>
                    <span className="text-4xl font-bold text-foreground">
                      ${annual ? tier.price.annual : tier.price.monthly}
                    </span>
                    <span className="text-muted-foreground">/dev/mo</span>
                  </>
                ) : (
                  <span className="text-4xl font-bold text-foreground">Custom</span>
                )}
              </div>

              <ul className="space-y-2 mb-6 flex-1">
                {tier.features.map((feature) => (
                  <li key={feature} className="flex items-center gap-2 text-sm">
                    <svg
                      className="w-4 h-4 text-emerald-500 shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                    <span className="text-muted-foreground">{feature}</span>
                  </li>
                ))}
              </ul>

              <Button
                className={cn("w-full", tier.highlighted ? "bg-emerald-500 hover:bg-emerald-600 text-white" : "")}
                variant={tier.highlighted ? "default" : "outline"}
              >
                {tier.cta}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
