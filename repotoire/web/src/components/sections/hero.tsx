"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { motion, useReducedMotion } from "framer-motion"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Terminal, Users, Download, ArrowRight, Sparkles, Shield, Zap, GitBranch, LucideIcon } from "lucide-react"
import {
  EASING,
  DURATION,
  DELAY,
  OFFSET,
  SCALE,
} from "@/lib/animation-constants"

// Typing animation for terminal
function TypingText({ text, delay = 0 }: { text: string; delay?: number }) {
  const [displayed, setDisplayed] = useState("")
  const prefersReducedMotion = useReducedMotion()

  useEffect(() => {
    if (prefersReducedMotion) {
      setDisplayed(text)
      return
    }

    const timeout = setTimeout(() => {
      let i = 0
      const interval = setInterval(() => {
        if (i <= text.length) {
          setDisplayed(text.slice(0, i))
          i++
        } else {
          clearInterval(interval)
        }
      }, 50)
      return () => clearInterval(interval)
    }, delay * 1000)

    return () => clearTimeout(timeout)
  }, [text, delay, prefersReducedMotion])

  return (
    <span>
      {displayed}
      <motion.span
        animate={{ opacity: [1, 0] }}
        transition={{ duration: 0.5, repeat: Infinity }}
        className="text-primary"
      >
        _
      </motion.span>
    </span>
  )
}

// Feature pill component
function FeaturePill({ icon: Icon, text, delay }: { icon: LucideIcon; text: string; delay: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.3 }}
      className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-muted/50 border border-border/50 text-sm text-muted-foreground"
    >
      <Icon className="w-3.5 h-3.5 text-primary" />
      <span>{text}</span>
    </motion.div>
  )
}

// CLI Card Component
function CLICard() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.3, duration: 0.5, ease: EASING.smooth }}
      className="relative"
    >
      {/* Terminal window */}
      <div className="card-elevated rounded-xl overflow-hidden shadow-2xl border border-border/50">
        {/* Terminal header */}
        <div className="flex items-center gap-2 px-4 py-3 bg-muted/50 border-b border-border">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-red-500/80" />
            <span className="w-3 h-3 rounded-full bg-amber-500/80" />
            <span className="w-3 h-3 rounded-full bg-emerald-500/80" />
          </div>
          <span className="text-xs text-muted-foreground font-mono ml-2">Terminal</span>
        </div>

        {/* Terminal content */}
        <div className="p-4 font-mono text-sm space-y-2 bg-background/50">
          <div className="flex items-center gap-2 text-muted-foreground">
            <span className="text-emerald-500">$</span>
            <TypingText text="pip install repotoire" delay={0.5} />
          </div>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 2 }}
            className="text-muted-foreground/70 text-xs"
          >
            Successfully installed repotoire-0.1.32
          </motion.div>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 2.5 }}
            className="flex items-center gap-2 text-muted-foreground pt-2"
          >
            <span className="text-emerald-500">$</span>
            <span>repotoire analyze .</span>
          </motion.div>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 3 }}
            className="pt-2 space-y-1"
          >
            <div className="text-emerald-500">✓ Found 3 circular dependencies</div>
            <div className="text-amber-500">✓ Found 12 dead exports</div>
            <div className="text-blue-500">✓ Found 5 god classes</div>
            <div className="text-muted-foreground pt-1">Health Score: <span className="text-foreground font-bold">87/100</span></div>
          </motion.div>
        </div>
      </div>

      {/* Floating badge */}
      <motion.div
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 1, duration: 0.3 }}
        className="absolute -top-3 -right-3 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-500 text-xs font-medium"
      >
        100% Local
      </motion.div>
    </motion.div>
  )
}

