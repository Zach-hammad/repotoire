"use client";

import { useRef, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import {
  ArrowLeft,
  BadgeCheck,
  Download,
  Star,
  ExternalLink,
  GitBranch,
  FileText,
  Tag,
  Calendar,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  ClaudeExportButton,
  ClaudeAICard,
  InstallButton,
  PricingBadge,
  ReviewsSection,
  formatInstalls,
} from "@/components/marketplace";
import { useAssetDetail, useIsAssetInstalled } from "@/lib/marketplace-hooks";
import { AssetType } from "@/types/marketplace";

const typeColorMap: Record<AssetType, string> = {
  command: "bg-purple-500/10 border-purple-500/20 text-purple-400",
  skill: "bg-blue-500/10 border-blue-500/20 text-blue-400",
  style: "bg-teal-500/10 border-teal-500/20 text-teal-400",
  hook: "bg-orange-500/10 border-orange-500/20 text-orange-400",
  prompt: "bg-pink-500/10 border-pink-500/20 text-pink-400",
};

function formatDate(dateString: string) {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export default function AssetDetailPage() {
  const params = useParams();
  const sectionRef = useRef<HTMLElement>(null);
  const [isVisible, setIsVisible] = useState(false);

  // Extract publisher and slug from params
  // The @ is part of the folder name, so publisher comes without @
  const publisherSlug = (params.publisher as string)?.replace("@", "") || "";
  const assetSlug = params.slug as string;

  const { data: asset, isLoading, error } = useAssetDetail(
    publisherSlug,
    assetSlug
  );
  const { isInstalled, installedAsset } = useIsAssetInstalled(
    publisherSlug,
    assetSlug
  );

  useEffect(() => {
    // Set visible immediately on mount since detail page is always in viewport
    // This fixes hydration issues where IntersectionObserver doesn't fire
    setIsVisible(true);
  }, []);

  if (isLoading) {
    return (
      <section className="py-24 px-4 sm:px-6 lg:px-8">
        <div className="max-w-6xl mx-auto">
          <div className="animate-pulse">
            <div className="h-8 w-48 bg-muted rounded mb-4" />
            <div className="h-4 w-96 bg-muted rounded mb-8" />
            <div className="grid lg:grid-cols-3 gap-8">
              <div className="lg:col-span-2">
                <div className="h-96 bg-muted rounded-xl" />
              </div>
              <div className="space-y-4">
                <div className="h-32 bg-muted rounded-xl" />
                <div className="h-32 bg-muted rounded-xl" />
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (error || !asset) {
    return (
      <section className="py-24 px-4 sm:px-6 lg:px-8">
        <div className="max-w-6xl mx-auto text-center">
          <h1 className="text-2xl font-display font-bold text-foreground mb-4">
            Asset Not Found
          </h1>
          <p className="text-muted-foreground mb-8">
            The asset you're looking for doesn't exist or has been removed.
          </p>
          <Button asChild>
            <Link href="/marketplace">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to Marketplace
            </Link>
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section ref={sectionRef} className="py-24 px-4 sm:px-6 lg:px-8">
      <div className="max-w-6xl mx-auto">
        {/* Back Link */}
        <Link
          href="/marketplace"
          className={cn(
            "inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors mb-8 opacity-0",
            isVisible && "animate-fade-up"
          )}
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Marketplace
        </Link>

        <div className="grid lg:grid-cols-3 gap-8">
          {/* Main Content (2/3) */}
          <div className="lg:col-span-2 space-y-6">
            {/* Header */}
            <div className={cn("opacity-0", isVisible && "animate-fade-up delay-100")}>
              <div className="flex items-start justify-between mb-4">
                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <h1 className="text-2xl font-display font-bold text-foreground">
                      {asset.name}
                    </h1>
                    {asset.verified && (
                      <BadgeCheck className="w-5 h-5 text-blue-500" />
                    )}
                  </div>
                  <Link
                    href={`/marketplace?publisher=${asset.publisher_slug}`}
                    className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                  >
                    by @{asset.publisher_slug}
                    {asset.publisher.verified && (
                      <BadgeCheck className="w-3.5 h-3.5 inline-block ml-1 text-blue-500" />
                    )}
                  </Link>
                </div>
                <div className="flex items-center gap-2">
                  <ClaudeExportButton asset={asset} size="default" />
                  <InstallButton
                    publisherSlug={publisherSlug}
                    assetSlug={assetSlug}
                    isInstalled={isInstalled}
                    hasUpdate={installedAsset?.has_update}
                    isCommunity={publisherSlug === 'community'}
                    homepage={(asset as any).homepage}
                  />
                </div>
              </div>

              <p className="text-muted-foreground mb-4">{asset.description}</p>

              {/* Badges */}
              <div className="flex flex-wrap gap-2 mb-4">
                <code
                  className={cn(
                    "inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono",
                    typeColorMap[asset.type]
                  )}
                >
                  {asset.type}
                </code>
                <PricingBadge
                  type={asset.pricing_type}
                  priceCents={asset.price_cents}
                />
              </div>

              {/* Stats Row */}
              <div className="flex items-center gap-6 text-sm text-muted-foreground">
                <div className="flex items-center gap-1">
                  <Star className="w-4 h-4 text-amber-500" />
                  <span className="font-medium text-foreground">
                    {asset.rating_avg.toFixed(1)}
                  </span>
                  <span>({asset.rating_count} reviews)</span>
                </div>
                <div className="flex items-center gap-1">
                  <Download className="w-4 h-4" />
                  <span>{formatInstalls(asset.install_count)} installs</span>
                </div>
              </div>
            </div>

            {/* README */}
            <div
              className={cn(
                "card-elevated rounded-xl p-6 opacity-0",
                isVisible && "animate-fade-up delay-200"
              )}
            >
              <h2 className="text-lg font-medium text-foreground mb-4">
                Documentation
              </h2>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown>{asset.readme || "No documentation available."}</ReactMarkdown>
              </div>
            </div>

            {/* Reviews */}
            <div className={cn("opacity-0", isVisible && "animate-fade-up delay-300")}>
              <ReviewsSection
                publisherSlug={publisherSlug}
                assetSlug={assetSlug}
                ratingAvg={asset.rating_avg}
                ratingCount={asset.rating_count}
              />
            </div>
          </div>

          {/* Sidebar (1/3) */}
          <div className="space-y-4">
            {/* Version Card */}
            <div
              className={cn(
                "card-elevated rounded-xl p-5 opacity-0",
                isVisible && "animate-fade-up delay-200"
              )}
            >
              <h3 className="text-sm font-medium text-foreground mb-4">Version</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Latest</span>
                  <span className="text-sm font-mono text-foreground">
                    v{asset.latest_version}
                  </span>
                </div>
                {asset.versions[0] && (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">Released</span>
                      <span className="text-sm text-foreground">
                        {formatDate(asset.versions[0].created_at)}
                      </span>
                    </div>
                    {asset.versions[0].changelog && (
                      <div className="pt-3 border-t border-border">
                        <p className="text-xs text-muted-foreground">
                          {asset.versions[0].changelog}
                        </p>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>

            {/* Links Card - only show if there are links */}
            {(asset.repository_url || asset.documentation_url || asset.license || (asset as any).homepage) && (
              <div
                className={cn(
                  "card-elevated rounded-xl p-5 opacity-0",
                  isVisible && "animate-fade-up delay-300"
                )}
              >
                <h3 className="text-sm font-medium text-foreground mb-4">Links</h3>
                <div className="space-y-2">
                  {(asset as any).homepage && (
                    <a
                      href={(asset as any).homepage}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <ExternalLink className="w-4 h-4" />
                      Homepage
                      <ExternalLink className="w-3 h-3 ml-auto" />
                    </a>
                  )}
                  {asset.repository_url && (
                    <a
                      href={asset.repository_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <GitBranch className="w-4 h-4" />
                      Repository
                      <ExternalLink className="w-3 h-3 ml-auto" />
                    </a>
                  )}
                  {asset.documentation_url && (
                    <a
                      href={asset.documentation_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <FileText className="w-4 h-4" />
                      Documentation
                      <ExternalLink className="w-3 h-3 ml-auto" />
                    </a>
                  )}
                  {asset.license && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <FileText className="w-4 h-4" />
                      <span>License: {asset.license}</span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Tags Card */}
            {asset.tags.length > 0 && (
              <div
                className={cn(
                  "card-elevated rounded-xl p-5 opacity-0",
                  isVisible && "animate-fade-up delay-400"
                )}
              >
                <h3 className="text-sm font-medium text-foreground mb-4">Tags</h3>
                <div className="flex flex-wrap gap-2">
                  {asset.tags.map((tag) => (
                    <Link
                      key={tag}
                      href={`/marketplace?tags=${tag}`}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground bg-muted hover:bg-muted/80 rounded-full transition-colors"
                    >
                      <Tag className="w-3 h-3" />
                      {tag}
                    </Link>
                  ))}
                </div>
              </div>
            )}

            {/* Claude.ai Card - only for styles and prompts */}
            <ClaudeAICard
              asset={asset}
              className={cn(
                "opacity-0",
                isVisible && "animate-fade-up delay-450"
              )}
            />

            {/* Publisher Card */}
            <div
              className={cn(
                "card-elevated rounded-xl p-5 opacity-0",
                isVisible && "animate-fade-up delay-500"
              )}
            >
              <h3 className="text-sm font-medium text-foreground mb-4">Publisher</h3>
              <div className="flex items-center gap-3">
                {asset.publisher.avatar_url ? (
                  <img
                    src={asset.publisher.avatar_url}
                    alt={asset.publisher.name}
                    className="w-10 h-10 rounded-full"
                  />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center">
                    <span className="text-sm font-medium text-muted-foreground">
                      {asset.publisher.name.charAt(0).toUpperCase()}
                    </span>
                  </div>
                )}
                <div>
                  <div className="flex items-center gap-1">
                    <span className="text-sm font-medium text-foreground">
                      {asset.publisher.display_name}
                    </span>
                    {asset.publisher.verified && (
                      <BadgeCheck className="w-4 h-4 text-blue-500" />
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {asset.publisher.asset_count} assets
                  </span>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-border">
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Calendar className="w-3.5 h-3.5" />
                  <span>
                    Joined {formatDate(asset.publisher.created_at)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
