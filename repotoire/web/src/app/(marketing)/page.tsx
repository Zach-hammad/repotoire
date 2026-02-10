import { Metadata } from "next"
import { AnimatedSections } from "@/components/sections/animated-sections"

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
  return <AnimatedSections />
}
