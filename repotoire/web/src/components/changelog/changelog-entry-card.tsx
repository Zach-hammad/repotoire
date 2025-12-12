import Link from "next/link";
import {
  Sparkles,
  TrendingUp,
  Bug,
  AlertTriangle,
  Shield,
  Archive,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  ChangelogEntry,
  ChangelogCategory,
  getCategoryLabel,
  getCategoryColor,
  formatRelativeDate,
} from "@/lib/changelog-api";

interface ChangelogEntryCardProps {
  entry: ChangelogEntry;
}

function CategoryIcon({ category }: { category: ChangelogCategory }) {
  const className = "h-3.5 w-3.5";
  switch (category) {
    case "feature":
      return <Sparkles className={className} />;
    case "improvement":
      return <TrendingUp className={className} />;
    case "fix":
      return <Bug className={className} />;
    case "breaking":
      return <AlertTriangle className={className} />;
    case "security":
      return <Shield className={className} />;
    case "deprecation":
      return <Archive className={className} />;
    default:
      return null;
  }
}

export function ChangelogEntryCard({ entry }: ChangelogEntryCardProps) {
  return (
    <Link
      href={`/changelog/${entry.slug}`}
      className="block group"
    >
      <article className="rounded-lg border bg-card p-6 transition-colors hover:bg-accent/50">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            {/* Header with version, category, and date */}
            <div className="flex flex-wrap items-center gap-2 mb-2">
              {entry.version && (
                <span className="text-sm font-mono text-muted-foreground">
                  {entry.version}
                </span>
              )}
              <Badge
                variant="outline"
                className={`${getCategoryColor(entry.category)} flex items-center gap-1`}
              >
                <CategoryIcon category={entry.category} />
                {getCategoryLabel(entry.category)}
              </Badge>
              {entry.is_major && (
                <Badge variant="default" className="bg-primary">
                  Major Release
                </Badge>
              )}
            </div>

            {/* Title */}
            <h3 className="text-lg font-semibold group-hover:text-primary transition-colors mb-2">
              {entry.title}
            </h3>

            {/* Summary */}
            <p className="text-muted-foreground text-sm line-clamp-2">
              {entry.summary}
            </p>
          </div>

          {/* Date */}
          {entry.published_at && (
            <time
              dateTime={entry.published_at}
              className="text-sm text-muted-foreground whitespace-nowrap"
            >
              {formatRelativeDate(entry.published_at)}
            </time>
          )}
        </div>

        {/* Image preview if available */}
        {entry.image_url && (
          <div className="mt-4 rounded-md overflow-hidden border">
            <img
              src={entry.image_url}
              alt={entry.title}
              className="w-full h-40 object-cover"
            />
          </div>
        )}
      </article>
    </Link>
  );
}
