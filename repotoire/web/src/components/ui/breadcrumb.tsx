import * as React from "react"
import { ChevronRight, Home } from "lucide-react"
import Link from "next/link"

import { cn } from "@/lib/utils"

interface BreadcrumbItem {
  label: string
  href?: string
  icon?: React.ReactNode
}

interface BreadcrumbProps extends React.ComponentProps<"nav"> {
  items: BreadcrumbItem[]
  separator?: React.ReactNode
  showHome?: boolean
  homeHref?: string
}

function Breadcrumb({
  items,
  separator,
  showHome = true,
  homeHref = "/dashboard",
  className,
  ...props
}: BreadcrumbProps) {
  const allItems = showHome
    ? [{ label: "Dashboard", href: homeHref, icon: <Home className="size-4" /> }, ...items]
    : items

  return (
    <nav
      aria-label="Breadcrumb"
      className={cn("flex items-center text-sm", className)}
      {...props}
    >
      <ol className="flex items-center gap-1.5">
        {allItems.map((item, index) => {
          const isLast = index === allItems.length - 1

          return (
            <li key={index} className="flex items-center gap-1.5">
              {index > 0 && (
                <span className="text-muted-foreground/60" aria-hidden="true">
                  {separator || <ChevronRight className="size-3.5" />}
                </span>
              )}
              {isLast ? (
                <span
                  className="flex items-center gap-1.5 font-medium text-foreground"
                  aria-current="page"
                >
                  {item.icon}
                  <span className="max-w-[200px] truncate">{item.label}</span>
                </span>
              ) : (
                <Link
                  href={item.href || "#"}
                  className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {item.icon}
                  <span className="max-w-[200px] truncate">{item.label}</span>
                </Link>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

// Simpler component-based API for more complex use cases
function BreadcrumbRoot({
  className,
  ...props
}: React.ComponentProps<"nav">) {
  return (
    <nav
      aria-label="Breadcrumb"
      className={cn("flex items-center text-sm", className)}
      {...props}
    />
  )
}

function BreadcrumbList({
  className,
  ...props
}: React.ComponentProps<"ol">) {
  return (
    <ol
      className={cn("flex items-center gap-1.5 flex-wrap", className)}
      {...props}
    />
  )
}

function BreadcrumbItemWrapper({
  className,
  ...props
}: React.ComponentProps<"li">) {
  return (
    <li
      className={cn("flex items-center gap-1.5", className)}
      {...props}
    />
  )
}

function BreadcrumbLink({
  className,
  ...props
}: React.ComponentProps<typeof Link>) {
  return (
    <Link
      className={cn(
        "flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors",
        className
      )}
      {...props}
    />
  )
}

function BreadcrumbPage({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      aria-current="page"
      className={cn(
        "flex items-center gap-1.5 font-medium text-foreground max-w-[200px] truncate",
        className
      )}
      {...props}
    />
  )
}

function BreadcrumbSeparator({
  className,
  children,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      role="presentation"
      aria-hidden="true"
      className={cn("text-muted-foreground/60", className)}
      {...props}
    >
      {children || <ChevronRight className="size-3.5" />}
    </span>
  )
}

export {
  Breadcrumb,
  BreadcrumbRoot,
  BreadcrumbList,
  BreadcrumbItemWrapper as BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbPage,
  BreadcrumbSeparator,
}
