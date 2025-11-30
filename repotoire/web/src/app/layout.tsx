import type React from "react"
import type { Metadata, Viewport } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import { ThemeProvider } from "next-themes"
import "./globals.css"

const geist = Geist({ subsets: ["latin"], variable: "--font-geist-sans" })
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" })

// Updated metadata for Repotoire SEO
export const metadata: Metadata = {
  title: "Repotoire — Graph-Powered Code Intelligence",
  description:
    "Go beyond traditional linters with graph-powered code intelligence. Detect architectural issues, get AI-powered auto-fixes, and analyze 10-100x faster.",
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
      "Go beyond traditional linters with graph-powered code intelligence. Detect architectural issues, get AI-powered auto-fixes, and analyze 10-100x faster.",
    type: "website",
  },
}

export const viewport: Viewport = {
  themeColor: "#0d0d14",
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
      <body className={`${geist.variable} ${geistMono.variable} font-sans antialiased`}>
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  )
}
