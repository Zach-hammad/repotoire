import { Metadata } from "next"
import { TeamsHero } from "@/components/teams/teams-hero"
import { TeamsFeatures } from "@/components/teams/teams-features"
import { TeamsPricing } from "@/components/teams/teams-pricing"
import { Footer } from "@/components/sections/footer"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Teams - Repotoire",
  description: "Team analytics for engineering organizations. Code ownership, bus factor analysis, and cross-repo insights.",
  openGraph: {
    title: "Repotoire Teams - Engineering Team Analytics",
    description: "See how your team builds. Code ownership, bus factor, collaboration graphs.",
    type: "website",
  },
}

export default function TeamsPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <main className="flex-1">
        <TeamsHero />
        <TeamsFeatures />
        <TeamsPricing />
      </main>
      <Footer />
    </div>
  )
}
