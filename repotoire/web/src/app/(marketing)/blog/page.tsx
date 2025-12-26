import Link from "next/link";

const posts = [
  {
    title: "Introducing Graph-Powered Code Analysis",
    excerpt: "Why traditional linters aren't enough and how knowledge graphs change everything.",
    date: "Dec 15, 2024",
    slug: "/blog/introducing-graph-powered-code-analysis",
  },
  {
    title: "Faster Re-Analysis with Incremental Processing",
    excerpt: "How we use hash-based change detection and dependency tracking for faster updates.",
    date: "Dec 10, 2024",
    slug: "/blog/incremental-processing",
  },
  {
    title: "AI-Powered Auto-Fix: From Detection to Resolution",
    excerpt: "How GPT-4o and RAG work together to generate evidence-based code fixes.",
    date: "Dec 5, 2024",
    slug: "/blog/ai-powered-auto-fix",
  },
];

export default function BlogPage() {
  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
            <span className="font-serif italic text-muted-foreground">The</span>{" "}
            <span className="text-gradient font-display font-bold">Blog</span>
          </h1>
          <p className="text-muted-foreground">
            Insights on code quality, graph databases, and AI-powered development.
          </p>
        </div>

        <div className="space-y-6">
          {posts.map((post) => (
            <Link
              key={post.title}
              href={post.slug}
              className="card-elevated rounded-xl p-6 block hover:border-border/80 transition-colors"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-display font-bold text-foreground mb-2">
                    {post.title}
                  </h2>
                  <p className="text-muted-foreground text-sm">{post.excerpt}</p>
                </div>
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  {post.date}
                </span>
              </div>
            </Link>
          ))}
        </div>

        <div className="mt-12 text-center">
          <p className="text-muted-foreground">
            Stay updated on code quality insights. Follow us on{" "}
            <a
              href="https://twitter.com/repotoire"
              target="_blank"
              rel="noopener noreferrer"
              className="text-foreground hover:underline"
            >
              Twitter
            </a>{" "}
            for the latest.
          </p>
        </div>
      </div>
    </section>
  );
}
