import { Metadata } from "next"
import { redirect } from "next/navigation"

export const metadata: Metadata = {
  title: "How It Works - Repotoire",
  description: "See how Repotoire analyzes your codebase in 3 simple steps: connect your repo, get graph-powered insights, and apply AI-suggested fixes.",
  openGraph: {
    title: "How It Works - Repotoire",
    description: "See how Repotoire analyzes your codebase in 3 simple steps: connect your repo, get graph-powered insights, and apply AI-suggested fixes.",
    type: "website",
  },
}

export default function HowItWorksPage() {
  redirect("/#how-it-works")
}
