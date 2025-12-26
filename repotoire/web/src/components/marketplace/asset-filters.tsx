"use client";

import { useState, useEffect, useRef } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { AssetType, AssetSource, MarketplaceFilters } from "@/types/marketplace";

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => {
      setDebouncedValue(value);
    }, delay);

    return () => {
      clearTimeout(handler);
    };
  }, [value, delay]);

  return debouncedValue;
}

const typeOptions = [
  { value: "all", label: "All" },
  { value: "command", label: "Commands" },
  { value: "skill", label: "Skills" },
  { value: "style", label: "Styles" },
  { value: "hook", label: "Hooks" },
  { value: "prompt", label: "Prompts" },
] as const;

const sortOptions = [
  { value: "popular", label: "Popular" },
  { value: "recent", label: "Recent" },
  { value: "rating", label: "Rating" },
  { value: "name", label: "A-Z" },
] as const;

const sourceOptions = [
  { value: "all", label: "All Sources" },
  { value: "marketplace", label: "Official" },
  { value: "community", label: "Community" },
] as const;

interface AssetFiltersProps {
  filters: MarketplaceFilters;
  onFiltersChange: (filters: MarketplaceFilters) => void;
  className?: string;
}

export function AssetFilters({
  filters,
  onFiltersChange,
  className,
}: AssetFiltersProps) {
  const [searchValue, setSearchValue] = useState(filters.query || "");
  const debouncedSearch = useDebounce(searchValue, 300);
  const [selectedType, setSelectedType] = useState<string>(filters.type || "all");
  const [selectedSort, setSelectedSort] = useState<string>(filters.sort || "popular");
  const [selectedSource, setSelectedSource] = useState<string>(filters.source || "all");

  // Track previous debounced value to prevent infinite loops
  const prevDebouncedSearchRef = useRef(debouncedSearch);

  // Update filters when debounced search changes
  useEffect(() => {
    // Only update if the debounced value actually changed
    if (prevDebouncedSearchRef.current !== debouncedSearch) {
      prevDebouncedSearchRef.current = debouncedSearch;
      onFiltersChange({
        query: debouncedSearch || undefined,
        type: selectedType === "all" ? undefined : (selectedType as AssetType),
        source: selectedSource === "all" ? undefined : (selectedSource as AssetSource),
        sort: selectedSort as MarketplaceFilters["sort"],
      });
    }
  }, [debouncedSearch, selectedType, selectedSort, selectedSource, onFiltersChange]);

  const handleTypeChange = (type: string) => {
    setSelectedType(type);
    onFiltersChange({
      query: searchValue || undefined,
      type: type === "all" ? undefined : (type as AssetType),
      source: selectedSource === "all" ? undefined : (selectedSource as AssetSource),
      sort: selectedSort as MarketplaceFilters["sort"],
    });
  };

  const handleSortChange = (sort: string) => {
    setSelectedSort(sort);
    onFiltersChange({
      query: searchValue || undefined,
      type: selectedType === "all" ? undefined : (selectedType as AssetType),
      source: selectedSource === "all" ? undefined : (selectedSource as AssetSource),
      sort: sort as MarketplaceFilters["sort"],
    });
  };

  const handleSourceChange = (source: string) => {
    setSelectedSource(source);
    onFiltersChange({
      query: searchValue || undefined,
      type: selectedType === "all" ? undefined : (selectedType as AssetType),
      source: source === "all" ? undefined : (source as AssetSource),
      sort: selectedSort as MarketplaceFilters["sort"],
    });
  };

  return (
    <div className={cn("space-y-4", className)}>
      {/* Search Input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        <Input
          type="text"
          placeholder="Search commands, skills, and more..."
          value={searchValue}
          onChange={(e) => setSearchValue(e.target.value)}
          className="pl-10"
        />
      </div>

      {/* Type Pills & Sort */}
      <div className="flex flex-col sm:flex-row gap-4 justify-between">
        {/* Type Pills */}
        <div className="flex flex-wrap gap-1 bg-muted p-1 rounded-full">
          {typeOptions.map((option) => (
            <button
              key={option.value}
              onClick={() => handleTypeChange(option.value)}
              className={cn(
                "px-4 py-1.5 text-sm font-medium rounded-full transition-all",
                selectedType === option.value
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {option.label}
            </button>
          ))}
        </div>

        {/* Source & Sort Dropdowns */}
        <div className="flex gap-2">
          <Select value={selectedSource} onValueChange={handleSourceChange}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Source" />
            </SelectTrigger>
            <SelectContent>
              {sourceOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={selectedSort} onValueChange={handleSortChange}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Sort by" />
            </SelectTrigger>
            <SelectContent>
              {sortOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
