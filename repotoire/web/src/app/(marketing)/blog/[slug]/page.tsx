import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { MDXRemote } from "next-mdx-remote/rsc";
import { getAllPosts, getPostBySlug, getRelatedPosts } from "@/lib/blog";
import { authors } from "@/content/authors";
import { mdxComponents } from "@/app/components/mdx-components";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

type Props = {
  params: Promise<{ slug: string }>;
};

export async function generateStaticParams() {
  const posts = getAllPosts();
  return posts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;

  try {
    const { frontmatter } = getPostBySlug(slug);
    return {
      title: `${frontmatter.title} - Repotoire`,
      description: frontmatter.description,
      openGraph: {
        title: frontmatter.title,
        description: frontmatter.description,
        type: "article",
        publishedTime: frontmatter.date,
        url: `https://www.repotoire.com/blog/${slug}`,
        siteName: "Repotoire",
        ...(frontmatter.image && { images: [frontmatter.image] }),
      },
      alternates: {
        canonical: `/blog/${slug}`,
      },
    };
  } catch {
    return {};
  }
}

export default async function BlogPost({ params }: Props) {
  const { slug } = await params;

  let post;
  try {
    post = getPostBySlug(slug);
  } catch {
    notFound();
  }

  const { frontmatter, content, readingTime } = post;
  const author = authors[frontmatter.author];
  const related = getRelatedPosts(slug);

  const blogPostingSchema = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: frontmatter.title,
    description: frontmatter.description,
    url: `https://www.repotoire.com/blog/${slug}`,
    datePublished: frontmatter.date,
    dateModified: frontmatter.date,
    author: {
      "@type": "Person",
      name: author?.name || "Zach Hammad",
    },
    publisher: {
      "@type": "Organization",
      name: "Repotoire",
      url: "https://www.repotoire.com",
    },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `https://www.repotoire.com/blog/${slug}`,
    },
    keywords: frontmatter.tags,
  };

  const breadcrumbSchema = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "Home",
        item: "https://www.repotoire.com",
      },
      {
        "@type": "ListItem",
        position: 2,
        name: "Blog",
        item: "https://www.repotoire.com/blog",
      },
      {
        "@type": "ListItem",
        position: 3,
        name: frontmatter.title,
        item: `https://www.repotoire.com/blog/${slug}`,
      },
    ],
  };

  return (
    <section className="py-24 px-4 sm:px-6 lg:px-8">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(blogPostingSchema),
        }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(breadcrumbSchema),
        }}
      />

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
              {frontmatter.title}
            </h1>
            <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground mb-3">
              {author && <span>{author.name}</span>}
              <span className="w-1 h-1 rounded-full bg-muted-foreground" />
              <span>
                {new Date(frontmatter.date).toLocaleDateString("en-US", {
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                })}
              </span>
              <span className="w-1 h-1 rounded-full bg-muted-foreground" />
              <span>{readingTime} min read</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {frontmatter.tags.map((tag) => (
                <Link
                  key={tag}
                  href={`/blog?tag=${encodeURIComponent(tag)}`}
                  className="text-[10px] tracking-widest uppercase text-muted-foreground border border-border px-2 py-0.5 rounded hover:text-foreground hover:border-foreground/30 transition-colors"
                >
                  {tag}
                </Link>
              ))}
            </div>
          </header>

          <div className="space-y-0">
            <MDXRemote source={content} components={mdxComponents} />
          </div>
        </article>

        {related.length > 0 && (
          <div className="mt-16 pt-8 border-t border-border">
            <p className="text-xs tracking-widest uppercase text-muted-foreground mb-6">
              Related Posts
            </p>
            <div className="space-y-4">
              {related.map((r) => (
                <Link
                  key={r.slug}
                  href={`/blog/${r.slug}`}
                  className="card-elevated rounded-xl p-4 block hover:border-border/80 transition-colors group"
                >
                  <h3 className="text-sm font-display font-bold text-foreground group-hover:text-primary transition-colors mb-1">
                    {r.title}
                  </h3>
                  <p className="text-xs text-muted-foreground">
                    {r.description}
                  </p>
                </Link>
              ))}
            </div>
          </div>
        )}

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
