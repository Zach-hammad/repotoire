"use client";

import { useState } from "react";
import { Search, Filter, ChevronDown, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ChangelogEntryCard } from "./changelog-entry-card";
import {
  ChangelogEntry,
  ChangelogCategory,
  getCategoryLabel,
  fetchChangelogEntries,
} from "@/lib/changelog-api";

interface ChangelogListProps {
  initialEntries: ChangelogEntry[];
  initialTotal: number;
  initialHasMore: boolean;
}

const CATEGORIES: (ChangelogCategory | null)[] = [
  null, // All
  "feature",
  "improvement",
  "fix",
  "breaking",
  "security",
  "deprecation",
];

export function ChangelogList({
  initialEntries,
  initialTotal,
  initialHasMore,
}: ChangelogListProps) {
  const [entries, setEntries] = useState<ChangelogEntry[]>(initialEntries);
  const [hasMore, setHasMore] = useState(initialHasMore);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState<ChangelogCategory | null>(null);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");

  const loadMore = async () => {
    setLoading(true);
    try {
      const result = await fetchChangelogEntries({
        offset: entries.length,
        limit: 20,
        category: category || undefined,
        search: search || undefined,
      });
      setEntries([...entries, ...result.entries]);
      setHasMore(result.has_more);
    } catch (error) {
      console.error("Failed to load more entries:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleCategoryChange = async (newCategory: ChangelogCategory | null) => {
    setCategory(newCategory);
    setLoading(true);
    try {
      const result = await fetchChangelogEntries({
        limit: 20,
        category: newCategory || undefined,
        search: search || undefined,
      });
      setEntries(result.entries);
      setHasMore(result.has_more);
    } catch (error) {
      console.error("Failed to filter entries:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
    setLoading(true);
    try {
      const result = await fetchChangelogEntries({
        limit: 20,
        category: category || undefined,
        search: searchInput || undefined,
      });
      setEntries(result.entries);
      setHasMore(result.has_more);
    } catch (error) {
      console.error("Failed to search entries:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-4">
        {/* Search */}
        <form onSubmit={handleSearch} className="flex-1 flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              type="search"
              placeholder="Search changelog..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="pl-9"
            />
          </div>
          <Button type="submit" variant="secondary">
            Search
          </Button>
        </form>

        {/* Category Filter */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" className="min-w-[140px]">
              <Filter className="h-4 w-4 mr-2" />
              {category ? getCategoryLabel(category) : "All Categories"}
              <ChevronDown className="h-4 w-4 ml-2" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {CATEGORIES.map((cat) => (
              <DropdownMenuItem
                key={cat || "all"}
                onClick={() => handleCategoryChange(cat)}
                className={category === cat ? "bg-accent" : ""}
              >
                {cat ? getCategoryLabel(cat) : "All Categories"}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Active filters */}
      {(category || search) && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Showing results for:</span>
          {category && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => handleCategoryChange(null)}
              className="h-6 px-2"
            >
              {getCategoryLabel(category)} &times;
            </Button>
          )}
          {search && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setSearch("");
                setSearchInput("");
                handleCategoryChange(category);
              }}
              className="h-6 px-2"
            >
              &quot;{search}&quot; &times;
            </Button>
          )}
        </div>
      )}

      {/* Entries */}
      {loading && entries.length === 0 ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : entries.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          <p>No changelog entries found.</p>
          {(category || search) && (
            <Button
              variant="link"
              onClick={() => {
                setCategory(null);
                setSearch("");
                setSearchInput("");
                handleCategoryChange(null);
              }}
            >
              Clear filters
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          {entries.map((entry) => (
            <ChangelogEntryCard key={entry.id} entry={entry} />
          ))}
        </div>
      )}

      {/* Load More */}
      {hasMore && (
        <div className="flex justify-center pt-4">
          <Button
            variant="outline"
            onClick={loadMore}
            disabled={loading}
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Loading...
              </>
            ) : (
              "Load More"
            )}
          </Button>
        </div>
      )}
    </div>
  );
}
