"use client"

import { useState } from "react"
import { motion } from "framer-motion"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Check, ArrowRight, Terminal, Loader2 } from "lucide-react"
import Link from "next/link"
import { useSafeAuth } from "@/lib/use-safe-auth"
import { toast } from "sonner"

export function TeamsPricing() {
  const [loading, setLoading] = useState(false)
  const { isSignedIn } = useSafeAuth()
  const router = useRouter()

  const handleCheckout = async () => {
    if (!isSignedIn) {
      router.push("/sign-up?plan=team")
      return
    }

    setLoading(true)
    try {
      const res = await fetch("/api/v1/billing/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan: "team",
          seats: 1,
          success_url: `${window.location.origin}/dashboard?checkout=success`,
          cancel_url: `${window.location.origin}/teams?checkout=cancelled`,
        }),
      })

      if (!res.ok) {
        const error = await res.json()
        throw new Error(error.detail || "Failed to create checkout session")
      }

      const data = await res.json()
      window.location.href = data.checkout_url
    } catch (err) {
      console.error("Checkout error:", err)
      toast.error(err instanceof Error ? err.message : "Failed to start checkout")
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="py-20 px-4 sm:px-6 lg:px-8 bg-muted/30 border-t border-border/50">
      <div className="max-w-4xl mx-auto">
        {/* Section header */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-12"
        >
          <h2 className="text-3xl sm:text-4xl font-display font-bold text-foreground mb-4">
            Simple pricing
          </h2>
          <p className="text-lg text-muted-foreground">
            Free for individuals. Pay only for team features.
          </p>
        </motion.div>

        {/* Pricing cards */}
        <div className="grid md:grid-cols-2 gap-6">
          {/* Team Plan */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            className="relative p-8 rounded-2xl bg-background border-2 border-primary/50 shadow-lg"
          >
            <div className="absolute -top-3 left-1/2 -translate-x-1/2">
              <span className="px-4 py-1 rounded-full bg-primary text-primary-foreground text-xs font-medium">
                Most Popular
              </span>
            </div>

            <div className="mb-6">
              <h3 className="text-xl font-display font-semibold text-foreground">Team</h3>
              <p className="text-sm text-muted-foreground mt-1">For growing engineering teams</p>
            </div>

            <div className="mb-6">
              <span className="text-4xl font-display font-bold text-foreground">$15</span>
              <span className="text-muted-foreground">/dev/month</span>
              <p className="text-sm text-muted-foreground mt-1">Billed annually ($180/dev/year)</p>
            </div>

            <ul className="space-y-3 mb-8">
              {[
                "Unlimited repositories",
                "Team dashboard",
                "Code ownership analysis",
                "Bus factor alerts",
                "PR quality gates",
                "Slack/Teams integration",
                "90-day history",
              ].map((feature) => (
                <li key={feature} className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-primary flex-shrink-0 mt-0.5" />
                  <span className="text-sm text-muted-foreground">{feature}</span>
                </li>
              ))}
            </ul>

            <Button 
              className="w-full h-12 font-display bg-primary hover:bg-primary/90 text-primary-foreground"
              onClick={handleCheckout}
              disabled={loading}
            >
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Loading...
                </>
              ) : (
                <>
                  Start Free Trial
                  <ArrowRight className="w-4 h-4 ml-2" />
                </>
              )}
            </Button>
            <p className="text-xs text-center text-muted-foreground mt-3">
              7 days free · No credit card required
            </p>
          </motion.div>

          {/* Enterprise Plan */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
            className="p-8 rounded-2xl bg-background border border-border"
          >
            <div className="mb-6">
              <h3 className="text-xl font-display font-semibold text-foreground">Enterprise</h3>
              <p className="text-sm text-muted-foreground mt-1">For large organizations</p>
            </div>

            <div className="mb-6">
              <span className="text-4xl font-display font-bold text-foreground">Custom</span>
              <p className="text-sm text-muted-foreground mt-1">Let's talk</p>
            </div>

            <ul className="space-y-3 mb-8">
              {[
                "Everything in Team",
                "SSO/SAML authentication",
                "Audit logs",
                "Custom integrations",
                "Dedicated support",
                "SLA guarantee",
                "Unlimited history",
                "On-prem option",
              ].map((feature) => (
                <li key={feature} className="flex items-start gap-3">
                  <Check className="w-5 h-5 text-primary flex-shrink-0 mt-0.5" />
                  <span className="text-sm text-muted-foreground">{feature}</span>
                </li>
              ))}
            </ul>

            <Link href="/contact">
              <Button variant="outline" className="w-full h-12 font-display">
                Contact Sales
              </Button>
            </Link>
          </motion.div>
        </div>

        {/* CLI reminder */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="mt-12 p-6 rounded-xl bg-primary/5 border border-primary/20 flex flex-col sm:flex-row items-center justify-between gap-4"
        >
          <div className="flex items-center gap-4">
            <div className="p-2 rounded-lg bg-primary/10">
              <Terminal className="w-5 h-5 text-primary" />
            </div>
            <div>
              <h4 className="font-display font-medium text-foreground">Just need local analysis?</h4>
              <p className="text-sm text-muted-foreground">The CLI is free forever. No signup required.</p>
            </div>
          </div>
          <Link href="/cli">
            <Button variant="outline" className="border-primary/30 hover:bg-primary/5">
              Download CLI
            </Button>
          </Link>
        </motion.div>
      </div>
    </section>
  )
}