// Teams Card Component
function TeamsCard() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.4, duration: 0.5, ease: EASING.smooth }}
      className="relative"
    >
      {/* Dashboard preview */}
      <div className="card-elevated rounded-xl overflow-hidden shadow-2xl border border-border/50">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 bg-muted/50 border-b border-border">
          <span className="text-sm font-medium text-foreground">Team Dashboard</span>
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-xs text-muted-foreground">Live</span>
          </div>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4 bg-background/50">
          {/* Bus Factor */}
          <motion.div
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.6 }}
            className="p-3 rounded-lg bg-muted/30 border border-border/50"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-muted-foreground">Bus Factor</span>
              <span className="text-xs text-amber-500 font-medium">⚠️ At Risk</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex -space-x-2">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="w-6 h-6 rounded-full bg-primary/20 border-2 border-background flex items-center justify-center text-[10px] text-primary font-medium">
                    {String.fromCharCode(64 + i)}
                  </div>
                ))}
              </div>
              <span className="text-sm text-foreground font-medium">3 critical owners</span>
            </div>
          </motion.div>

          {/* Ownership Graph Preview */}
          <motion.div
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.8 }}
            className="p-3 rounded-lg bg-muted/30 border border-border/50"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-muted-foreground">Code Ownership</span>
              <span className="text-xs text-primary">View Graph →</span>
            </div>
            <div className="flex gap-1 h-8">
              {[40, 25, 20, 10, 5].map((width, i) => (
                <motion.div
                  key={i}
                  initial={{ width: 0 }}
                  animate={{ width: `${width}%` }}
                  transition={{ delay: 1 + i * 0.1, duration: 0.5 }}
                  className={cn(
                    "rounded h-full",
                    i === 0 && "bg-primary",
                    i === 1 && "bg-primary/70",
                    i === 2 && "bg-primary/50",
                    i === 3 && "bg-primary/30",
                    i === 4 && "bg-primary/20"
                  )}
                />
              ))}
            </div>
          </motion.div>

          {/* Team Stats */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 1.2 }}
            className="grid grid-cols-3 gap-2 text-center"
          >
            <div className="p-2 rounded-lg bg-muted/30">
              <div className="text-lg font-bold text-foreground">12</div>
              <div className="text-[10px] text-muted-foreground">Repos</div>
            </div>
            <div className="p-2 rounded-lg bg-muted/30">
              <div className="text-lg font-bold text-foreground">8</div>
              <div className="text-[10px] text-muted-foreground">Devs</div>
            </div>
            <div className="p-2 rounded-lg bg-muted/30">
              <div className="text-lg font-bold text-foreground">94</div>
              <div className="text-[10px] text-muted-foreground">Health</div>
            </div>
          </motion.div>
        </div>
      </div>

      {/* Floating badge */}
      <motion.div
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 1.2, duration: 0.3 }}
        className="absolute -top-3 -right-3 px-3 py-1 rounded-full bg-primary/10 border border-primary/30 text-primary text-xs font-medium"
      >
        Team Insights
      </motion.div>
    </motion.div>
  )
}

