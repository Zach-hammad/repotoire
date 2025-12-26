import * as React from "react"
import { LucideIcon } from "lucide-react"
import Link from "next/link"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  icon?: LucideIcon
  title: string
  description?: string
  action?: {
    label: string
    href?: string
    onClick?: () => void
    variant?: "default" | "outline" | "secondary" | "ghost"
  }
  secondaryAction?: {
    label: string
    href?: string
    onClick?: () => void
  }
  size?: "sm" | "default" | "lg"
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  size = "default",
  className,
  ...props
}: EmptyStateProps) {
  const sizeClasses = {
    sm: {
      container: "py-6",
      iconWrapper: "h-10 w-10",
      icon: "h-5 w-5",
      title: "text-sm font-medium",
      description: "text-xs",
      maxWidth: "max-w-[250px]",
    },
    default: {
      container: "py-12",
      iconWrapper: "h-14 w-14",
      icon: "h-7 w-7",
      title: "text-lg font-semibold",
      description: "text-sm",
      maxWidth: "max-w-sm",
    },
    lg: {
      container: "py-16",
      iconWrapper: "h-16 w-16",
      icon: "h-8 w-8",
      title: "text-xl font-semibold",
      description: "text-base",
      maxWidth: "max-w-md",
    },
  }

  const sizes = sizeClasses[size]

  const ActionButton = action ? (
    action.href ? (
      <Link href={action.href}>
        <Button variant={action.variant || "default"} size={size === "sm" ? "sm" : "default"}>
          {action.label}
        </Button>
      </Link>
    ) : (
      <Button
        variant={action.variant || "default"}
        size={size === "sm" ? "sm" : "default"}
        onClick={action.onClick}
      >
        {action.label}
      </Button>
    )
  ) : null

  const SecondaryButton = secondaryAction ? (
    secondaryAction.href ? (
      <Link
        href={secondaryAction.href}
        className="text-sm text-muted-foreground hover:text-foreground hover:underline transition-colors"
      >
        {secondaryAction.label}
      </Link>
    ) : (
      <button
        onClick={secondaryAction.onClick}
        className="text-sm text-muted-foreground hover:text-foreground hover:underline transition-colors"
      >
        {secondaryAction.label}
      </button>
    )
  ) : null

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        sizes.container,
        className
      )}
      {...props}
    >
      {Icon && (
        <div
          className={cn(
            "mb-4 flex items-center justify-center rounded-full bg-muted",
            sizes.iconWrapper
          )}
        >
          <Icon className={cn("text-muted-foreground", sizes.icon)} />
        </div>
      )}
      <h3 className={cn("mb-2", sizes.title)}>{title}</h3>
      {description && (
        <p className={cn("text-muted-foreground mb-6 mx-auto", sizes.description, sizes.maxWidth)}>
          {description}
        </p>
      )}
      {(action || secondaryAction) && (
        <div className="flex flex-col items-center gap-3">
          {ActionButton}
          {SecondaryButton}
        </div>
      )}
    </div>
  )
}

// Convenience components for common empty states
export function NoDataEmptyState({
  title = "No data yet",
  description = "Data will appear here once available",
  ...props
}: Partial<EmptyStateProps>) {
  return <EmptyState title={title} description={description} {...props} />
}

export function NoResultsEmptyState({
  title = "No results found",
  description = "Try adjusting your filters or search criteria",
  onClear,
  ...props
}: Partial<EmptyStateProps> & { onClear?: () => void }) {
  return (
    <EmptyState
      title={title}
      description={description}
      action={onClear ? { label: "Clear filters", onClick: onClear, variant: "outline" } : undefined}
      {...props}
    />
  )
}

export function ErrorEmptyState({
  title = "Something went wrong",
  description = "We couldn't load this content. Please try again.",
  onRetry,
  ...props
}: Partial<EmptyStateProps> & { onRetry?: () => void }) {
  return (
    <EmptyState
      title={title}
      description={description}
      action={onRetry ? { label: "Try again", onClick: onRetry } : undefined}
      {...props}
    />
  )
}
