import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { notFound } from "next/navigation";

const posts: Record<
  string,
  {
    title: string;
    date: string;
    readTime: string;
    content: string;
  }
> = {
  "introducing-graph-powered-code-analysis": {
    title: "Introducing Graph-Powered Code Analysis",
    date: "December 15, 2024",
    readTime: "5 min read",
    content: `
## Why Traditional Linters Aren't Enough

Traditional static analysis tools like ESLint, Pylint, and Ruff are excellent at catching syntax errors, style violations, and common bugs. But they share a fundamental limitation: **they analyze files in isolation**.

When you run \`ruff check\`, each file is parsed and validated independently. The tool has no understanding of how your modules connect, which functions call which, or how changes in one file ripple through your codebase.

This isolation means traditional tools miss some of the most impactful issues:

- **Circular dependencies** that make code impossible to test
- **Architectural bottlenecks** where a single module has 100+ dependents
- **Dead code** that's exported but never imported
- **Feature envy** where functions constantly reach into other modules

## Enter Knowledge Graphs

Repotoire takes a different approach. Instead of analyzing files one by one, we build a **knowledge graph** of your entire codebase:

\`\`\`
Codebase → Parser (AST) → Entities + Relationships → Neo4j Graph → Detectors → Health Report
\`\`\`

Every function, class, module, and import becomes a node in the graph. Relationships like \`IMPORTS\`, \`CALLS\`, \`INHERITS\`, and \`USES\` connect them together.

This graph-first approach enables queries that would be impossible with file-based analysis:

\`\`\`cypher
// Find all circular dependencies
MATCH path = (a:Module)-[:IMPORTS*]->(a)
WHERE length(path) > 1
RETURN path
\`\`\`

\`\`\`cypher
// Find bottleneck modules (high in-degree)
MATCH (m:Module)<-[:IMPORTS]-(dependent)
WITH m, count(dependent) as importers
WHERE importers > 20
RETURN m.name, importers
ORDER BY importers DESC
\`\`\`

## The Three-Layer Analysis

Repotoire combines three types of analysis:

1. **Structural (AST)**: Parse code into abstract syntax trees, extract entities and relationships
2. **Semantic (NLP + AI)**: Understand naming patterns, detect code smells using natural language
3. **Relational (Graph)**: Run graph algorithms to find architectural issues

Each layer catches different types of issues. Together, they provide a holistic view of code health that no single tool can match.

## Getting Started

Try Repotoire on your codebase today:

\`\`\`bash
cargo install repotoire
repotoire ingest /path/to/repo
repotoire analyze /path/to/repo -o report.html
\`\`\`

The HTML report shows your health score broken down by Structure, Quality, and Architecture, with specific issues and fix suggestions.
    `,
  },
  "incremental-processing": {
    title: "Faster Re-Analysis with Incremental Processing",
    date: "December 10, 2024",
    readTime: "4 min read",
    content: `
## The Problem with Full Re-Analysis

Running a full code analysis on a large codebase can take minutes. For a 10,000+ file monorepo, you might be waiting 10-15 minutes every time you want to check code health.

This creates a painful workflow: developers avoid running analysis because it's too slow, issues accumulate, and by the time someone runs a full scan, there are hundreds of new problems.

## Hash-Based Change Detection

Repotoire solves this with **incremental analysis**. When you run \`repotoire ingest\`, we compute an MD5 hash of each file and store it in the graph:

\`\`\`cypher
(:File {path: "src/auth.py", content_hash: "a1b2c3..."})
\`\`\`

On subsequent runs, we compare hashes to detect what's changed:

- **Modified files**: Hash changed → re-parse
- **New files**: No node exists → parse and add
- **Deleted files**: Node exists but file doesn't → remove from graph

## Dependency-Aware Analysis

But just analyzing changed files isn't enough. If you modify \`auth.py\`, any file that imports it might have issues too. Repotoire traces the dependency graph to find affected files:

\`\`\`cypher
// Find all files that depend on changed files (up to 3 hops)
MATCH (changed:File)-[:IMPORTS*1..3]->(dependent:File)
WHERE changed.path IN $changedPaths
RETURN DISTINCT dependent.path
\`\`\`

This "impact radius" ensures we catch issues introduced by API changes, not just local problems.

## Real Performance

Here's an example of incremental analysis on a ~1,200 file codebase (results vary based on codebase size and number of changes):

| Metric | Full Analysis | Incremental |
|--------|--------------|-------------|
| Files | 1,234 | 29 (2.3%) |
| Time | 5 minutes | 8 seconds |

The speedup depends on how many files you change. When you only modify a few files, incremental analysis processes a small fraction of your codebase.

## Usage

Incremental analysis is enabled by default:

\`\`\`bash
# First run: full ingestion
repotoire ingest /path/to/repo

# Subsequent runs: incremental
repotoire ingest /path/to/repo  # Automatically detects changes

# Force full re-analysis if needed
repotoire ingest /path/to/repo --force-full
\`\`\`

Combined with our pre-commit hook integration, you get instant feedback on every commit:

\`\`\`yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: repotoire-check
        name: Repotoire Code Quality Check
        entry: repotoire-pre-commit
        language: system
        types: [python]
\`\`\`
    `,
  },
  "ai-powered-auto-fix": {
    title: "AI-Powered Auto-Fix: From Detection to Resolution",
    date: "December 5, 2024",
    readTime: "6 min read",
    content: `
## Beyond Detection

Finding code issues is only half the battle. The real value is in **fixing** them.

Traditional tools tell you "Function X has cyclomatic complexity of 15" but leave you to figure out the fix. With Repotoire's auto-fix system, we go from detection to resolution using GPT-4o and RAG (Retrieval-Augmented Generation).

## How Auto-Fix Works

When you run \`repotoire auto-fix\`, here's what happens:

1. **Issue Detection**: Run all detectors to find problems
2. **Context Retrieval**: Use embeddings to find related code
3. **Fix Generation**: GPT-4o generates a fix with evidence
4. **Human Review**: You approve, modify, or reject the fix
5. **Application**: Apply approved fixes via clean diffs

The key insight is **evidence-based fixing**. Instead of generating fixes in a vacuum, we retrieve relevant code patterns from your codebase to inform the fix.

## RAG in Action

Say we detect a "God Class" with 50+ methods. Before generating a fix, we search for similar patterns:

\`\`\`python
# Semantic search using embeddings
similar_classes = retriever.search(
    query="well-structured class with single responsibility",
    entity_type="Class",
    top_k=5
)
\`\`\`

This retrieves examples of well-designed classes from *your own codebase*. GPT-4o then uses these as templates for the refactoring suggestion.

## Human-in-the-Loop

We believe AI-generated fixes should always be reviewed by humans. Auto-fix presents each change with:

- **Before/After diff**: See exactly what will change
- **Evidence**: The similar code patterns that informed the fix
- **Justification**: Why this fix addresses the issue
- **Confidence score**: How certain the AI is about the fix

You can:
- **Accept**: Apply the fix as-is
- **Modify**: Edit the fix before applying
- **Reject**: Skip this fix
- **Regenerate**: Ask for a different approach

## Example Session

\`\`\`bash
$ repotoire auto-fix /path/to/repo --severity high

Found 3 high-severity issues

[1/3] Complex function: calculate_metrics (complexity: 18)
File: src/analytics/metrics.py:45-120

Suggested fix:
  - Extract 4 helper functions
  - Reduce complexity from 18 to 6
  - Preserve all behavior

Evidence: Similar refactoring in src/reports/generator.py

[a]ccept, [m]odify, [r]eject, [s]kip? a

Applied fix to src/analytics/metrics.py
\`\`\`

## Getting Started

\`\`\`bash
# Install the CLI
cargo install repotoire
export OPENAI_API_KEY="sk-..."

# Generate embeddings for RAG
repotoire ingest /path/to/repo --generate-embeddings

# Run auto-fix
repotoire auto-fix /path/to/repo
\`\`\`

For high-confidence fixes, you can enable auto-approve:

\`\`\`bash
repotoire auto-fix /path/to/repo --auto-approve-high
\`\`\`

This automatically applies fixes with >90% confidence, speeding up bulk refactoring while keeping you in control for uncertain cases.
    `,
  },
};

