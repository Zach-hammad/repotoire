"use client"

import { motion } from "framer-motion"
import { Button } from "@/components/ui/button"
import { Users, ArrowRight, BarChart3, Shield, GitPullRequest } from "lucide-react"
import Link from "next/link"

export function TeamsHero() {
  return (
    <section className="relative pt-32 pb-20 px-4 sm:px-6 lg:px-8 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 dot-grid opacity-50" />
        <div className="absolute top-1/4 -left-32 w-96 h-96 rounded-full bg-primary/5 blur-3xl" />
        <div className="absolute bottom-1/4 -right-32 w-96 h-96 rounded-full bg-primary/5 blur-3xl" />
      </div>

      <div className="max-w-6xl mx-auto">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex justify-center mb-8"
        >
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-primary/10 border border-primary/20 text-sm text-primary">
            <Users className="w-4 h-4" />
            <span>For Engineering Teams</span>
          </div>
        </motion.div>

        {/* Headline */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="text-center mb-12"
        >
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-display font-bold tracking-tight text-foreground mb-6">
            See how your team
            <br />
            <span className="text-primary">actually builds.</span>
          </h1>
          <p className="text-lg text-muted-foreground max-w-2xl mx-auto">
            Code ownership. Bus factor. Knowledge silos. Cross-repo patterns.
            <br />
            <span className="text-foreground font-medium">Insights you can't get from git log.</span>
          </p>
        </motion.div>

        {/* CTA */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16"
        >
          <Link href="/sign-up">
            <Button
              size="lg"
              className="group h-12 px-8 text-base font-display bg-primary hover:bg-primary/90 text-primary-foreground shadow-lg hover:shadow-xl transition-all"
            >
              <span>Start Free Trial</span>
              <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
            </Button>
          </Link>
          <Link href="/pricing">
            <Button
              size="lg"
              variant="outline"
              className="h-12 px-8 text-base font-display"
            >
              View Pricing
            </Button>
          </Link>
        </motion.div>

        {/* Dashboard Preview */}
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3, duration: 0.6 }}
          className="relative"
        >
          <div className="card-elevated rounded-2xl overflow-hidden shadow-2xl border border-border/50 max-w-4xl mx-auto">
            {/* Dashboard header */}
            <div className="flex items-center justify-between px-6 py-4 bg-muted/50 border-b border-border">
              <div className="flex items-center gap-3">
                <div className="flex gap-1.5">
                  <span className="w-3 h-3 rounded-full bg-red-500/80" />
                  <span className="w-3 h-3 rounded-full bg-amber-500/80" />
                  <span className="w-3 h-3 rounded-full bg-emerald-500/80" />
                </div>
                <span className="text-sm text-muted-foreground">Team Dashboard — Acme Corp</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-xs text-muted-foreground">12 repos connected</span>
              </div>
            </div>

            {/* Dashboard content */}
            <div className="p-6 bg-background/50">
              <div className="grid md:grid-cols-3 gap-6">
                {/* Bus Factor Card */}
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.5 }}
                  className="p-4 rounded-xl bg-amber-500/5 border border-amber-500/20"
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-foreground">Bus Factor</span>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-500">At Risk</span>
                  </div>
                  <div className="flex items-center gap-3 mb-3">
                    <div className="flex -space-x-2">
                      {["A", "S", "M"].map((initial, i) => (
                        <div key={i} className="w-8 h-8 rounded-full bg-primary/20 border-2 border-background flex items-center justify-center text-xs font-medium text-primary">
                          {initial}
                        </div>
                      ))}
                    </div>
                    <div className="text-sm text-muted-foreground">
                      3 critical owners
                    </div>
                  </div>
                  <div className="text-xs text-amber-500">
                    payment-service has only 1 maintainer
                  </div>
                </motion.div>

                {/* Ownership Card */}
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.6 }}
                  className="p-4 rounded-xl bg-muted/30 border border-border/50"
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-foreground">Code Ownership</span>
                    <BarChart3 className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <div className="space-y-2">
                    {[
                      { name: "Alice", pct: 42, color: "bg-primary" },
                      { name: "Sarah", pct: 28, color: "bg-primary/70" },
                      { name: "Mike", pct: 18, color: "bg-primary/50" },
                      { name: "Others", pct: 12, color: "bg-primary/30" },
                    ].map((person) => (
                      <div key={person.name} className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground w-12">{person.name}</span>
                        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                          <motion.div
                            initial={{ width: 0 }}
                            animate={{ width: `${person.pct}%` }}
                            transition={{ delay: 0.8, duration: 0.5 }}
                            className={`h-full ${person.color} rounded-full`}
                          />
                        </div>
                        <span className="text-xs text-muted-foreground w-8">{person.pct}%</span>
                      </div>
                    ))}
                  </div>
                </motion.div>

                {/* PR Gates Card */}
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.7 }}
                  className="p-4 rounded-xl bg-muted/30 border border-border/50"
                >
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium text-foreground">PR Quality Gate</span>
                    <GitPullRequest className="w-4 h-4 text-muted-foreground" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">PRs blocked</span>
                      <span className="text-foreground font-medium">3 today</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Issues prevented</span>
                      <span className="text-foreground font-medium">12</span>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">Avg review time</span>
                      <span className="text-emerald-500 font-medium">-23%</span>
                    </div>
                  </div>
                </motion.div>
              </div>
            </div>
          </div>

          {/* Floating gradient */}
          <div className="absolute -inset-4 bg-gradient-to-t from-background via-transparent to-transparent pointer-events-none" />
        </motion.div>

        {/* Trust bar */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1 }}
          className="mt-16 text-center"
        >
          <p className="text-sm text-muted-foreground">
            7-day free trial · No credit card required · Cancel anytime
          </p>
        </motion.div>
      </div>
    </section>
  )
}
