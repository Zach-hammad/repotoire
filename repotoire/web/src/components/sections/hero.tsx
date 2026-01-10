"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { motion, useMotionValue, useTransform, useReducedMotion } from "framer-motion"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import {
  EASING,
  DURATION,
  DELAY,
  OFFSET,
  SCALE,
  SEVERITY_COLORS
} from "@/lib/animation-constants"

// Animated progress bar component
function AnimatedProgress({
  value,
  color,
  delay = 0,
}: {
  value: number
  color: string
  delay?: number
}) {
  const prefersReducedMotion = useReducedMotion()

  return (
    <div
      className="h-1.5 bg-muted rounded-full overflow-hidden"
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <motion.div
        className={cn("h-full rounded-full", color)}
        initial={{ width: prefersReducedMotion ? `${value}%` : 0 }}
        animate={{ width: `${value}%` }}
        transition={{
          delay: prefersReducedMotion ? 0 : delay + DURATION.medium,
          duration: prefersReducedMotion ? 0 : DURATION.extended,
          ease: EASING.smooth
        }}
      />
    </div>
  )
}

// Animated counter component
function AnimatedCounter({ value, delay = 0 }: { value: number; delay?: number }) {
  const [count, setCount] = useState(0)
  const prefersReducedMotion = useReducedMotion()

  useEffect(() => {
    // Skip animation if user prefers reduced motion
    if (prefersReducedMotion) {
      setCount(value)
      return
    }

    const timeout = setTimeout(() => {
      const duration = DURATION.counter * 1000 // Convert to ms
      const steps = 30
      const increment = value / steps
      let current = 0
      const interval = setInterval(() => {
        current += increment
        if (current >= value) {
          setCount(value)
          clearInterval(interval)
        } else {
          setCount(Math.floor(current))
        }
      }, duration / steps)
      return () => clearInterval(interval)
    }, delay * 1000)
    return () => clearTimeout(timeout)
  }, [value, delay, prefersReducedMotion])

  return <>{count}</>
}

// Pulsing severity dot
function SeverityDot({ color, pulse = false }: { color: string; pulse?: boolean }) {
  return (
    <span className="relative flex h-2 w-2">
      {pulse && (
        <span
          className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", color)}
        />
      )}
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", color)} />
    </span>
  )
}

// Issue row with hover animation
function IssueRow({
  severity,
  text,
  action,
  delay,
  pulse = false,
}: {
  severity: string
  text: string
  action: string
  delay: number
  pulse?: boolean
}) {
  const severityColors: Record<string, string> = {
    critical: "bg-red-500",
    high: "bg-orange-500",
    medium: "bg-amber-500",
    low: "bg-blue-500",
  }

  const severityLabels: Record<string, string> = {
    critical: "Critical severity",
    high: "High severity",
    medium: "Medium severity",
    low: "Low severity",
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: OFFSET.medium }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay, duration: DURATION.normal, ease: EASING.smooth }}
      whileHover={{ x: 4, backgroundColor: "var(--muted)" }}
      className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-muted/50 transition-colors cursor-pointer group"
      role="listitem"
      aria-label={`${severityLabels[severity]}: ${text}. Action: ${action}`}
    >
      <div className="flex items-center gap-3">
        <SeverityDot color={severityColors[severity]} pulse={pulse} />
        <span className="text-foreground">{text}</span>
      </div>
      <motion.span
        initial={{ opacity: 0, x: -10 }}
        whileHover={{ x: 0 }}
        className="text-primary text-xs font-display flex items-center gap-1"
        aria-hidden="true"
      >
        {action}
        <motion.span
          animate={{ x: [0, 4, 0] }}
          transition={{ repeat: Infinity, duration: 1.5, ease: "easeInOut" }}
        >
          →
        </motion.span>
      </motion.span>
    </motion.div>
  )
}

