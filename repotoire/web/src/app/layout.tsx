import type React from "react"
import type { Metadata, Viewport } from "next"
import { Inter, Space_Grotesk, Geist_Mono } from "next/font/google"
import { ThemeProvider } from "next-themes"
import { ClerkProvider } from "@/components/providers/clerk-provider"
import { CookieConsent } from "@/components/cookie-consent"
import { OfflineIndicator } from "@/components/offline-indicator"
import { ServiceWorkerRegistration } from "@/components/service-worker-registration"
import { Toaster } from "sonner"
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
      <body className={`${inter.variable} ${spaceGrotesk.variable} ${geistMono.variable} font-sans antialiased`}>
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
