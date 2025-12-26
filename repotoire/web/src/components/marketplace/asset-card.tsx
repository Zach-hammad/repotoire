"use client";

import Link from "next/link";
import { Download, Star, BadgeCheck, Users } from "lucide-react";
import { cn } from "@/lib/utils";
import { AssetSummary, AssetType } from "@/types/marketplace";
import { PricingBadge } from "./pricing-badge";

const typeColorMap: Record<AssetType, string> = {
  command: "bg-purple-500/10 border-purple-500/20 text-purple-400",
  skill: "bg-blue-500/10 border-blue-500/20 text-blue-400",
  style: "bg-teal-500/10 border-teal-500/20 text-teal-400",
  hook: "bg-orange-500/10 border-orange-500/20 text-orange-400",
  prompt: "bg-pink-500/10 border-pink-500/20 text-pink-400",
};

const dotColorMap: Record<AssetType, string> = {
  command: "bg-purple-500",
  skill: "bg-blue-500",
  style: "bg-teal-500",
  hook: "bg-orange-500",
  prompt: "bg-pink-500",
};

function formatInstalls(count: number): string {
  if (count >= 1000000) {
    return `${(count / 1000000).toFixed(1)}M`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}k`;
  }
  return count.toString();
}

interface AssetCardProps {
  asset: AssetSummary;
  animationDelay?: number;
  isVisible?: boolean;
  className?: string;
}

export function AssetCard({
  asset,
  animationDelay = 0,
  isVisible = true,
  className,
}: AssetCardProps) {
  return (
    <Link
      href={`/marketplace/${asset.publisher_slug}/${asset.slug}`}
      className={cn(
        "card-elevated rounded-xl p-5 opacity-0 hover:border-border/80 transition-colors block",
        isVisible && "animate-scale-in",
        className
      )}
      style={{ animationDelay: `${animationDelay}ms` }}
    >
      {/* Header: Name + Publisher */}
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("w-2 h-2 rounded-full", dotColorMap[asset.type])} />
          <h3 className="text-sm font-medium text-foreground line-clamp-1">
            {asset.name}
          </h3>
          {asset.verified && (
            <BadgeCheck className="w-4 h-4 text-blue-500 shrink-0" />
          )}
        </div>
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <span className="hover:text-foreground transition-colors">
            @{asset.publisher_slug}
          </span>
          {asset.publisher_verified && (
            <BadgeCheck className="w-3 h-3 text-blue-500" />
          )}
        </div>
      </div>

      {/* Description */}
      <p className="text-sm text-muted-foreground line-clamp-2 mb-4">
        {asset.description}
      </p>

      {/* Badges */}
      <div className="flex flex-wrap gap-2 mb-4">
        {/* Type badge */}
        <code
          className={cn(
            "inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono",
            typeColorMap[asset.type]
          )}
        >
          {asset.type}
        </code>
        {/* Community badge */}
        {asset.source === "community" && (
          <span className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md border bg-emerald-500/10 border-emerald-500/20 text-emerald-400">
            <Users className="w-3 h-3" />
            Community
          </span>
        )}
        {/* Pricing badge */}
        <PricingBadge type={asset.pricing_type} priceCents={asset.price_cents} />
      </div>

      {/* Footer: Stats */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <div className="flex items-center gap-1">
          <Star className="w-3.5 h-3.5 text-amber-500" />
          <span>{asset.rating_avg.toFixed(1)}</span>
        </div>
        <div className="flex items-center gap-1">
          <Download className="w-3.5 h-3.5" />
          <span>{formatInstalls(asset.install_count)}</span>
        </div>
        <span className="text-xs">v{asset.latest_version}</span>
      </div>
    </Link>
  );
}

export { formatInstalls };
