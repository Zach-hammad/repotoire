import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const cardVariants = cva(
  "flex flex-col rounded-xl border shadow-card",
  {
    variants: {
      size: {
        compact: "gap-3 py-4",
        default: "gap-6 py-6",
        spacious: "gap-8 py-8",
      },
      variant: {
        default: "bg-card text-card-foreground",
        elevated: "card-elevated bg-card text-card-foreground",
        holographic: "card-holographic text-card-foreground",
        critical: "card-critical text-card-foreground",
        diagnostic: "card-diagnostic bg-card text-card-foreground",
      },
      glow: {
        none: "",
        primary: "box-glow-primary",
        cyan: "box-glow-cyan",
        good: "box-glow-good",
        warning: "box-glow-warning",
        critical: "box-glow-critical",
      },
    },
    defaultVariants: {
      size: "default",
      variant: "default",
      glow: "none",
    },
  }
)

// Size-to-padding mapping for child components
const sizePadding = {
  compact: "px-4",
  default: "px-6",
  spacious: "px-8",
} as const

function Card({
  className,
  size = "default",
  variant = "default",
  glow = "none",
  glowAnimate = false,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof cardVariants> & { glowAnimate?: boolean }) {
  return (
    <div
      data-slot="card"
      data-size={size}
      data-variant={variant}
      className={cn(
        cardVariants({ size, variant, glow, className }),
        glowAnimate && "box-glow-animate"
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "@container/card-header grid auto-rows-min grid-rows-[auto_auto] items-start gap-2 px-6 has-data-[slot=card-action]:grid-cols-[1fr_auto] [.border-b]:pb-6",
        // Size-based padding via parent data attribute
        "[[data-size=compact]_&]:px-4 [[data-size=spacious]_&]:px-8",
        className
      )}
      {...props}
    />
  )
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("leading-none font-semibold", className)}
      {...props}
    />
  )
}

function CardDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn("text-muted-foreground text-sm", className)}
      {...props}
    />
  )
}

function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn(
        "px-6",
        // Size-based padding via parent data attribute
        "[[data-size=compact]_&]:px-4 [[data-size=spacious]_&]:px-8",
        className
      )}
      {...props}
    />
  )
}

function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn(
        "flex items-center px-6 [.border-t]:pt-6",
        // Size-based padding via parent data attribute
        "[[data-size=compact]_&]:px-4 [[data-size=spacious]_&]:px-8",
        className
      )}
      {...props}
    />
  )
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
}
