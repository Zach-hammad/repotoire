import type React from "react"
import type { Metadata, Viewport } from "next"
import { Inter, Space_Grotesk, Crimson_Pro } from "next/font/google"
import { ThemeProvider } from "next-themes"
import { ClerkProvider } from "@/components/providers/clerk-provider"
import { CookieConsent } from "@/components/cookie-consent"
import { Toaster } from "sonner"
import "./globals.css"

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
})

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space",
})

const crimsonPro = Crimson_Pro({
  subsets: ["latin"],
  variable: "--font-crimson",
  style: ["normal", "italic"],
})

export const metadata: Metadata = {
  title: "Repotoire — Graph-Powered Code Intelligence",
  description:
    "Go beyond traditional linters with graph-powered code intelligence. Detect architectural issues, get AI-powered auto-fixes, and fast incremental analysis.",
  generator: "v0.app",
  keywords: ["code analysis", "linter", "graph database", "AI", "technical debt", "code quality"],
  icons: {
    icon: [
      {
        url: "/icon-light-32x32.png",
        media: "(prefers-color-scheme: light)",
      },
      {
        url: "/icon-dark-32x32.png",
        media: "(prefers-color-scheme: dark)",
      },
      {
        url: "/icon.svg",
        type: "image/svg+xml",
      },
    ],
    apple: "/apple-icon.png",
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
      <body className={`${inter.variable} ${spaceGrotesk.variable} ${crimsonPro.variable} font-sans antialiased`}>
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
