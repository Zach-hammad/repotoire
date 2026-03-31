import { Metadata } from "next"
import { AnimatedSections } from "@/components/sections/animated-sections"
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
  return <AnimatedSections />
}
