import { Metadata } from "next"
import Link from "next/link"

export const metadata: Metadata = {
  title: "Repotoire vs SonarQube: Which Code Analysis Tool Is Right for You?",
  description:
    "Compare Repotoire and SonarQube — graph-powered analysis vs rule-based scanning. Pricing, features, setup, and who each tool is best for.",
  alternates: {
    canonical: "/compare/repotoire-vs-sonarqube",
  },
  openGraph: {
    title: "Repotoire vs SonarQube: Which Code Analysis Tool Is Right for You?",
    description:
      "Compare Repotoire and SonarQube — graph-powered analysis vs rule-based scanning. Pricing, features, setup, and who each tool is best for.",
    url: "https://www.repotoire.com/compare/repotoire-vs-sonarqube",
    type: "website",
    siteName: "Repotoire",
  },
}

const comparisonData = [
  { feature: "Analysis approach", repotoire: "Graph-powered (petgraph)", sonarqube: "Rule-based AST scanning" },
  { feature: "Languages", repotoire: "9 (Python, TS/JS, Rust, Go, Java, C#, C, C++)", sonarqube: "30+" },
  { feature: "Rules / Detectors", repotoire: "106 pure Rust detectors", sonarqube: "5,000+ rules" },
  { feature: "Architectural analysis", repotoire: "Yes (circular deps, god classes, bottlenecks, coupling)", sonarqube: "Limited" },
  { feature: "Graph algorithms", repotoire: "PageRank, Louvain, SCC, betweenness centrality", sonarqube: "None" },
  { feature: "Setup", repotoire: "Single binary, no dependencies", sonarqube: "Java server + database required" },
  { feature: "CI/CD integration", repotoire: "GitHub Action, SARIF output", sonarqube: "SonarScanner + server, deep CI integration" },
  { feature: "Self-hosted option", repotoire: "Yes (single binary)", sonarqube: "Yes (Community Edition, requires Java + DB)" },
  { feature: "Cloud option", repotoire: "Coming soon", sonarqube: "SonarCloud" },
  { feature: "Security scanning", repotoire: "23 SSA-based taint detectors", sonarqube: "Extensive (SAST, secrets, hotspots)" },
  { feature: "Incremental analysis", repotoire: "Yes (content-hash cache, ~1.4s warm)", sonarqube: "Yes (server-side)" },
  { feature: "IDE plugins", repotoire: "VS Code (preview)", sonarqube: "SonarLint (VS Code, IntelliJ, Eclipse)" },
  { feature: "Pricing", repotoire: "Free CLI, Pro plans coming", sonarqube: "Community (free), Developer ($150/yr), Enterprise ($65K+/yr)" },
]

