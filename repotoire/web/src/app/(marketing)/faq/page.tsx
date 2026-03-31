import type { Metadata } from "next";
import {
  FULL_GRAPH_LANGUAGE_LIST,
  REGEX_SCANNED_LANGUAGE_LIST,
  TOTAL_DETECTOR_LABEL,
  TOTAL_LANGUAGE_LABEL,
} from "@/lib/product-facts.generated";

export const metadata: Metadata = {
  title: "Frequently Asked Questions - Repotoire",
  description:
    "Common questions about Repotoire — graph-powered code analysis, detectors, installation, supported languages, and how it compares to linters.",
  alternates: {
    canonical: "/faq",
  },
  openGraph: {
    title: "Frequently Asked Questions - Repotoire",
    description:
      "Common questions about Repotoire — graph-powered code analysis, detectors, and supported languages.",
    url: "https://www.repotoire.com/faq",
  },
};

const faqs = [
  {
    q: "What is Repotoire?",
    a: `Repotoire is a graph-powered code analysis CLI that detects architectural issues, security vulnerabilities, and code smells across your codebase. It builds a knowledge graph of your code using petgraph and tree-sitter, then runs ${TOTAL_DETECTOR_LABEL} to find problems that traditional file-by-file linters miss — like circular dependencies, god classes, architectural bottlenecks, and hidden coupling.`,
  },
  {
    q: "How is Repotoire different from ESLint or SonarQube?",
    a: "Traditional linters like ESLint analyze files in isolation — they can catch syntax errors, style violations, and per-file bugs. Repotoire analyzes relationships between files by building a graph of your entire codebase. This lets it detect cross-file issues like circular dependencies, architectural bottlenecks, and coupling problems that no file-by-file linter can see. It complements ESLint rather than replacing it.",
  },
  {
    q: "What languages does Repotoire support?",
    a: `Repotoire supports ${TOTAL_LANGUAGE_LABEL}: full graph analysis for ${FULL_GRAPH_LANGUAGE_LIST}, plus regex-scanned security and quality coverage for ${REGEX_SCANNED_LANGUAGE_LIST}. All full-graph parsing is done via tree-sitter grammars compiled into the binary — no external parser dependencies needed.`,
  },
  {
    q: "How do I install Repotoire?",
    a: "Install via Homebrew (brew install repotoire), cargo install (cargo install repotoire), cargo-binstall for prebuilt binaries (cargo binstall repotoire), or npm (npx repotoire). It's a single binary with zero runtime dependencies.",
  },
  {
    q: "Is Repotoire free?",
    a: "The CLI is free and open source. You can analyze any codebase locally without limits. The web dashboard (at repotoire.com) has free and paid tiers for team features, history, and CI/CD integration.",
  },
  {
    q: "What are the 110 detectors?",
    a: "Repotoire has 77 default detectors and 33 deep-scan detectors (enabled with --all-detectors). Categories include security, code quality, graph-based code smells, architecture, and specialized detectors for AI-generated code, ML/data science, Rust, and async patterns.",
  },
  {
    q: "How does the scoring work?",
    a: "Repotoire uses a three-pillar scoring system: Structure (40%), Quality (30%), and Architecture (30%). Findings are weighted by severity (Critical=5, High=2, Medium=0.5, Low=0.1). Graph-derived bonuses reward good practices like high modularity, clean dependencies, and balanced complexity distribution. Scores range from F to A+, with 13 grade levels.",
  },
  {
    q: "How fast is Repotoire?",
    a: "Cold analysis of a typical codebase takes 10-20 seconds. Subsequent runs use incremental caching and typically complete in 1-2 seconds for single-file changes. All parsing and detection runs in parallel via rayon. Files over 2MB are automatically skipped.",
  },
  {
    q: "Does Repotoire work in CI/CD?",
    a: "Yes. There's an official GitHub Action (Zach-hammad/repotoire-action@v1) that runs analysis on PRs, posts comments with findings, uploads SARIF for GitHub Code Scanning, and supports quality gates (--fail-on high to block merges). SARIF 2.1.0 output works with any CI system that supports it.",
  },
  {
    q: "What output formats does Repotoire support?",
    a: "Five formats: text (default, with themed narrative output), JSON (machine-readable), HTML (standalone report with SVG architecture map, treemap, and charts), SARIF 2.1.0 (GitHub Code Scanning compatible), and Markdown. Use --format to select.",
  },
  {
    q: "Can I suppress specific findings?",
    a: "Yes. Add // repotoire:ignore on the line before a finding to suppress all detectors, or // repotoire:ignore[detector-name] to suppress a specific detector. Supports //, #, /*, and -- comment styles. You can also configure exclusions in repotoire.toml.",
  },
  {
    q: "Does Repotoire send my code anywhere?",
    a: "No. All analysis runs locally on your machine. The only network call is optional telemetry (PostHog, opt-in only) which sends aggregate metrics like language distribution and score — never source code. You can disable it with repotoire config telemetry off.",
  },
];

export default function FAQPage() {
  const faqSchema = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.q,
      acceptedAnswer: {
        "@type": "Answer",
        text: faq.a,
      },
    })),
  };

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      {/* repotoire:ignore[XssDetector] — JSON-LD structured data, no user input */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />
      <div className="max-w-3xl mx-auto">
        <div className="text-center mb-16">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
            <span className="font-serif italic text-muted-foreground">
              Frequently
            </span>{" "}
            <span className="text-gradient font-display font-bold">
              Asked Questions
            </span>
          </h1>
          <p className="text-muted-foreground">
            Everything you need to know about Repotoire.
          </p>
        </div>

        <div className="space-y-8">
          {faqs.map((faq, i) => (
            <div
              key={i}
              className="card-elevated rounded-xl p-6"
            >
              <h2 className="text-base font-display font-bold text-foreground mb-3">
                {faq.q}
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {faq.a}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
