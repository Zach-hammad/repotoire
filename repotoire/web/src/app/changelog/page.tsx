import { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { Rss } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChangelogList, ChangelogSubscribe } from "@/components/changelog";
import { fetchChangelogEntries } from "@/lib/changelog-api";

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
    return await fetchChangelogEntries({ limit: 20 });
  } catch (error) {
    console.error("Failed to fetch changelog entries:", error);
    return { entries: [], total: 0, has_more: false };
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
