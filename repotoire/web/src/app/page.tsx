import { Metadata } from "next"
import { Navbar } from "@/components/navbar"
import { Hero } from "@/components/sections/hero"
import { ProblemSolution } from "@/components/sections/problem-solution"
import { Features } from "@/components/sections/features"
import { HowItWorks } from "@/components/sections/how-it-works"
import { SocialProof } from "@/components/sections/social-proof"
import { FAQ } from "@/components/sections/faq"
import { FinalCTA } from "@/components/sections/final-cta"
import { Footer } from "@/components/sections/footer"

export const metadata: Metadata = {
  title: "Repotoire - Graph-Powered Code Analysis",
  description:
    "Find architectural issues, circular dependencies, and code smells that linters miss. Graph-powered analysis with AI-powered fixes.",
  openGraph: {
    title: "Repotoire - Graph-Powered Code Analysis",
    description: "Find what your linter can't see. Graph-powered code health analysis.",
    images: ["/og-image.png"],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Repotoire - Graph-Powered Code Analysis",
    description: "Find what your linter can't see.",
    images: ["/og-image.png"],
  },
}

export default function LandingPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <main className="flex-1">
        <Hero />
        <ProblemSolution />
        <Features />
        <HowItWorks />
        <SocialProof />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  )
}
