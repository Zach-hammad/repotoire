import { Metadata } from "next"
import { AnimatedSections } from "@/components/sections/animated-sections"

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
  return <AnimatedSections />
}
