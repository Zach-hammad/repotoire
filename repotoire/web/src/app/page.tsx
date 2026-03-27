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
  title: "Repotoire — Graph-Powered Code Intelligence for Developers",
  description:
    "106 pure Rust detectors find architectural issues, circular dependencies, and code smells that linters miss. Single binary, 9 languages, graph-powered analysis.",
  openGraph: {
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description: "106 pure Rust detectors find architectural issues, circular dependencies, and code smells that linters miss. Single binary, 9 languages.",
    images: [{ url: "https://www.repotoire.com/og-image.png", width: 1200, height: 630 }],
    type: "website",
    url: "https://www.repotoire.com",
  },
  twitter: {
    card: "summary_large_image",
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description: "106 pure Rust detectors find architectural issues, circular dependencies, and code smells that linters miss.",
    images: ["https://www.repotoire.com/og-image.png"],
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
