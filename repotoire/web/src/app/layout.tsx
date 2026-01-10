import type React from "react"
import type { Metadata, Viewport } from "next"
import { Plus_Jakarta_Sans, Instrument_Serif, IBM_Plex_Mono } from "next/font/google"
import { ThemeProvider } from "next-themes"
import { ClerkProvider } from "@/components/providers/clerk-provider"
import { CookieConsent } from "@/components/cookie-consent"
import { Toaster } from "sonner"
import "./globals.css"

// Primary sans-serif: Geometric but warm, more character than Inter
const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  weight: ["400", "500", "600", "700", "800"],
})

// Display/headlines: Editorial serif for authority and sophistication
const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  variable: "--font-display",
  weight: "400",
  style: ["normal", "italic"],
})

// Monospace: Technical, precise, more character than system mono
const ibmPlexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  weight: ["400", "500", "600"],
})

export const metadata: Metadata = {
  title: "Repotoire — Graph-Powered Code Intelligence",
  description:
    "Go beyond traditional linters with graph-powered code intelligence. Detect architectural issues, get AI-powered auto-fixes, and fast incremental analysis.",
  generator: "v0.app",
  keywords: ["code analysis", "linter", "graph database", "AI", "technical debt", "code quality"],
  icons: {
    icon: "/logo.png",
    apple: "/logo.png",
  },
  openGraph: {
    title: "Repotoire — Graph-Powered Code Intelligence",
    description:
      "Go beyond traditional linters with graph-powered code intelligence. Detect architectural issues, get AI-powered auto-fixes, and fast incremental analysis.",
    type: "website",
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
      <body className={`${plusJakarta.variable} ${instrumentSerif.variable} ${ibmPlexMono.variable} font-sans antialiased`}>
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
            <Toaster richColors position="top-right" />
          </ClerkProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
