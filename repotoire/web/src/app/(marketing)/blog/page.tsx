import type { Metadata } from "next";
import Link from "next/link";
import { getAllPosts } from "@/lib/blog";

export const metadata: Metadata = {
  title: "Blog - Repotoire",
  description:
    "Insights on code quality, architecture analysis, and technical debt. Graph-powered perspectives on building better software.",
  alternates: {
    canonical: "/blog",
  },
  openGraph: {
    title: "Blog - Repotoire",
    description:
      "Insights on code quality, architecture analysis, and technical debt. Graph-powered perspectives on building better software.",
    type: "website",
    url: "https://www.repotoire.com/blog",
  },
};

type Props = {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
};

export default async function BlogPage({ searchParams }: Props) {
  const resolvedParams = await searchParams;
  const tag =
    typeof resolvedParams.tag === "string" ? resolvedParams.tag : undefined;
  const allPosts = getAllPosts();
  const posts = tag
    ? allPosts.filter((p) => p.tags.includes(tag))
    : allPosts;

  const allTags = [...new Set(allPosts.flatMap((p) => p.tags))].sort();

  const itemListSchema = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: "Repotoire Blog",
    url: "https://www.repotoire.com/blog",
    itemListElement: allPosts.map((post, i) => ({
      "@type": "ListItem",
      position: i + 1,
      url: `https://www.repotoire.com/blog/${post.slug}`,
      name: post.title,
    })),
  };

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(itemListSchema) }}
      />
      <div className="max-w-4xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
            <span className="font-serif italic text-muted-foreground">
              The
            </span>{" "}
            <span className="text-gradient font-display font-bold">Blog</span>
          </h1>
          <p className="text-muted-foreground">
            Insights on code quality, architecture analysis, and technical debt.
          </p>
        </div>

        <div className="flex flex-wrap justify-center gap-2 mb-12">
          <Link
            href="/blog"
            className={`text-xs tracking-wide px-3 py-1 rounded-full border transition-colors ${
              !tag
                ? "text-foreground border-border bg-muted"
                : "text-muted-foreground border-transparent hover:text-foreground hover:border-border"
            }`}
          >
            All
          </Link>
          {allTags.map((t) => (
            <Link
              key={t}
              href={`/blog?tag=${encodeURIComponent(t)}`}
              className={`text-xs tracking-wide px-3 py-1 rounded-full border transition-colors ${
                tag === t
                  ? "text-foreground border-border bg-muted"
                  : "text-muted-foreground border-transparent hover:text-foreground hover:border-border"
              }`}
            >
              {t}
            </Link>
          ))}
        </div>

        <div className="space-y-6">
          {posts.map((post) => (
            <Link
              key={post.slug}
              href={`/blog/${post.slug}`}
              className="card-elevated rounded-xl p-6 block hover:border-border/80 transition-colors group"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 className="text-lg font-display font-bold text-foreground mb-2 group-hover:text-primary transition-colors">
                    {post.title}
                  </h2>
                  <p className="text-muted-foreground text-sm mb-3">
                    {post.description}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {post.tags.map((t) => (
                      <span
                        key={t}
                        className="text-[10px] tracking-widest uppercase text-muted-foreground border border-border px-2 py-0.5 rounded"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <span className="text-xs text-muted-foreground whitespace-nowrap">
                    {new Date(post.date).toLocaleDateString("en-US", {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                  </span>
                  <div className="text-[10px] text-muted-foreground mt-1">
                    {post.readingTime} min read
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>

        {posts.length === 0 && (
          <p className="text-center text-muted-foreground">
            No posts found{tag ? ` for "${tag}"` : ""}.
          </p>
        )}
      </div>
    </section>
  );
}
