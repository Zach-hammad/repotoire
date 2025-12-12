import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { notFound } from "next/navigation";
import { ArrowLeft, Rss, Calendar, User, Tag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ChangelogContent, ChangelogSubscribe } from "@/components/changelog";
import {
  fetchChangelogEntry,
  getCategoryLabel,
  getCategoryColor,
  formatDate,
} from "@/lib/changelog-api";

interface PageProps {
  params: Promise<{ slug: string }>;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;

  try {
    const entry = await fetchChangelogEntry(slug);
    return {
      title: `${entry.title} - Changelog - Repotoire`,
      description: entry.summary,
      openGraph: {
        title: entry.title,
        description: entry.summary,
        type: "article",
        publishedTime: entry.published_at || undefined,
        authors: entry.author_name ? [entry.author_name] : undefined,
      },
      twitter: {
        card: "summary_large_image",
        title: entry.title,
        description: entry.summary,
      },
    };
  } catch {
    return {
      title: "Changelog Entry - Repotoire",
    };
  }
}

// Revalidate every 5 minutes
export const revalidate = 300;

async function getEntry(slug: string) {
  try {
    return await fetchChangelogEntry(slug);
  } catch (error) {
    console.error("Failed to fetch changelog entry:", error);
    return null;
  }
}

export default async function ChangelogEntryPage({ params }: PageProps) {
  const { slug } = await params;
  const entry = await getEntry(slug);

  if (!entry) {
    notFound();
  }

  // JSON-LD structured data for SEO
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    headline: entry.title,
    description: entry.summary,
    datePublished: entry.published_at,
    dateModified: entry.updated_at || entry.published_at,
    author: entry.author_name
      ? {
          "@type": "Person",
          name: entry.author_name,
        }
      : undefined,
    publisher: {
      "@type": "Organization",
      name: "Repotoire",
      url: "https://repotoire.io",
    },
    mainEntityOfPage: {
      "@type": "WebPage",
      "@id": `https://repotoire.io/changelog/${entry.slug}`,
    },
  };

  return (
    <div className="min-h-screen bg-background">
      {/* JSON-LD */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* Header */}
      <header className="border-b">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <Image
              src="/logo.png"
              alt="Repotoire"
              width={120}
              height={28}
              className="h-7 w-auto dark:hidden"
            />
            <Image
              src="/logo-grayscale.png"
              alt="Repotoire"
              width={120}
              height={28}
              className="h-7 w-auto hidden dark:block brightness-200"
            />
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/changelog/rss"
              className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <Rss className="h-4 w-4" />
              <span className="hidden sm:inline">RSS</span>
            </Link>
            <Link href="/dashboard">
              <Button variant="outline" size="sm">
                Dashboard
              </Button>
            </Link>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-8">
        {/* Back Link */}
        <Link
          href="/changelog"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground mb-6"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Changelog
        </Link>

        <article>
          {/* Entry Header */}
          <header className="mb-8">
            <div className="flex flex-wrap items-center gap-2 mb-4">
              {entry.version && (
                <Badge variant="outline" className="font-mono">
                  {entry.version}
                </Badge>
              )}
              <Badge
                variant="outline"
                className={getCategoryColor(entry.category)}
              >
                {getCategoryLabel(entry.category)}
              </Badge>
              {entry.is_major && (
                <Badge className="bg-primary">Major Release</Badge>
              )}
            </div>

            <h1 className="text-4xl font-bold tracking-tight mb-4">
              {entry.title}
            </h1>

            <p className="text-xl text-muted-foreground mb-6">{entry.summary}</p>

            <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
              {entry.published_at && (
                <div className="flex items-center gap-1">
                  <Calendar className="h-4 w-4" />
                  <time dateTime={entry.published_at}>
                    {formatDate(entry.published_at)}
                  </time>
                </div>
              )}
              {entry.author_name && (
                <div className="flex items-center gap-1">
                  <User className="h-4 w-4" />
                  <span>{entry.author_name}</span>
                </div>
              )}
            </div>
          </header>

          {/* Entry Content */}
          <div className="border-t pt-8">
            <ChangelogContent html={entry.content_html || ""} />
          </div>
        </article>

        {/* Subscribe Section */}
        <div className="mt-12 border-t pt-8">
          <h2 className="text-xl font-semibold mb-4">Stay Updated</h2>
          <div className="max-w-md">
            <ChangelogSubscribe />
          </div>
        </div>

        {/* Navigation */}
        <div className="mt-8 pt-8 border-t">
          <Link
            href="/changelog"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← View all changelog entries
          </Link>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t mt-16">
        <div className="max-w-4xl mx-auto px-4 py-6 text-center text-sm text-muted-foreground">
          <p>
            <Link href="/" className="hover:text-foreground">
              Repotoire
            </Link>
            {" - "}
            AI-Powered Code Health Platform
          </p>
        </div>
      </footer>
    </div>
  );
}
