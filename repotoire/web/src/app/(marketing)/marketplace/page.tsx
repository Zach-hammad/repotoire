"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { AssetCard, AssetFilters } from "@/components/marketplace";
import { useMarketplaceBrowse, useFeaturedAssets } from "@/lib/marketplace-hooks";
import { MarketplaceFilters } from "@/types/marketplace";

export default function MarketplacePage() {
  const sectionRef = useRef<HTMLElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [filters, setFilters] = useState<MarketplaceFilters>({
    sort: "popular",
  });
  const [page, setPage] = useState(1);

  const { data: browseData, isLoading } = useMarketplaceBrowse(filters, page, 12);
  const { data: featuredAssets } = useFeaturedAssets();

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setIsVisible(true);
      },
      { threshold: 0.1 }
    );
    if (sectionRef.current) observer.observe(sectionRef.current);
    return () => observer.disconnect();
  }, []);

  const handleFiltersChange = useCallback((newFilters: MarketplaceFilters) => {
    setFilters(newFilters);
    setPage(1); // Reset to first page when filters change
  }, []);

  const assets = browseData?.items || [];
  const hasMore = browseData?.has_more || false;

  return (
    <section ref={sectionRef} className="py-24 px-4 sm:px-6 lg:px-8 dot-grid">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className={`text-center mb-12 opacity-0 ${isVisible ? "animate-fade-up" : ""}`}>
          <h1 className="text-4xl sm:text-5xl tracking-tight text-foreground mb-4">
            <span className="font-serif italic text-muted-foreground">AI Skills &</span>{" "}
            <span className="text-gradient font-display font-bold">Marketplace</span>
          </h1>
          <p className="text-muted-foreground max-w-xl mx-auto">
            Commands, skills, styles, and hooks for Claude Code and AI assistants.
          </p>
        </div>

        {/* Featured Section */}
        {featuredAssets && featuredAssets.length > 0 && !filters.query && !filters.type && (
          <div className={`mb-12 opacity-0 ${isVisible ? "animate-fade-up delay-100" : ""}`}>
            <h2 className="text-lg font-medium text-foreground mb-4">
              <span className="font-serif italic text-muted-foreground">Featured</span>
            </h2>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {featuredAssets.slice(0, 3).map((asset, i) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  animationDelay={150 + i * 50}
                  isVisible={isVisible}
                />
              ))}
            </div>
          </div>
        )}

        {/* Filters */}
        <div className={`mb-8 opacity-0 ${isVisible ? "animate-fade-up delay-200" : ""}`}>
          <AssetFilters filters={filters} onFiltersChange={handleFiltersChange} />
        </div>

        {/* Loading State */}
        {isLoading && (
          <div className="flex justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Asset Grid */}
        {!isLoading && assets.length > 0 && (
          <>
            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {assets.map((asset, i) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  animationDelay={200 + i * 50}
                  isVisible={isVisible}
                />
              ))}
            </div>

            {/* Load More */}
            {hasMore && (
              <div className="flex justify-center mt-8">
                <button
                  onClick={() => setPage((p) => p + 1)}
                  className="px-6 py-2 text-sm font-display font-medium text-foreground bg-muted hover:bg-muted/80 rounded-full transition-colors"
                >
                  Load More
                </button>
              </div>
            )}
          </>
        )}

        {/* Empty State */}
        {!isLoading && assets.length === 0 && (
          <div className="text-center py-16">
            <p className="text-muted-foreground">
              No assets found matching your criteria.
            </p>
          </div>
        )}

        {/* Stats */}
        {browseData && (
          <div className={`mt-8 text-center text-sm text-muted-foreground opacity-0 ${isVisible ? "animate-fade-up delay-300" : ""}`}>
            Showing {assets.length} of {browseData.total} assets
          </div>
        )}
      </div>
    </section>
  );
}
