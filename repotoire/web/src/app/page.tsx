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
import { GRAPH_LANGUAGE_LABEL, TOTAL_DETECTOR_LABEL } from "@/lib/product-facts.generated"

export const metadata: Metadata = {
  title: "Repotoire — Graph-Powered Code Intelligence for Developers",
  description:
    `${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that linters miss. Single binary, ${GRAPH_LANGUAGE_LABEL}, graph-powered analysis.`,
  openGraph: {
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description: `${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that linters miss. Single binary, ${GRAPH_LANGUAGE_LABEL}.`,
    images: [{ url: "https://www.repotoire.com/og-image.png", width: 1200, height: 630 }],
    type: "website",
    url: "https://www.repotoire.com",
  },
  twitter: {
    card: "summary_large_image",
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description: `${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that linters miss.`,
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
