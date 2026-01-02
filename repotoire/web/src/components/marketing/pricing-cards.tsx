"use client"

import { useState } from "react"
import Link from "next/link"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const plans = [
  {
    name: "Pro",
    price: { monthly: "33", annual: "26" },
    description: "For professional developers",
    features: [
      "5 repositories per seat",
      "Unlimited analyses",
      "AI-powered auto-fix",
      "Best-of-N sampling",
      "Private repositories",
      "Priority support",
    ],
    cta: "Start 7-Day Free Trial",
    href: "/sign-up?plan=pro",
    popular: true,
    trial: "7 days free, then $33/mo",
  },
  {
    name: "Enterprise",
    price: { monthly: "199", annual: "159" },
    description: "For organizations",
    features: [
      "Unlimited repositories",
      "Everything in Pro",
      "SSO/SAML authentication",
      "Custom quality rules",
      "SLA guarantee",
      "Dedicated support",
      "Best-of-N unlimited",
    ],
    cta: "Contact Sales",
    href: "/contact",
    popular: false,
  },
]

export function PricingCards() {
  const [annual, setAnnual] = useState(true)

  return (
    <div>
      {/* Billing toggle */}
      <div className="flex justify-center mb-12">
        <div className="inline-flex items-center gap-1 bg-muted border border-border rounded-full p-1">
          <button
            onClick={() => setAnnual(false)}
            className={cn(
              "px-5 py-2 rounded-full text-sm font-medium transition-all duration-300",
              !annual ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
          >
            Monthly
          </button>
          <button
            onClick={() => setAnnual(true)}
            className={cn(
              "px-5 py-2 rounded-full text-sm font-medium transition-all duration-300",
              annual ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
          >
            Annual <span className="text-primary ml-1">-20%</span>
          </button>
        </div>
      </div>

      {/* Pricing cards */}
      <div className="grid gap-6 md:grid-cols-2 max-w-3xl mx-auto">
        {plans.map((plan, index) => (
          <div
            key={plan.name}
            className={cn(
              "relative flex flex-col card-elevated rounded-xl p-6 transition-all duration-300 hover:border-primary/30",
              plan.popular && "border-primary/50 md:scale-105 shadow-xl shadow-primary/10"
            )}
            style={{ animationDelay: `${index * 100}ms` }}
          >
            {plan.popular && (
              <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                <span className="bg-brand-gradient text-white text-xs font-display font-medium px-4 py-1 rounded-full">
                  Most Popular
                </span>
              </div>
            )}

            <div className="mb-6">
              <h3 className="text-xl font-display font-semibold text-foreground">{plan.name}</h3>
              <p className="text-sm text-muted-foreground mt-1">{plan.description}</p>
            </div>

            <div className="mb-6">
              {plan.price.monthly !== "Custom" ? (
                <>
                  <span className="text-5xl font-display font-bold text-foreground">
                    ${annual ? plan.price.annual : plan.price.monthly}
                  </span>
                  <span className="text-muted-foreground ml-1">/month</span>
                  {annual && (
                    <p className="text-sm text-muted-foreground mt-2">
                      Billed annually (${parseInt(plan.price.annual) * 12}/year)
                    </p>
                  )}
                  {plan.trial && (
                    <p className="text-sm text-primary mt-2 font-medium">{plan.trial}</p>
                  )}
                </>
              ) : (
                <span className="text-5xl font-display font-bold text-foreground">Custom</span>
              )}
            </div>

            <ul className="space-y-3 flex-1 mb-6">
              {plan.features.map((feature) => (
                <li key={feature} className="flex items-start gap-3">
                  <Check className="h-5 w-5 text-primary flex-shrink-0 mt-0.5" />
                  <span className="text-sm text-muted-foreground">{feature}</span>
                </li>
              ))}
            </ul>

            <Link href={plan.href} className="w-full">
              <Button
                className={cn(
                  "w-full font-display transition-all duration-300",
                  plan.popular
                    ? "bg-brand-gradient hover:opacity-90 text-white border-0"
                    : "hover:border-primary/50"
                )}
                variant={plan.popular ? "default" : "outline"}
                size="lg"
              >
                {plan.cta}
              </Button>
            </Link>
          </div>
        ))}
      </div>

      {/* Trust note */}
      <p className="mt-10 text-center text-sm text-muted-foreground">
        Try free for 7 days. Cancel anytime. No commitment.
      </p>
    </div>
  )
}
