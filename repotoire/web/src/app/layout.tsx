import type React from "react"
import type { Metadata, Viewport } from "next"
import { Inter, Space_Grotesk, Geist_Mono } from "next/font/google"
import { ThemeProvider } from "next-themes"
import { ClerkProvider } from "@/components/providers/clerk-provider"
import { CookieConsent } from "@/components/cookie-consent"
import { OfflineIndicator } from "@/components/offline-indicator"
import { ServiceWorkerRegistration } from "@/components/service-worker-registration"
import { Toaster } from "sonner"
import { GRAPH_LANGUAGE_LABEL, TOTAL_DETECTOR_LABEL } from "@/lib/product-facts.generated"
import "./globals.css"

// Primary sans-serif: Clean, readable, professional
const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  weight: ["400", "500", "600", "700"],
})

// Display/headlines: Modern geometric sans
const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space",
  weight: ["400", "500", "600", "700"],
})

// Monospace: Technical, precise
const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "500", "600"],
})

export const metadata: Metadata = {
  title: "Repotoire — Graph-Powered Code Intelligence for Developers",
  description:
    `Go beyond traditional linters with graph-powered code intelligence. ${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that other tools miss. Fast incremental analysis.`,
  generator: "v0.app",
  keywords: ["code analysis", "linter", "graph database", "AI", "technical debt", "code quality"],
  icons: {
    icon: "/logo.png",
    apple: "/logo.png",
  },
  metadataBase: new URL("https://www.repotoire.com"),
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description:
      `${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that linters miss. Single binary, ${GRAPH_LANGUAGE_LABEL}, graph-powered analysis.`,
    type: "website",
    url: "https://www.repotoire.com",
    siteName: "Repotoire",
    images: [
      {
        url: "https://www.repotoire.com/og-image.png",
        width: 1200,
        height: 630,
        alt: `Repotoire — Graph-powered code analysis with ${TOTAL_DETECTOR_LABEL}`,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Repotoire — Graph-Powered Code Intelligence for Developers",
    description:
      `${TOTAL_DETECTOR_LABEL} find architectural issues, circular dependencies, and code smells that linters miss. Single binary, ${GRAPH_LANGUAGE_LABEL}.`,
    images: ["https://www.repotoire.com/og-image.png"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
    },
  },
}

export const viewport: Viewport = {
  themeColor: "#0a0a12",
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${spaceGrotesk.variable} ${geistMono.variable} font-sans antialiased`}>
        {/* repotoire:ignore[XssDetector] — JSON-LD structured data, no user input */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@graph": [
                {
                  "@type": "WebSite",
                  "@id": "https://www.repotoire.com/#website",
                  url: "https://www.repotoire.com",
                  name: "Repotoire",
                  description: `Graph-powered code intelligence with ${TOTAL_DETECTOR_LABEL}.`,
                },
                {
                  "@type": "Organization",
                  "@id": "https://www.repotoire.com/#organization",
                  name: "Repotoire",
                  url: "https://www.repotoire.com",
                  logo: {
                    "@type": "ImageObject",
                    url: "https://www.repotoire.com/logo.png",
                  },
                  description: `Graph-powered code health platform. ${TOTAL_DETECTOR_LABEL}, ${GRAPH_LANGUAGE_LABEL}, single binary.`,
                },
                {
                  "@type": "SoftwareApplication",
                  name: "Repotoire",
                  applicationCategory: "DeveloperApplication",
                  operatingSystem: "Linux, macOS, Windows",
                  url: "https://www.repotoire.com",
                  description: "Find architectural issues, circular dependencies, and code smells that linters miss. Graph-powered analysis with AI-powered fixes.",
                  offers: {
                    "@type": "Offer",
                    price: "0",
                    priceCurrency: "USD",
                  },
                },
              ],
            }),
          }}
        />
        {/* Skip link for keyboard navigation */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-[100] focus:px-4 focus:py-2 focus:bg-primary focus:text-primary-foreground focus:rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Skip to main content
        </a>
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          <ClerkProvider>
            {children}
            <CookieConsent />
            <OfflineIndicator />
            <ServiceWorkerRegistration />
            <Toaster richColors position="top-right" />
          </ClerkProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
