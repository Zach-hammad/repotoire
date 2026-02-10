import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Contact Us - Repotoire",
  description: "Get in touch with the Repotoire team. Have questions about code health analysis or AI-powered fixes? We'd love to hear from you.",
  openGraph: {
    title: "Contact Us - Repotoire",
    description: "Get in touch with the Repotoire team. Have questions about code health analysis or AI-powered fixes? We'd love to hear from you.",
    type: "website",
  },
}

export default function ContactLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