export default function RepotoireVsSonarQubePage() {
  return (
    <article className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <header className="text-center mb-16">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-6 font-display font-bold">
            Repotoire vs SonarQube
          </h1>
          <p className="text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
            SonarQube is the industry standard for rule-based code analysis with unmatched language breadth.
            Repotoire takes a different approach: graph-powered analysis that finds architectural issues
            traditional scanners miss. Here&apos;s how they compare.
          </p>
        </header>

        {/* Comparison Table */}
        <section className="mb-16">
          <h2 className="text-2xl font-display font-bold text-foreground mb-6">
            Feature Comparison
          </h2>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left p-4 font-display font-semibold text-foreground">Feature</th>
                  <th className="text-left p-4 font-display font-semibold text-primary">Repotoire</th>
                  <th className="text-left p-4 font-display font-semibold text-muted-foreground">SonarQube</th>
                </tr>
              </thead>
              <tbody>
                {comparisonData.map((row, i) => (
                  <tr key={row.feature} className={i % 2 === 0 ? "bg-muted/10" : ""}>
                    <td className="p-4 font-medium text-foreground">{row.feature}</td>
                    <td className="p-4 text-muted-foreground">{row.repotoire}</td>
                    <td className="p-4 text-muted-foreground">{row.sonarqube}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Architecture */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Architecture
          </h2>
          <div className="grid md:grid-cols-2 gap-6">
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-primary mb-3">Repotoire</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">
                Builds an in-memory knowledge graph of your codebase using petgraph and tree-sitter.
                Runs graph algorithms (PageRank, Louvain community detection, SCC, betweenness centrality)
                to surface architectural issues. Single binary, no server, no database. Detectors query
                the graph directly for O(1) lookups on pre-computed metrics.
              </p>
            </div>
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-muted-foreground mb-3">SonarQube</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">
                Client-server architecture. SonarScanner runs locally and sends results to a SonarQube
                server backed by a database (PostgreSQL, Oracle, or SQL Server). The server stores
                historical data, manages quality gates, and provides a web dashboard. Rules operate
                on ASTs per-file with some cross-file dataflow analysis in paid editions.
              </p>
            </div>
          </div>
        </section>

        {/* Detection Capabilities */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Detection Capabilities
          </h2>
          <div className="card-elevated rounded-xl p-6 space-y-4">
            <p className="text-muted-foreground leading-relaxed">
              <strong className="text-foreground">SonarQube excels at breadth.</strong> With 5,000+ rules
              across 30+ languages, it catches a wide range of bugs, vulnerabilities, code smells, and
              security hotspots. Its SAST capabilities are mature and well-tested across millions of projects.
            </p>
            <p className="text-muted-foreground leading-relaxed">
              <strong className="text-foreground">Repotoire excels at depth.</strong> Its 110 detectors
              include graph-based architectural analysis that SonarQube cannot perform: circular dependency
              detection via Tarjan&apos;s SCC, god class identification through fan-in/fan-out metrics,
              architectural bottleneck detection via PageRank and betweenness centrality, hidden coupling
              through git co-change analysis, and community misplacement via Louvain clustering.
            </p>
            <p className="text-muted-foreground leading-relaxed">
              If your primary concern is per-file bug and vulnerability detection across many languages,
              SonarQube has the edge. If you need to understand and improve your codebase&apos;s architecture,
              Repotoire finds issues that rule-based tools structurally cannot detect.
            </p>
          </div>
        </section>

        {/* Setup & Deployment */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Setup &amp; Deployment
          </h2>
          <div className="grid md:grid-cols-2 gap-6">
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-primary mb-3">Repotoire</h3>
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>Install a single binary. Run it. That&apos;s it.</p>
                <pre className="bg-muted/30 rounded-lg p-3 font-mono text-xs overflow-x-auto">
{`# Install
cargo binstall repotoire
# or: brew install repotoire

# Analyze
repotoire analyze .`}
                </pre>
                <p>No Java, no database, no server configuration. Works offline. Results in seconds.</p>
              </div>
            </div>
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-muted-foreground mb-3">SonarQube</h3>
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>Requires Java 17+, a database, and server configuration.</p>
                <pre className="bg-muted/30 rounded-lg p-3 font-mono text-xs overflow-x-auto">
{`# Start server (Docker)
docker run -d sonarqube:community

# Install scanner
brew install sonar-scanner

# Configure & scan
sonar-scanner \\
  -Dsonar.projectKey=my-project \\
  -Dsonar.host.url=http://localhost:9000`}
                </pre>
                <p>Or use SonarCloud for a managed experience without self-hosting.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Pricing */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Pricing
          </h2>
          <div className="card-elevated rounded-xl p-6">
            <div className="grid md:grid-cols-2 gap-6">
              <div>
                <h3 className="font-display font-bold text-primary mb-3">Repotoire</h3>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">&#10003;</span>
                    <span>CLI is free and open source</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">&#10003;</span>
                    <span>All 110 detectors included</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">&#10003;</span>
                    <span>Pro plans (team features, dashboard) coming soon</span>
                  </li>
                </ul>
              </div>
              <div>
                <h3 className="font-display font-bold text-muted-foreground mb-3">SonarQube</h3>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li className="flex items-start gap-2">
                    <span className="text-muted-foreground/60 mt-0.5">&#8226;</span>
                    <span>Community Edition: Free (open source, limited features)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-muted-foreground/60 mt-0.5">&#8226;</span>
                    <span>Developer Edition: ~$150/year (branch analysis, PR decoration)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-muted-foreground/60 mt-0.5">&#8226;</span>
                    <span>Enterprise Edition: $20K&ndash;$65K+/year (portfolio management, SAST)</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-muted-foreground/60 mt-0.5">&#8226;</span>
                    <span>SonarCloud: Free for open source, paid for private repos</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* Who It's For */}
        <section className="mb-12">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Who Each Tool Is For
          </h2>
          <div className="grid md:grid-cols-2 gap-6">
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-primary mb-3">Choose Repotoire if you...</h3>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li>Need architectural analysis (circular deps, coupling, bottlenecks)</li>
                <li>Want zero-setup, single binary deployment</li>
                <li>Work primarily in Rust, Python, TypeScript, Go, Java, C#, C, or C++</li>
                <li>Value graph-powered insights over rule count</li>
                <li>Want fast local analysis without a server</li>
              </ul>
            </div>
            <div className="card-elevated rounded-xl p-6">
              <h3 className="font-display font-bold text-muted-foreground mb-3">Choose SonarQube if you...</h3>
              <ul className="space-y-2 text-sm text-muted-foreground">
                <li>Need coverage across 30+ languages</li>
                <li>Want mature quality gates and CI/CD integration</li>
                <li>Need a centralized dashboard for multiple projects</li>
                <li>Require compliance reporting and enterprise governance</li>
                <li>Already have Java infrastructure and database resources</li>
              </ul>
            </div>
          </div>
        </section>

        {/* Verdict */}
        <section className="mb-16">
          <h2 className="text-2xl font-display font-bold text-foreground mb-4">
            Verdict
          </h2>
          <div className="card-elevated rounded-xl p-6">
            <p className="text-muted-foreground leading-relaxed mb-4">
              SonarQube and Repotoire solve different problems. SonarQube is the right choice when you need
              broad language coverage, enterprise governance, and a centralized quality platform. Its ecosystem
              is mature, well-documented, and battle-tested.
            </p>
            <p className="text-muted-foreground leading-relaxed">
              Repotoire is the right choice when you need to understand your codebase&apos;s architecture.
              Graph-powered analysis finds structural problems &mdash; circular dependencies, architectural
              bottlenecks, hidden coupling &mdash; that rule-based tools cannot detect. And with a single
              binary and no infrastructure requirements, you can be running in seconds. Many teams use both:
              SonarQube for broad coverage in CI, and Repotoire for architectural health locally.
            </p>
          </div>
        </section>

        {/* CTA */}
        <section className="text-center">
          <div className="card-elevated rounded-xl p-8">
            <h2 className="text-2xl font-display font-bold text-foreground mb-3">
              Try Repotoire on your codebase
            </h2>
            <p className="text-muted-foreground mb-6">
              See what your linter is missing. One command, zero setup.
            </p>
            <pre className="bg-muted/30 rounded-lg p-4 font-mono text-sm text-muted-foreground mb-6 inline-block">
              cargo binstall repotoire &amp;&amp; repotoire analyze .
            </pre>
            <div className="flex items-center justify-center gap-4">
              <Link
                href="/docs/cli"
                className="inline-flex items-center justify-center rounded-md bg-primary px-6 py-3 text-sm font-display font-medium text-primary-foreground shadow hover:bg-primary/90 transition-colors"
              >
                Get Started Free
              </Link>
              <a
                href="https://github.com/Zach-hammad/repotoire"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center rounded-md border border-border px-6 py-3 text-sm font-display font-medium text-foreground hover:bg-muted/50 transition-colors"
              >
                View on GitHub
              </a>
            </div>
          </div>
        </section>
      </div>
    </article>
  )
}
