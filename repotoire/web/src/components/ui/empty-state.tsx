"use client"

import * as React from "react"
import { LucideIcon, PartyPopper, CheckCircle2, Search, AlertCircle, Sparkles, GitBranch, FolderOpen, Plus, RefreshCw, ArrowRight } from "lucide-react"
import Link from "next/link"
import { motion, AnimatePresence } from "framer-motion"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

type EmptyStateVariant = "default" | "success" | "celebration" | "search" | "error" | "getting-started" | "no-repos" | "empty-folder"

interface VariantConfig {
  icon: LucideIcon
  iconColor: string
  bgColor: string
  defaultTitle: string
  defaultDescription: string
}

const variantConfigs: Record<EmptyStateVariant, VariantConfig> = {
  default: {
    icon: FolderOpen,
    iconColor: "text-muted-foreground",
    bgColor: "bg-muted",
    defaultTitle: "No data yet",
    defaultDescription: "Data will appear here once available",
  },
  success: {
    icon: CheckCircle2,
    iconColor: "text-emerald-500",
    bgColor: "bg-emerald-500/10",
    defaultTitle: "All done!",
    defaultDescription: "Everything is in order",
  },
  celebration: {
    icon: PartyPopper,
    iconColor: "text-amber-500",
    bgColor: "bg-amber-500/10",
    defaultTitle: "Congratulations!",
    defaultDescription: "You've achieved something great",
  },
  search: {
    icon: Search,
    iconColor: "text-muted-foreground",
    bgColor: "bg-muted",
    defaultTitle: "No results found",
    defaultDescription: "Try adjusting your search or filter criteria",
  },
  error: {
    icon: AlertCircle,
    iconColor: "text-red-500",
    bgColor: "bg-red-500/10",
    defaultTitle: "Something went wrong",
    defaultDescription: "Please try again or contact support",
  },
  "getting-started": {
    icon: Sparkles,
    iconColor: "text-primary",
    bgColor: "bg-primary/10",
    defaultTitle: "Get started",
    defaultDescription: "Set up your first project",
  },
  "no-repos": {
    icon: GitBranch,
    iconColor: "text-primary",
    bgColor: "bg-primary/10",
    defaultTitle: "No repositories connected",
    defaultDescription: "Connect a repository to start analyzing",
  },
  "empty-folder": {
    icon: FolderOpen,
    iconColor: "text-blue-500",
    bgColor: "bg-blue-500/10",
    defaultTitle: "Folder is empty",
    defaultDescription: "Add files to get started",
  },
}

interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  icon?: LucideIcon
  title?: string
  description?: string
  action?: {
    label: string
    href?: string
    onClick?: () => void
    variant?: "default" | "outline" | "secondary" | "ghost"
    icon?: LucideIcon
  }
  secondaryAction?: {
    label: string
    href?: string
    onClick?: () => void
  }
  size?: "sm" | "default" | "lg"
  variant?: EmptyStateVariant
  animated?: boolean
  /** Heading level for accessibility - defaults to h3 */
  headingLevel?: "h2" | "h3" | "h4"
}

