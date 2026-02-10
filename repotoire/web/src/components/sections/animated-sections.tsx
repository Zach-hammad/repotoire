'use client'

import { AnimationBoundary } from "@/components/providers/animation-boundary"
import { Hero } from "./hero"
import { ProblemSolution } from "./problem-solution"
import { Features } from "./features"
import { HowItWorks } from "./how-it-works"
import { SocialProof } from "./social-proof"
import { FAQ } from "./faq"
import { FinalCTA } from "./final-cta"

/**
 * Client wrapper for all animated marketing sections
 * Provides error boundary protection for Framer Motion animations
 */
export function AnimatedSections() {
  return (
    <>
      <AnimationBoundary>
        <Hero />
      </AnimationBoundary>
      <AnimationBoundary>
        <ProblemSolution />
      </AnimationBoundary>
      <section id="features">
        <AnimationBoundary>
          <Features />
        </AnimationBoundary>
      </section>
      <section id="how-it-works">
        <AnimationBoundary>
          <HowItWorks />
        </AnimationBoundary>
      </section>
      <AnimationBoundary>
        <SocialProof />
      </AnimationBoundary>
      <AnimationBoundary>
        <FAQ />
      </AnimationBoundary>
      <AnimationBoundary>
        <FinalCTA />
      </AnimationBoundary>
    </>
  )
}
