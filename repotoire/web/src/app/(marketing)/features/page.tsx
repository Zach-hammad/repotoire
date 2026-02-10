import { Metadata } from "next"
import { redirect } from "next/navigation"

export const metadata: Metadata = {
  title: "Features - Repotoire",
  description: "Explore Repotoire's powerful features: graph-powered code analysis, AI-assisted fixes, real-time dashboards, and seamless CI/CD integration.",
  openGraph: {
    title: "Features - Repotoire",
    description: "Explore Repotoire's powerful features: graph-powered code analysis, AI-assisted fixes, real-time dashboards, and seamless CI/CD integration.",
    type: "website",
  },
}

export default function FeaturesPage() {
  redirect("/#features")
}
