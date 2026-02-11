import { Metadata } from "next"
import { CLIHero } from "@/components/cli/cli-hero"
import { CLIFeatures } from "@/components/cli/cli-features"
import { CLIQuickStart } from "@/components/cli/cli-quickstart"
import { CLIFAQ } from "@/components/cli/cli-faq"
import { Footer } from "@/components/sections/footer"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "CLI - Repotoire",
  description: "Free, local code analysis. 81 detectors, AI-powered fixes, your code never leaves your machine.",
  openGraph: {
    title: "Repotoire CLI - Free Local Code Analysis",
    description: "cargo install repotoire. Analyze your code locally with 81 detectors and AI-powered fixes.",
    type: "website",
  },
}

export default function CLIPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <main className="flex-1">
        <CLIHero />
        <CLIFeatures />
        <CLIQuickStart />
        <CLIFAQ />
      </main>
      <Footer />
    </div>
  )
}
