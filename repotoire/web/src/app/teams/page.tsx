import { Metadata } from "next"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Terminal, Users, ArrowRight, Github } from "lucide-react"

export const metadata: Metadata = {
  title: "Repotoire Teams - Coming Soon",
  description: "Team dashboard and GitHub integration for Repotoire. Coming soon.",
}

export default function TeamsPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      {/* Simple header */}
      <header className="border-b border-border">
        <div className="container mx-auto px-4 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <div className="w-8 h-8 bg-primary rounded-lg flex items-center justify-center">
              <span className="text-primary-foreground font-bold text-sm">R</span>
            </div>
            <span className="font-display font-semibold text-lg">Repotoire</span>
          </Link>
          <Link href="/">
            <Button variant="ghost" size="sm">
              ← Back to Home
            </Button>
          </Link>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 flex items-center justify-center px-4">
        <div className="max-w-2xl mx-auto text-center">
          {/* Icon */}
          <div className="w-20 h-20 bg-primary/10 rounded-2xl flex items-center justify-center mx-auto mb-8">
            <Users className="w-10 h-10 text-primary" />
          </div>

          {/* Heading */}
          <h1 className="text-4xl font-display font-bold mb-4">
            Teams Dashboard
            <span className="text-primary ml-2">Coming Soon</span>
          </h1>

          <p className="text-xl text-muted-foreground mb-8 max-w-lg mx-auto">
            Shared visibility, GitHub integration, and PR quality gates for engineering teams.
          </p>

          {/* CTA - Use CLI now */}
          <div className="bg-muted/50 rounded-xl p-8 mb-8">
            <div className="flex items-center justify-center gap-3 mb-4">
              <Terminal className="w-6 h-6 text-primary" />
              <h2 className="text-lg font-semibold">Get started with the CLI today</h2>
            </div>

            <div className="bg-background rounded-lg p-4 font-mono text-sm text-left mb-4 border">
              <div className="text-muted-foreground mb-2"># Install and analyze in seconds</div>
              <div className="text-foreground">cargo install repotoire</div>
              <div className="text-foreground">repotoire analyze .</div>
            </div>

            <p className="text-muted-foreground text-sm mb-4">
              Full code analysis with 81 detectors. No sign-up required.
            </p>

            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              <Link href="/docs/cli">
                <Button size="lg" className="w-full sm:w-auto">
                  <Terminal className="w-4 h-4 mr-2" />
                  CLI Documentation
                </Button>
              </Link>
              <a
                href="https://github.com/repotoire/repotoire"
                target="_blank"
                rel="noopener noreferrer"
              >
                <Button variant="outline" size="lg" className="w-full sm:w-auto">
                  <Github className="w-4 h-4 mr-2" />
                  View on GitHub
                </Button>
              </a>
            </div>
          </div>

          {/* What's coming */}
          <div className="text-left">
            <h3 className="font-semibold mb-4 text-center">What&apos;s coming in Teams:</h3>
            <div className="grid sm:grid-cols-2 gap-4">
              {[
                "Team dashboard with shared visibility",
                "GitHub App integration",
                "Automatic PR analysis",
                "Quality gates and blocking rules",
                "Code ownership tracking",
                "Bus factor alerts",
              ].map((feature) => (
                <div key={feature} className="flex items-center gap-2 text-muted-foreground">
                  <ArrowRight className="w-4 h-4 text-primary shrink-0" />
                  <span>{feature}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Notify me */}
          <div className="mt-12 pt-8 border-t border-border">
            <p className="text-muted-foreground mb-4">
              Want early access to Teams?
            </p>
            <a href="mailto:hello@repotoire.com?subject=Teams%20Early%20Access">
              <Button variant="outline">
                Request Early Access
              </Button>
            </a>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-border py-6">
        <div className="container mx-auto px-4 text-center text-sm text-muted-foreground">
          © 2026 Repotoire. Graph-powered code analysis.
        </div>
      </footer>
    </div>
  )
}
