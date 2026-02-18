"use client"

import Link from "next/link"
import { Check, Terminal, Heart, Building2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const plans = [
  {
    name: "CLI",
    icon: Terminal,
    price: "Free",
    description: "Everything you need",
    features: [
      "114 code detectors",
      "13 languages",
      "Graph-based architecture analysis",
      "SSA taint flow analysis",
      "AI-powered fixes (BYOK)",
      "SARIF, HTML, JSON, Markdown export",
      "GitHub Actions integration",
      "MCP server for AI assistants",
      "MIT license",
      "Forever free",
    ],
    cta: "Install Now",
    href: "https://github.com/Zach-hammad/repotoire",
    popular: true,
    highlight: "primary",
  },
  {
    name: "Sponsor",
    icon: Heart,
    price: "Your call",
    description: "Support development",
    features: [
      "Everything in CLI (it's all free)",
      "Fund new detectors & languages",
      "Priority issue responses",
      "Your name in SPONSORS.md",
      "Good karma",
    ],
    cta: "Sponsor on GitHub",
    href: "https://github.com/sponsors/Zach-hammad",
    popular: false,
    highlight: "primary",
  },
  {
    name: "Enterprise",
    icon: Building2,
    price: "Let's talk",
    description: "Custom integrations",
    features: [
      "On-prem deployment",
      "Custom detector development",
      "Priority support & SLA",
      "Team training",
      "CI/CD pipeline setup",
    ],
    cta: "Contact Us",
    href: "/contact",
    popular: false,
    highlight: "primary",
  },
]

export function PricingCards() {
  return (
    <div className="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
      {plans.map((plan) => (
        <div
          key={plan.name}
          className={cn(
            "relative flex flex-col rounded-2xl border bg-card p-8 shadow-sm transition-all duration-300 hover:shadow-lg",
            plan.popular
              ? "border-primary shadow-primary/10 scale-[1.02]"
              : "border-border hover:border-primary/50"
          )}
        >
          {plan.popular && (
            <div className="absolute -top-3 left-1/2 -translate-x-1/2">
              <span className="inline-block px-4 py-1 text-xs font-bold rounded-full bg-primary text-primary-foreground shadow-sm">
                Most Popular
              </span>
            </div>
          )}

          <div className="mb-6">
            <div className="flex items-center gap-3 mb-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10">
                <plan.icon className="h-5 w-5 text-primary" />
              </div>
              <h3 className="text-xl font-display font-bold text-foreground">
                {plan.name}
              </h3>
            </div>
            <p className="text-sm text-muted-foreground">{plan.description}</p>
          </div>

          <div className="mb-6">
            <span className="text-4xl font-display font-bold text-foreground">
              {plan.price}
            </span>
          </div>

          <ul className="mb-8 flex-1 space-y-3">
            {plan.features.map((feature) => (
              <li key={feature} className="flex items-start gap-3">
                <Check className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span className="text-sm text-muted-foreground">{feature}</span>
              </li>
            ))}
          </ul>

          <Link href={plan.href} target={plan.href.startsWith("http") ? "_blank" : undefined}>
            <Button
              className={cn(
                "w-full font-display",
                plan.popular
                  ? "bg-primary hover:bg-primary/90 text-primary-foreground"
                  : "bg-secondary hover:bg-secondary/80 text-secondary-foreground"
              )}
              size="lg"
            >
              {plan.cta}
            </Button>
          </Link>
        </div>
      ))}
    </div>
  )
}
