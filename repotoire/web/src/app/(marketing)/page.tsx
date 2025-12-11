import { Metadata } from "next"
import { Hero } from "@/components/sections/hero"
import { ProblemSolution } from "@/components/sections/problem-solution"
import { Features } from "@/components/sections/features"
import { HowItWorks } from "@/components/sections/how-it-works"
import { Differentiators } from "@/components/sections/differentiators"
import { SocialProof } from "@/components/sections/social-proof"
import { Pricing } from "@/components/sections/pricing"
import { FAQ } from "@/components/sections/faq"
import { FinalCTA } from "@/components/sections/final-cta"

export const metadata: Metadata = {
  title: "Repotoire - AI-Powered Code Health Platform",
  description:
    "Analyze code quality, detect issues, and fix them automatically with AI. Graph-powered analysis that catches problems before they become technical debt.",
  openGraph: {
    title: "Repotoire - AI-Powered Code Health Platform",
    description: "Ship healthier code, faster. AI-powered code health analysis.",
    images: ["/og-image.png"],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Repotoire - AI-Powered Code Health Platform",
    description: "Ship healthier code, faster.",
    images: ["/og-image.png"],
  },
}

export default function LandingPage() {
  return (
    <>
      <Hero />
      <ProblemSolution />
      <Features />
      <HowItWorks />
      <Differentiators />
      <SocialProof />
      <Pricing />
      <FAQ />
      <FinalCTA />
    </>
  )
}