interface PageProps {
  params: Promise<{ slug: string }>;
}

export default async function BlogPostPage({ params }: PageProps) {
  const { slug } = await params;
  const post = posts[slug];

  if (!post) {
    notFound();
  }

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-3xl mx-auto">
        <Link
          href="/blog"
          className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors mb-8"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Blog
        </Link>

        <article>
          <header className="mb-8">
            <h1 className="text-3xl sm:text-4xl font-display font-bold text-foreground mb-4">
              {post.title}
            </h1>
            <div className="flex items-center gap-4 text-sm text-muted-foreground">
              <span>{post.date}</span>
              <span className="w-1 h-1 rounded-full bg-muted-foreground" />
              <span>{post.readTime}</span>
            </div>
          </header>

          <div className="prose prose-lg dark:prose-invert max-w-none prose-headings:font-display prose-headings:font-bold prose-h2:text-2xl prose-h2:mt-8 prose-h2:mb-4 prose-p:text-muted-foreground prose-p:leading-relaxed prose-code:text-foreground prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-a:text-primary prose-a:no-underline hover:prose-a:underline prose-strong:text-foreground prose-li:text-muted-foreground">
            {post.content.split("\n").map((line, i) => {
              if (line.startsWith("## ")) {
                return (
                  <h2 key={i} className="text-2xl font-display font-bold text-foreground mt-8 mb-4">
                    {line.replace("## ", "")}
                  </h2>
                );
              }
              if (line.startsWith("```")) {
                return null; // Skip code fence markers
              }
              if (line.trim() === "") {
                return <br key={i} />;
              }
              if (line.startsWith("- **") || line.startsWith("| ")) {
                return (
                  <p key={i} className="text-muted-foreground font-mono text-sm">
                    {line}
                  </p>
                );
              }
              return (
                <p key={i} className="text-muted-foreground leading-relaxed">
                  {line}
                </p>
              );
            })}
          </div>
        </article>

        <div className="mt-12 pt-8 border-t border-border">
          <Link
            href="/blog"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Blog
          </Link>
        </div>
      </div>
    </section>
  );
}

export function generateStaticParams() {
  return Object.keys(posts).map((slug) => ({ slug }));
}