export function EmptyState({
  icon: CustomIcon,
  title,
  description,
  action,
  secondaryAction,
  size = "default",
  variant = "default",
  animated = true,
  headingLevel = "h3",
  className,
  children,
  ...props
}: EmptyStateProps) {
  const config = variantConfigs[variant]
  const Icon = CustomIcon ?? config.icon
  const finalTitle = title ?? config.defaultTitle
  const finalDescription = description ?? config.defaultDescription

  const sizeClasses = {
    sm: {
      container: "py-6",
      iconWrapper: "h-12 w-12",
      icon: "h-6 w-6",
      title: "text-sm font-medium",
      description: "text-xs",
      maxWidth: "max-w-[250px]",
    },
    default: {
      container: "py-12",
      iconWrapper: "h-16 w-16",
      icon: "h-8 w-8",
      title: "text-lg font-semibold",
      description: "text-sm",
      maxWidth: "max-w-sm",
    },
    lg: {
      container: "py-16",
      iconWrapper: "h-20 w-20",
      icon: "h-10 w-10",
      title: "text-xl font-semibold",
      description: "text-base",
      maxWidth: "max-w-md",
    },
  }

  const sizes = sizeClasses[size]
  const HeadingTag = headingLevel

  // Animation variants
  const containerVariants = {
    hidden: { opacity: 0 },
    visible: {
      opacity: 1,
      transition: { staggerChildren: 0.1, delayChildren: 0.1 },
    },
  }

  const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    visible: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] as const },
    },
  }

  const ActionIcon = action?.icon
  const ActionButton = action ? (
    action.href ? (
      <Link href={action.href}>
        <Button variant={action.variant || "default"} size={size === "sm" ? "sm" : "default"}>
          {ActionIcon && <ActionIcon className="h-4 w-4 mr-2" />}
          {action.label}
          {!ActionIcon && <ArrowRight className="h-4 w-4 ml-2" />}
        </Button>
      </Link>
    ) : (
      <Button
        variant={action.variant || "default"}
        size={size === "sm" ? "sm" : "default"}
        onClick={action.onClick}
      >
        {ActionIcon && <ActionIcon className="h-4 w-4 mr-2" />}
        {action.label}
        {!ActionIcon && <ArrowRight className="h-4 w-4 ml-2" />}
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

  const content = (
    <>
      {/* Icon with animated background */}
      {animated ? (
        <motion.div variants={itemVariants} className="relative mb-4">
          <motion.div
            className={cn(
              "flex items-center justify-center rounded-full",
              sizes.iconWrapper,
              config.bgColor
            )}
            animate={
              variant === "celebration"
                ? { scale: [1, 1.1, 1], rotate: [0, 5, -5, 0] }
                : undefined
            }
            transition={
              variant === "celebration"
                ? { duration: 2, repeat: Infinity, repeatDelay: 3 }
                : undefined
            }
          >
            <Icon className={cn(sizes.icon, config.iconColor)} />
          </motion.div>

          {/* Decorative rings for celebration */}
          {variant === "celebration" && (
            <>
              <motion.div
                className="absolute inset-0 rounded-full border-2 border-amber-500/30"
                animate={{ scale: [1, 1.5], opacity: [0.5, 0] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
              <motion.div
                className="absolute inset-0 rounded-full border-2 border-amber-500/20"
                animate={{ scale: [1, 2], opacity: [0.3, 0] }}
                transition={{ duration: 1.5, repeat: Infinity, delay: 0.2 }}
              />
            </>
          )}

          {/* Success sparkles */}
          {variant === "success" && (
            <>
              {[...Array(3)].map((_, i) => (
                <motion.div
                  key={i}
                  className="absolute h-1 w-1 rounded-full bg-emerald-400"
                  style={{
                    top: `${20 + i * 20}%`,
                    left: `${10 + i * 30}%`,
                  }}
                  animate={{
                    scale: [0, 1, 0],
                    opacity: [0, 1, 0],
                    y: [0, -15],
                  }}
                  transition={{
                    duration: 1.2,
                    repeat: Infinity,
                    delay: i * 0.4,
                    repeatDelay: 2,
                  }}
                />
              ))}
            </>
          )}

          {/* Celebration sparkles */}
          {variant === "celebration" && (
            <>
              {[...Array(5)].map((_, i) => (
                <motion.div
                  key={i}
                  className="absolute h-1.5 w-1.5 rounded-full"
                  style={{
                    backgroundColor: ["#fbbf24", "#f59e0b", "#ef4444", "#8b5cf6", "#3b82f6"][i],
                    top: `${10 + i * 15}%`,
                    left: `${10 + i * 15}%`,
                  }}
                  animate={{
                    scale: [0, 1.5, 0],
                    opacity: [0, 1, 0],
                    y: [0, -25],
                  }}
                  transition={{
                    duration: 1.5,
                    repeat: Infinity,
                    delay: i * 0.3,
                    repeatDelay: 2.5,
                  }}
                />
              ))}
            </>
          )}
        </motion.div>
      ) : (
        <div className="relative mb-4">
          <div
            className={cn(
              "flex items-center justify-center rounded-full",
              sizes.iconWrapper,
              config.bgColor
            )}
          >
            <Icon className={cn(sizes.icon, config.iconColor)} />
          </div>
        </div>
      )}

      {animated ? (
        <motion.div variants={itemVariants}>
          <HeadingTag className={cn("mb-2", sizes.title)}>{finalTitle}</HeadingTag>
        </motion.div>
      ) : (
        <HeadingTag className={cn("mb-2", sizes.title)}>{finalTitle}</HeadingTag>
      )}

      {finalDescription && (
        animated ? (
          <motion.div variants={itemVariants}>
            <p className={cn("text-muted-foreground mb-6 mx-auto", sizes.description, sizes.maxWidth)}>
              {finalDescription}
            </p>
          </motion.div>
        ) : (
          <p className={cn("text-muted-foreground mb-6 mx-auto", sizes.description, sizes.maxWidth)}>
            {finalDescription}
          </p>
        )
      )}

      {children && (
        animated ? (
          <motion.div variants={itemVariants}>
            {children}
          </motion.div>
        ) : (
          <>{children}</>
        )
      )}

      {(action || secondaryAction) && (
        animated ? (
          <motion.div variants={itemVariants} className="flex flex-col items-center gap-3">
            {ActionButton}
            {SecondaryButton}
          </motion.div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            {ActionButton}
            {SecondaryButton}
          </div>
        )
      )}
    </>
  )

  if (animated) {
    return (
      <motion.div
        className={cn(
          "flex flex-col items-center justify-center text-center",
          sizes.container,
          className
        )}
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {content}
      </motion.div>
    )
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        sizes.container,
        className
      )}
    >
      {content}
    </div>
  )
}

// Convenience components for common empty states
export function NoDataEmptyState({
  title,
  description,
  ...props
}: Partial<EmptyStateProps>) {
  return <EmptyState variant="default" title={title} description={description} {...props} />
}

export function NoResultsEmptyState({
  title,
  description,
  onClear,
  ...props
}: Partial<EmptyStateProps> & { onClear?: () => void }) {
  return (
    <EmptyState
      variant="search"
      title={title}
      description={description}
      action={onClear ? { label: "Clear filters", onClick: onClear, variant: "outline" } : undefined}
      {...props}
    />
  )
}

export function ErrorEmptyState({
  title,
  description,
  onRetry,
  ...props
}: Partial<EmptyStateProps> & { onRetry?: () => void }) {
  return (
    <EmptyState
      variant="error"
      title={title}
      description={description}
      action={onRetry ? { label: "Try again", onClick: onRetry, icon: RefreshCw, variant: "outline" } : undefined}
      {...props}
    />
  )
}

export function NoFindingsEmptyState(props: Partial<EmptyStateProps>) {
  return (
    <EmptyState
      variant="celebration"
      title="No issues found!"
      description="Your codebase is looking healthy. Keep up the great work!"
      {...props}
    />
  )
}

export function NoReposEmptyState({
  onConnect,
  ...props
}: Partial<EmptyStateProps> & { onConnect?: () => void }) {
  return (
    <EmptyState
      variant="no-repos"
      title="No repositories connected"
      description="Connect a GitHub repository to start analyzing your code health."
      action={onConnect ? { label: "Connect Repository", onClick: onConnect, icon: Plus } : undefined}
      {...props}
    />
  )
}

export function GettingStartedEmptyState({
  onStart,
  ...props
}: Partial<EmptyStateProps> & { onStart?: () => void }) {
  return (
    <EmptyState
      variant="getting-started"
      title="Welcome to Repotoire!"
      description="Start by connecting your first repository to analyze code health."
      action={onStart ? { label: "Get started", onClick: onStart } : undefined}
      {...props}
    />
  )
}
