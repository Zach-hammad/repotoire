"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Check, Terminal, Users, Building2, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useSafeAuth } from "@/lib/use-safe-auth"
import { toast } from "sonner"

const plans = [
  {
    name: "CLI",
    icon: Terminal,
    price: { monthly: "0", annual: "0" },
    description: "For individual developers",
    features: [
      "Unlimited local analysis",
      "42 code detectors",
      "AI-powered fixes (BYOK)",
      "Graph-based insights",
      "Python, JS, TS, Rust, Go",
      "Apache 2.0 license",
    ],
    cta: "Download Free",
    href: "/cli",
    popular: false,
    highlight: "emerald",
    note: "Free forever",
  },
  {
    name: "Team",
    icon: Users,
    price: { monthly: "19", annual: "15" },
    description: "For engineering teams",
    features: [
      "Everything in CLI",
      "Team dashboard",
      "Code ownership analysis",
      "Bus factor alerts",
      "PR quality gates",
      "Slack/Teams integration",
      "90-day history",
      "Unlimited repos",
    ],
    cta: "Start Free Trial",
    href: "/sign-up?plan=team",
    popular: true,
    highlight: "primary",
    trial: "7 days free",
  },
  {
    name: "Enterprise",
    icon: Building2,
    price: { monthly: "Custom", annual: "Custom" },
    description: "For large organizations",
    features: [
      "Everything in Team",
      "SSO/SAML authentication",
      "Audit logs",
      "Custom integrations",
      "Dedicated support",
      "SLA guarantee",
      "Unlimited history",
      "On-prem option",
    ],
    cta: "Contact Sales",
    href: "/contact",
    popular: false,
    highlight: "primary",
  },
]

export function PricingCards() {
  const [annual, setAnnual] = useState(true)
  const [loading, setLoading] = useState<string | null>(null)
  const { isSignedIn } = useSafeAuth()
  const router = useRouter()

  const handleCheckout = async (plan: string, seats: number = 1) => {
    if (!isSignedIn) {
      // Redirect to sign-up with plan intent
      router.push(`/sign-up?plan=${plan}`)
      return
    }

    setLoading(plan)
    try {
      const res = await fetch("/api/v1/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan,
          seats,
          success_url: `${window.location.origin}/dashboard?checkout=success`,
          cancel_url: `${window.location.origin}/pricing?checkout=cancelled`,
        }),
      })

      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.detail || "Failed to create checkout session")
      }

      const data = await res.json()
      // Redirect to Stripe Checkout
      window.location.href = data.checkout_url
    } catch (err) {
      console.error("Checkout error:", err)
      toast.error(err instanceof Error ? err.message : "Failed to start checkout")
    } finally {
      setLoading(null)
    }
  }

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
      <div className="grid gap-6 lg:grid-cols-3 max-w-5xl mx-auto">
        {plans.map((plan, index) => (
          <div
            key={plan.name}
            className={cn(
              "relative flex flex-col card-elevated rounded-xl p-6 transition-all duration-300",
              plan.popular 
                ? "border-primary/50 lg:scale-105 shadow-xl shadow-primary/10 z-10" 
                : plan.highlight === "emerald"
                  ? "hover:border-emerald-500/30"
                  : "hover:border-primary/30"
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
              <div className="flex items-center gap-3 mb-2">
                <div className={cn(
                  "p-2 rounded-lg",
                  plan.highlight === "emerald" ? "bg-emerald-500/10" : "bg-primary/10"
                )}>
                  <plan.icon className={cn(
                    "w-5 h-5",
                    plan.highlight === "emerald" ? "text-emerald-500" : "text-primary"
                  )} />
                </div>
                <h3 className="text-xl font-display font-semibold text-foreground">{plan.name}</h3>
              </div>
              <p className="text-sm text-muted-foreground">{plan.description}</p>
            </div>

            <div className="mb-6">
              {plan.price.monthly !== "Custom" ? (
                <>
                  <span className="text-5xl font-display font-bold text-foreground">
                    ${annual ? plan.price.annual : plan.price.monthly}
                  </span>
                  {plan.price.monthly !== "0" && (
                    <span className="text-muted-foreground ml-1">/dev/month</span>
                  )}
                  {annual && plan.price.monthly !== "0" && (
                    <p className="text-sm text-muted-foreground mt-2">
                      Billed annually (${parseInt(plan.price.annual) * 12}/dev/year)
                    </p>
                  )}
                  {plan.trial && (
                    <p className="text-sm text-primary mt-2 font-medium">{plan.trial}</p>
                  )}
                  {plan.note && (
                    <p className={cn(
                      "text-sm mt-2 font-medium",
                      plan.highlight === "emerald" ? "text-emerald-500" : "text-primary"
                    )}>
                      {plan.note}
                    </p>
                  )}
                </>
              ) : (
                <span className="text-4xl font-display font-bold text-foreground">Custom</span>
              )}
            </div>

            <ul className="space-y-3 flex-1 mb-6">
              {plan.features.map((feature) => (
                <li key={feature} className="flex items-start gap-3">
                  <Check className={cn(
                    "h-5 w-5 flex-shrink-0 mt-0.5",
                    plan.highlight === "emerald" ? "text-emerald-500" : "text-primary"
                  )} />
                  <span className="text-sm text-muted-foreground">{feature}</span>
                </li>
              ))}
            </ul>

            {plan.name === "Team" ? (
              <Button
                className={cn(
                  "w-full font-display transition-all duration-300",
                  "bg-brand-gradient hover:opacity-90 text-white border-0"
                )}
                size="lg"
                onClick={() => handleCheckout("team")}
                disabled={loading === "team"}
              >
                {loading === "team" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Loading...
                  </>
                ) : (
                  plan.cta
                )}
              </Button>
            ) : plan.name === "Enterprise" ? (
              <Link href="/contact" className="w-full">
                <Button
                  className="w-full font-display transition-all duration-300 hover:border-primary/50"
                  variant="outline"
                  size="lg"
                >
                  {plan.cta}
                </Button>
              </Link>
            ) : (
              <Link href={plan.href} className="w-full">
                <Button
                  className={cn(
                    "w-full font-display transition-all duration-300",
                    "border-emerald-500/30 hover:bg-emerald-500/5 hover:border-emerald-500/50"
                  )}
                  variant="outline"
                  size="lg"
                >
                  {plan.cta}
                </Button>
              </Link>
            )}
          </div>
        ))}
      </div>

      {/* Trust note */}
      <p className="mt-10 text-center text-sm text-muted-foreground">
        CLI is free forever. Team plans include 7-day free trial. Cancel anytime.
      </p>
    </div>
  )
}