export function Hero() {
  const [isVisible, setIsVisible] = useState(false)
  const prefersReducedMotion = useReducedMotion()

  useEffect(() => {
    setIsVisible(true)
  }, [])

  return (
    <section
      id="main-content"
      className="relative min-h-screen pt-32 pb-20 px-4 sm:px-6 lg:px-8 overflow-hidden"
      aria-labelledby="hero-heading"
    >
      {/* Animated background */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 dot-grid opacity-50" />
        <motion.div
          className="absolute top-1/4 -left-32 w-96 h-96 rounded-full bg-primary/5 blur-3xl"
          animate={prefersReducedMotion ? {} : {
            scale: [1, 1.2, 1],
            opacity: [0.3, 0.5, 0.3],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute bottom-1/4 -right-32 w-96 h-96 rounded-full bg-primary/5 blur-3xl"
          animate={prefersReducedMotion ? {} : {
            scale: [1.2, 1, 1.2],
            opacity: [0.5, 0.3, 0.5],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      <div className="max-w-6xl mx-auto">
        {/* Main headline */}
        <div className="text-center mb-16">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="inline-flex items-center gap-2 px-4 py-1.5 mb-6 rounded-full bg-primary/10 border border-primary/20 text-sm text-primary"
          >
            <Sparkles className="w-4 h-4" />
            <span>Graph-powered code analysis</span>
          </motion.div>

          <motion.h1
            id="hero-heading"
            initial={{ opacity: 0, y: OFFSET.large }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: DURATION.slow, ease: EASING.smooth }}
            className="text-4xl sm:text-5xl lg:text-6xl tracking-tight text-foreground mb-6 leading-[1.1]"
          >
            <span className="font-display font-bold">Find what your linter</span>
            <br />
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: DELAY.secondary, duration: DURATION.medium }}
              className="font-serif italic text-muted-foreground"
            >
              can't see.
            </motion.span>
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: OFFSET.medium }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: DELAY.heroContent, duration: DURATION.medium }}
            className="text-lg text-muted-foreground mb-8 max-w-2xl mx-auto"
          >
            Repotoire builds a knowledge graph of your codebase—surfacing architectural debt,
            circular dependencies, and code smells that traditional tools miss.
          </motion.p>
        </div>

        {/* Split: CLI vs Teams */}
        <div className="grid lg:grid-cols-2 gap-8 lg:gap-12">
          {/* Left: For You (CLI) */}
          <motion.div
            initial={{ opacity: 0, x: -30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2, duration: 0.5 }}
            className="space-y-6"
          >
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                <Terminal className="w-5 h-5 text-emerald-500" />
              </div>
              <div>
                <h2 className="text-xl font-display font-semibold text-foreground">For You</h2>
                <p className="text-sm text-muted-foreground">Free CLI, runs locally</p>
              </div>
            </div>

            <CLICard />

            {/* Feature pills */}
            <div className="flex flex-wrap gap-2">
              <FeaturePill icon={Shield} text="Code stays local" delay={1.5} />
              <FeaturePill icon={Zap} text="42 detectors" delay={1.6} />
              <FeaturePill icon={Sparkles} text="AI fixes (BYOK)" delay={1.7} />
            </div>

            {/* CTA */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.8 }}
              className="pt-2"
            >
              <Link href="/cli">
                <Button
                  size="lg"
                  variant="outline"
                  className="group h-12 px-6 text-base font-display border-emerald-500/30 hover:border-emerald-500/50 hover:bg-emerald-500/5"
                >
                  <Download className="w-4 h-4 mr-2 text-emerald-500" />
                  <span>Download CLI</span>
                  <ArrowRight className="w-4 h-4 ml-2 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
                </Button>
              </Link>
              <p className="text-xs text-muted-foreground mt-3">
                <code className="px-1.5 py-0.5 rounded bg-muted text-foreground">pip install repotoire</code>
                {" "}— Free forever
              </p>
            </motion.div>
          </motion.div>

          {/* Right: For Teams (Cloud) */}
          <motion.div
            initial={{ opacity: 0, x: 30 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3, duration: 0.5 }}
            className="space-y-6"
          >
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 rounded-lg bg-primary/10 border border-primary/20">
                <Users className="w-5 h-5 text-primary" />
              </div>
              <div>
                <h2 className="text-xl font-display font-semibold text-foreground">For Teams</h2>
                <p className="text-sm text-muted-foreground">Cloud dashboard, team insights</p>
              </div>
            </div>

            <TeamsCard />

            {/* Feature pills */}
            <div className="flex flex-wrap gap-2">
              <FeaturePill icon={Users} text="Code ownership" delay={1.8} />
              <FeaturePill icon={GitBranch} text="Cross-repo insights" delay={1.9} />
              <FeaturePill icon={Shield} text="PR gates" delay={2.0} />
            </div>

            {/* CTA */}
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.9 }}
              className="pt-2"
            >
              <Link href="/sign-up">
                <Button
                  size="lg"
                  className="group h-12 px-6 text-base font-display bg-primary hover:bg-primary/90 text-primary-foreground shadow-lg hover:shadow-xl transition-shadow"
                >
                  <span>Start Free Trial</span>
                  <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
                </Button>
              </Link>
              <p className="text-xs text-muted-foreground mt-3">
                7 days free · No credit card required
              </p>
            </motion.div>
          </motion.div>
        </div>

        {/* Trust bar */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.5 }}
          className="mt-20 pt-12 border-t border-border/50"
        >
          <div className="flex flex-col sm:flex-row items-center justify-center gap-6 sm:gap-10 text-sm text-muted-foreground">
            <a
              href="https://github.com/repotoire/repotoire"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 hover:text-foreground transition-colors group"
            >
              <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              <span className="font-display font-medium">Open Source</span>
            </a>
            <span className="hidden sm:block w-px h-4 bg-border" />
            <span>Apache 2.0 License</span>
            <span className="hidden sm:block w-px h-4 bg-border" />
            <span>Python & Rust</span>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
