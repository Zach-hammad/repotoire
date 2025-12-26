"use client";

import { cn } from "@/lib/utils";
import { PricingType } from "@/types/marketplace";

const pricingStyles: Record<PricingType, string> = {
  free: "bg-emerald-500/10 border-emerald-500/20 text-emerald-400",
  freemium: "bg-blue-500/10 border-blue-500/20 text-blue-400",
  paid: "bg-amber-500/10 border-amber-500/20 text-amber-400",
};

interface PricingBadgeProps {
  type: PricingType;
  priceCents?: number;
  className?: string;
}

export function PricingBadge({ type, priceCents, className }: PricingBadgeProps) {
  const formatPrice = (cents: number) => {
    return `$${(cents / 100).toFixed(2)}`;
  };

  const label = type === "paid" && priceCents ? formatPrice(priceCents) : type;

  return (
    <code
      className={cn(
        "inline-block text-xs px-2.5 py-1.5 rounded-md border font-mono capitalize",
        pricingStyles[type],
        className
      )}
    >
      {label}
    </code>
  );
}
