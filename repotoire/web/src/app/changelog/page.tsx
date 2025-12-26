import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { Rss } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChangelogList, ChangelogSubscribe } from "@/components/changelog";
import { fetchChangelogEntries, ChangelogEntry } from "@/lib/changelog-api";

// Fallback changelog entries for when API is unavailable
const fallbackEntries: ChangelogEntry[] = [
  {
    id: "1",
    version: "1.0.0",
    title: "Repotoire 1.0 - Official Launch",
    slug: "v1-0-0-official-launch",
    summary: "We're excited to announce the official launch of Repotoire! Graph-powered code health analysis is now available for everyone. Analyze your codebase, detect architectural issues, and get AI-powered fix suggestions.",
    category: "feature",
    is_major: true,
    published_at: "2024-12-01T00:00:00Z",
    image_url: null,
  },
  {
    id: "2",
    version: "1.0.0",
    title: "8 Hybrid Detectors for Comprehensive Analysis",
    slug: "hybrid-detectors",
    summary: "Repotoire now includes 8 production-ready hybrid detectors: Ruff, Pylint, Mypy, Bandit, Radon, Jscpd, Vulture, and Semgrep. Each detector combines external tool analysis with graph-based context enrichment.",
    category: "feature",
    is_major: false,
    published_at: "2024-12-01T00:00:00Z",
    image_url: null,
  },
  {
    id: "3",
    version: "1.0.0",
    title: "AI-Powered Auto-Fix with Human-in-the-Loop",
    slug: "ai-auto-fix",
    summary: "Introducing AI-powered code fixing using GPT-4o and RAG. Get evidence-based fix suggestions with before/after diffs, and approve changes before they're applied to your codebase.",
    category: "feature",
    is_major: false,
    published_at: "2024-12-01T00:00:00Z",
    image_url: null,
  },
  {
    id: "4",
    version: "1.0.0",
    title: "Fast Incremental Analysis",
    slug: "incremental-analysis",
    summary: "Hash-based change detection and dependency-aware analysis means re-analyzing your codebase is significantly faster. Only changed files and their dependents are processed.",
    category: "improvement",
    is_major: false,
    published_at: "2024-11-15T00:00:00Z",
    image_url: null,
  },
  {
    id: "5",
    version: "1.0.0",
    title: "Pre-commit Hook Integration",
    slug: "pre-commit-hooks",
    summary: "Integrate Repotoire into your development workflow with pre-commit hooks. Get instant feedback on code quality before commits are finalized.",
    category: "feature",
    is_major: false,
    published_at: "2024-11-15T00:00:00Z",
    image_url: null,
  },
];

export const metadata: Metadata = {
  title: "Changelog - Repotoire",
  description:
    "Stay up to date with the latest features, improvements, and fixes in Repotoire. View our product changelog and release notes.",
  openGraph: {
    title: "Changelog - Repotoire",
    description: "Latest features, improvements, and fixes in Repotoire",
    type: "website",
  },
  alternates: {
    types: {
      "application/rss+xml": "/changelog/rss",
    },
  },
};

// Revalidate every 5 minutes
export const revalidate = 300;

async function getChangelogEntries() {
  try {
    const result = await fetchChangelogEntries({ limit: 20 });
    // If API returns empty, use fallback entries
    if (result.entries.length === 0) {
      return { entries: fallbackEntries, total: fallbackEntries.length, has_more: false };
    }
    return result;
  } catch (error) {
    console.error("Failed to fetch changelog entries:", error);
    // Use fallback entries when API fails
    return { entries: fallbackEntries, total: fallbackEntries.length, has_more: false };
  }
}

export default async function ChangelogPage() {
  const data = await getChangelogEntries();

  return (
    <div className="min-h-screen bg-background">
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
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">Changelog</h1>
          <p className="text-muted-foreground">
            New features, improvements, and fixes in Repotoire
          </p>
        </div>

        <div className="grid gap-8 lg:grid-cols-[1fr_300px]">
          {/* Entries List */}
          <div>
            {data.entries.length > 0 ? (
              <ChangelogList
                initialEntries={data.entries}
                initialTotal={data.total}
                initialHasMore={data.has_more}
              />
            ) : (
              <div className="rounded-lg border bg-card p-8 text-center">
                <p className="text-muted-foreground">
                  No changelog entries yet. Check back soon!
                </p>
              </div>
            )}
          </div>

          {/* Sidebar */}
          <aside className="space-y-6">
            <ChangelogSubscribe />

            <div className="rounded-lg border bg-card p-6">
              <h3 className="font-semibold mb-3">Stay Updated</h3>
              <p className="text-sm text-muted-foreground mb-4">
                Get notified about new features and releases:
              </p>
              <ul className="space-y-2 text-sm">
                <li>
                  <Link
                    href="/changelog/rss"
                    className="text-primary hover:underline flex items-center gap-1"
                  >
                    <Rss className="h-4 w-4" />
                    RSS Feed
                  </Link>
                </li>
                <li>
                  <a
                    href="https://twitter.com/repotoire"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  >
                    Follow on Twitter
                  </a>
                </li>
              </ul>
            </div>
          </aside>
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
