"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { Navbar } from "@/components/navbar"
import { Footer } from "@/components/sections/footer"
import {
  Book,
  Terminal,
  Server,
  Webhook,
  Rocket,
  FileText,
  ChevronRight,
  ChevronDown,
  Menu,
  X,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"

interface NavItem {
  title: string
  href: string
  icon?: React.ReactNode
  items?: NavItem[]
}

const navigation: NavItem[] = [
  {
    title: "Getting Started",
    href: "/docs/getting-started",
    icon: <Rocket className="h-4 w-4" />,
    items: [
      { title: "Quick Start", href: "/docs/getting-started/quickstart" },
    ],
  },
  {
    title: "CLI Reference",
    href: "/docs/cli",
    icon: <Terminal className="h-4 w-4" />,
    items: [
      { title: "Overview", href: "/docs/cli/overview" },
    ],
  },
  {
    title: "REST API",
    href: "/docs/api",
    icon: <Server className="h-4 w-4" />,
    items: [
      { title: "Overview", href: "/docs/api/overview" },
    ],
  },
  {
    title: "Webhooks",
    href: "/docs/webhooks",
    icon: <Webhook className="h-4 w-4" />,
    items: [
      { title: "Overview", href: "/docs/webhooks/overview" },
    ],
  },
]

function NavSection({ item, pathname }: { item: NavItem; pathname: string }) {
  const isActive = pathname.startsWith(item.href)
  const [isOpen, setIsOpen] = useState(isActive)

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
          isActive
            ? "bg-primary/10 text-primary"
            : "text-muted-foreground hover:bg-muted hover:text-foreground"
        )}
      >
        {item.icon}
        <span className="flex-1 text-left">{item.title}</span>
        {item.items && (
          isOpen ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )
        )}
      </button>
      {item.items && isOpen && (
        <div className="ml-4 space-y-1 border-l pl-4">
          {item.items.map((subItem) => (
            <Link
              key={subItem.href}
              href={subItem.href}
              className={cn(
                "block rounded-md px-3 py-1.5 text-sm transition-colors",
                pathname === subItem.href
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {subItem.title}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname()

  return (
    <aside className={cn("w-64 shrink-0", className)}>
      <ScrollArea className="h-[calc(100vh-4rem)] py-6 pr-4">
        <div className="space-y-4">
          <div className="px-3">
            <Link href="/docs" className="flex items-center gap-2 font-semibold">
              <Book className="h-5 w-5" />
              <span>Documentation</span>
            </Link>
          </div>
          <nav className="space-y-1">
            {navigation.map((item) => (
              <NavSection key={item.href} item={item} pathname={pathname} />
            ))}
          </nav>
        </div>
      </ScrollArea>
    </aside>
  )
}

export default function DocsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="min-h-screen flex flex-col bg-background">
      <Navbar />
      <div className="flex-1 container mx-auto px-4 pt-16">
        <div className="flex gap-8">
          {/* Mobile sidebar toggle */}
          <Button
            variant="ghost"
            size="icon"
            className="fixed bottom-4 right-4 z-50 md:hidden rounded-full shadow-lg bg-primary text-primary-foreground"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            {sidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>

          {/* Mobile sidebar */}
          {sidebarOpen && (
            <div className="fixed inset-0 z-40 md:hidden">
              <div
                className="fixed inset-0 bg-background/80 backdrop-blur-sm"
                onClick={() => setSidebarOpen(false)}
              />
              <div className="fixed left-0 top-16 bottom-0 w-64 bg-background border-r p-4">
                <Sidebar />
              </div>
            </div>
          )}

          {/* Desktop sidebar */}
          <Sidebar className="hidden md:block sticky top-16 h-[calc(100vh-4rem)]" />

          {/* Main content */}
          <main className="flex-1 min-w-0 py-8">
            <article className="prose prose-slate dark:prose-invert max-w-none">
              {children}
            </article>
          </main>
        </div>
      </div>
      <Footer />
    </div>
  )
}