export function Hero() {
  const [isVisible, setIsVisible] = useState(false)
  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)
  const prefersReducedMotion = useReducedMotion()

  // Parallax effect for the product card (reduced for subtlety)
  const rotateX = useTransform(mouseY, [-300, 300], [3, -3])
  const rotateY = useTransform(mouseX, [-300, 300], [-3, 3])

  useEffect(() => {
    setIsVisible(true)
  }, [])

  const handleMouseMove = (e: React.MouseEvent) => {
    if (prefersReducedMotion) return
    const rect = e.currentTarget.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    mouseX.set(e.clientX - centerX)
    mouseY.set(e.clientY - centerY)
  }

  const handleMouseLeave = () => {
    mouseX.set(0)
    mouseY.set(0)
  }

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
          animate={{
            scale: [1, 1.2, 1],
            opacity: [0.3, 0.5, 0.3],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.div
          className="absolute bottom-1/4 -right-32 w-96 h-96 rounded-full bg-primary/5 blur-3xl"
          animate={{
            scale: [1.2, 1, 1.2],
            opacity: [0.5, 0.3, 0.5],
          }}
          transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
        />
      </div>

      <div className="max-w-6xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          {/* Left: Copy */}
          <div>
            <motion.h1
              id="hero-heading"
              initial={{ opacity: 0, y: OFFSET.large }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: DURATION.slow, ease: EASING.smooth }}
              className="text-5xl sm:text-6xl lg:text-7xl tracking-tight text-foreground mb-6 leading-[1.05]"
            >
              <span className="font-display font-bold text-gradient">See what your linter</span>
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
              className="text-lg text-muted-foreground mb-10 max-w-md leading-relaxed"
            >
              Repotoire builds a knowledge graph of your code—finding architectural debt, code smells, and issues that
              linters miss.
            </motion.p>

            <motion.div
              initial={{ opacity: 0, y: OFFSET.medium }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: DELAY.secondary, duration: DURATION.medium }}
              className="flex flex-col sm:flex-row gap-4 mb-8"
              role="group"
              aria-label="Get started actions"
            >
              <motion.div whileHover={{ scale: SCALE.hover }} whileTap={{ scale: SCALE.pressed }}>
                <Button
                  asChild
                  size="lg"
                  className="relative overflow-hidden bg-primary hover:bg-primary/90 text-primary-foreground h-12 px-6 text-base font-display font-medium shadow-lg hover:shadow-xl transition-shadow"
                >
                  <Link href="/dashboard">
                    <span className="relative z-10">Analyze Your Repo</span>
                    <motion.span
                      className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent"
                      initial={{ x: "-100%" }}
                      whileHover={{ x: "100%" }}
                      transition={{ duration: 0.5 }}
                    />
                  </Link>
                </Button>
              </motion.div>
              <motion.div whileHover={{ scale: SCALE.hover }} whileTap={{ scale: SCALE.pressed }}>
                <Button
                  asChild
                  size="lg"
                  variant="outline"
                  className="h-12 px-6 text-base font-display border-border hover:bg-muted bg-transparent transition-all"
                >
                  <Link href="/samples">See Sample Report</Link>
                </Button>
              </motion.div>
            </motion.div>

            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.5, duration: 0.5 }}
              className="flex items-center gap-6"
            >
              <motion.a
                href="https://github.com/repotoire/repotoire"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors group"
                whileHover={{ x: 2 }}
              >
                <svg className="w-5 h-5 transition-transform group-hover:scale-110" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                <span className="font-display font-medium">Open Source</span>
              </motion.a>
              <span className="w-px h-4 bg-border" />
              <span className="text-sm text-muted-foreground">Apache 2.0 License</span>
            </motion.div>
          </div>

          {/* Right: Product card with 3D effect */}
          <motion.div
            initial={{ opacity: 0, x: 50, rotateY: -10 }}
            animate={{ opacity: 1, x: 0, rotateY: 0 }}
            transition={{ delay: DELAY.secondary, duration: 0.7, ease: EASING.smooth }}
            aria-label="Interactive demo showing Repotoire code analysis results"
            style={{
              perspective: 1000,
            }}
            onMouseMove={handleMouseMove}
            onMouseLeave={handleMouseLeave}
          >
            <motion.div
              style={{
                rotateX,
                rotateY,
                transformStyle: "preserve-3d",
              }}
              transition={{ type: "spring", stiffness: 100, damping: 30 }}
              className="card-elevated rounded-xl overflow-hidden shadow-2xl"
            >
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-gradient-to-r from-muted/50 to-transparent">
                <div className="flex items-center gap-3">
                  <div className="flex gap-1.5">
                    <motion.span
                      className="w-3 h-3 rounded-full bg-red-500/80"
                      whileHover={{ scale: 1.2 }}
                    />
                    <motion.span
                      className="w-3 h-3 rounded-full bg-amber-500/80"
                      whileHover={{ scale: 1.2 }}
                    />
                    <motion.span
                      className="w-3 h-3 rounded-full bg-emerald-500/80"
                      whileHover={{ scale: 1.2 }}
                    />
                  </div>
                  <span className="text-sm font-mono text-muted-foreground">myapp/</span>
                </div>
                <motion.span
                  className="text-xs text-muted-foreground"
                  animate={{ opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 2, repeat: Infinity }}
                >
                  analyzing...
                </motion.span>
              </div>

              {/* Health Score */}
              <div className="p-5 border-b border-border">
                <div className="flex items-baseline justify-between mb-4">
                  <span className="text-sm text-muted-foreground font-display">Health Score</span>
                  <motion.span
                    className="text-4xl font-display font-bold text-foreground"
                    initial={{ opacity: 0, scale: 0.5 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ delay: 0.6, duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
                  >
                    <AnimatedCounter value={72} delay={0.6} />
                  </motion.span>
                </div>
                <div className="grid grid-cols-3 gap-4 text-xs">
                  <div>
                    <div className="text-muted-foreground mb-1.5">Structure</div>
                    <AnimatedProgress value={85} color="bg-emerald-500" delay={0.2} />
                    <motion.div
                      className="text-muted-foreground mt-1"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 1.2 }}
                    >
                      <AnimatedCounter value={85} delay={0.8} />%
                    </motion.div>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1.5">Quality</div>
                    <AnimatedProgress value={68} color="bg-amber-500" delay={0.3} />
                    <motion.div
                      className="text-muted-foreground mt-1"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 1.3 }}
                    >
                      <AnimatedCounter value={68} delay={0.9} />%
                    </motion.div>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1.5">Architecture</div>
                    <AnimatedProgress value={52} color="bg-red-500" delay={0.4} />
                    <motion.div
                      className="text-muted-foreground mt-1"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: 1.4 }}
                    >
                      <AnimatedCounter value={52} delay={1.0} />%
                    </motion.div>
                  </div>
                </div>
              </div>

              {/* Issues */}
              <div className="p-5 space-y-2 font-mono text-sm" role="list" aria-label="Code issues detected">
                <IssueRow
                  severity="critical"
                  text="3 circular dependencies"
                  action="Fix"
                  delay={1.0}
                  pulse
                />
                <IssueRow
                  severity="medium"
                  text="847 dead exports"
                  action="Fix"
                  delay={1.1}
                />
                <IssueRow
                  severity="low"
                  text="12 bottleneck modules"
                  action="View"
                  delay={1.2}
                />
              </div>

              {/* Footer */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 1.4 }}
                className="px-5 py-3 border-t border-border flex items-center justify-between bg-muted/30"
              >
                <div className="flex items-center gap-2">
                  {["Ruff", "Pylint", "Mypy", "Bandit", "Semgrep"].map((tool, i) => (
                    <motion.span
                      key={tool}
                      initial={{ opacity: 0, y: 10 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: 1.5 + i * 0.05 }}
                      className="text-xs text-muted-foreground"
                    >
                      {tool}{i < 4 && " ·"}
                    </motion.span>
                  ))}
                </div>
                <motion.div whileHover={{ scale: 1.05 }} whileTap={{ scale: SCALE.pressed }}>
                  <Button
                    size="sm"
                    className="h-7 text-xs font-display bg-primary hover:bg-primary/90 text-primary-foreground shadow-md"
                    aria-label="Apply automatic AI-powered code fixes"
                  >
                    <motion.span
                      className="mr-1"
                      animate={{ rotate: [0, 15, -15, 0] }}
                      transition={{ duration: DURATION.medium, repeat: Infinity, repeatDelay: 3 }}
                      aria-hidden="true"
                    >
                      ✨
                    </motion.span>
                    Apply AI Fix
                  </Button>
                </motion.div>
              </motion.div>
            </motion.div>
          </motion.div>
        </div>
      </div>
    </section>
  )
}
