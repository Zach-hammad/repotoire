import { Metadata } from "next"

export const metadata: Metadata = {
  title: "About Repotoire - Our Mission & Values",
  description: "Learn about Repotoire's mission to revolutionize code health with graph-powered analysis and AI-assisted fixes. Developer-first, open source, quality focused.",
  openGraph: {
    title: "About Repotoire - Our Mission & Values",
    description: "Learn about Repotoire's mission to revolutionize code health with graph-powered analysis and AI-assisted fixes. Developer-first, open source, quality focused.",
    type: "website",
  },
}

export default function AboutLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
