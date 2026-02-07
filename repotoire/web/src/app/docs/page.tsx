import Link from "next/link"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Rocket, Terminal, Server, Webhook, Book, ExternalLink } from "lucide-react"

export const metadata = {
  title: "Documentation | Repotoire",
  description: "Repotoire documentation - Learn how to use the graph-powered code health platform",
}

const sections = [
  {
    title: "Getting Started",
    description: "Set up Repotoire and analyze your first codebase in 5 minutes",
    href: "/docs/getting-started/quickstart",
    icon: Rocket,
  },
  {
    title: "CLI Reference",
    description: "Command-line interface for local development and CI/CD pipelines",
    href: "/docs/cli/overview",
    icon: Terminal,
  },
  {
    title: "REST API",
    description: "Programmatic access to all platform features via REST endpoints",
    href: "/docs/api/overview",
    icon: Server,
  },
  {
    title: "Webhooks",
    description: "Real-time notifications for analysis events and findings",
    href: "/docs/webhooks/overview",
    icon: Webhook,
  },
]

export default function DocsPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-4xl font-bold tracking-tight">Repotoire Documentation</h1>
        <p className="text-xl text-muted-foreground mt-4">
          Welcome to the Repotoire documentation. Repotoire is a graph-powered code health platform
          that analyzes codebases using Neo4j knowledge graphs to detect code smells, architectural
          issues, and technical debt.
        </p>
      </div>

      <div className="not-prose">
        <div className="grid gap-4 md:grid-cols-2">
          {sections.map((section) => {
            const Icon = section.icon
            return (
              <Link key={section.href} href={section.href}>
                <Card className="h-full transition-colors hover:bg-muted/50">
                  <CardHeader>
                    <div className="flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-primary/10">
                        <Icon className="h-5 w-5 text-primary" />
                      </div>
                      <CardTitle className="text-lg">{section.title}</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <CardDescription className="text-sm">
                      {section.description}
                    </CardDescription>
                  </CardContent>
                </Card>
              </Link>
            )
          })}
        </div>
      </div>

      <div className="border-t pt-8">
        <h2 className="text-2xl font-semibold mb-4">What is Repotoire?</h2>
        <p className="text-muted-foreground">
          Unlike traditional linters that examine files in isolation, Repotoire builds a Neo4j
          knowledge graph combining:
        </p>
        <ul className="mt-4 space-y-2 text-muted-foreground">
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">Structural Analysis</span> - AST parsing to understand code structure
          </li>
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">Semantic Understanding</span> - NLP and AI to understand code meaning
          </li>
          <li className="flex items-start gap-2">
            <span className="text-primary font-bold">Relational Patterns</span> - Graph algorithms to detect architectural issues
          </li>
        </ul>
      </div>

      <div className="border-t pt-8">
        <h2 className="text-2xl font-semibold mb-4">How to Use Repotoire</h2>

        <div className="space-y-6">
          <div>
            <h3 className="text-lg font-medium mb-2">CLI (Command Line)</h3>
            <p className="text-muted-foreground mb-3">
              Best for local development and CI/CD pipelines:
            </p>
            <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
              <code>{`# Install
pip install repotoire

# Analyze a codebase
repotoire ingest ./my-project
repotoire analyze ./my-project

# Ask questions with natural language
repotoire ask "Where is authentication handled?"`}</code>
            </pre>
          </div>

          <div>
            <h3 className="text-lg font-medium mb-2">REST API</h3>
            <p className="text-muted-foreground mb-3">
              Best for integrating with web applications and services:
            </p>
            <pre className="bg-muted p-4 rounded-lg overflow-x-auto text-sm">
              <code>{`curl -X POST https://api.repotoire.io/api/v1/analysis/trigger \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"repository_id": "550e8400-e29b-41d4-a716-446655440000"}'`}</code>
            </pre>
          </div>

          <div>
            <h3 className="text-lg font-medium mb-2">Web Dashboard</h3>
            <p className="text-muted-foreground">
              Use the <Link href="/dashboard" className="text-primary hover:underline">web dashboard</Link> for
              a visual interface with repository health dashboards, finding browser with code context,
              AI-powered fix suggestions, and team analytics.
            </p>
          </div>
        </div>
      </div>

      <div className="border-t pt-8">
        <h2 className="text-2xl font-semibold mb-4">Support</h2>
        <ul className="space-y-2 text-muted-foreground">
          <li>
            <a
              href="https://github.com/Zach-hammad/repotoire/issues"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-1"
            >
              GitHub Issues <ExternalLink className="h-3 w-3" />
            </a>
          </li>
          <li>
            <a
              href="mailto:support@repotoire.io"
              className="text-primary hover:underline"
            >
              support@repotoire.io
            </a>
          </li>
        </ul>
      </div>
    </div>
  )
}
