import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { Loader2 } from "lucide-react"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium",
    "transition-all duration-200 ease-out",
    "disabled:pointer-events-none disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0",
    "outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    "aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
    // Active/pressed state
    "active:scale-[0.98] active:transition-none",
  ],
  {
    variants: {
      variant: {
        default: [
          "bg-primary text-primary-foreground shadow-md",
          "hover:bg-primary/90 hover:shadow-lg hover:-translate-y-0.5",
          "active:bg-primary/95 active:shadow-sm active:translate-y-0",
        ],
        destructive: [
          "bg-destructive text-white shadow-md",
          "hover:bg-destructive/90 hover:shadow-lg hover:-translate-y-0.5",
          "active:bg-destructive/95 active:shadow-sm active:translate-y-0",
          "focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60",
        ],
        outline: [
          "border bg-background shadow-xs",
          "hover:bg-accent hover:text-accent-foreground hover:border-accent-foreground/20 hover:-translate-y-0.5 hover:shadow-md",
          "active:bg-accent/80 active:shadow-xs active:translate-y-0",
          "dark:bg-input/30 dark:border-input dark:hover:bg-input/50",
        ],
        secondary: [
          "bg-secondary text-secondary-foreground shadow-sm",
          "hover:bg-secondary/80 hover:shadow-md hover:-translate-y-0.5",
          "active:bg-secondary/90 active:shadow-xs active:translate-y-0",
        ],
        ghost: [
          "hover:bg-accent hover:text-accent-foreground",
          "active:bg-accent/80",
          "dark:hover:bg-accent/50",
        ],
        link: [
          "text-primary underline-offset-4",
          "hover:underline hover:text-primary/80",
          "active:text-primary/70",
        ],
        // New variants for distinctive UI
        glow: [
          "relative bg-primary text-primary-foreground shadow-lg",
          "before:absolute before:inset-0 before:rounded-md before:bg-primary/50 before:blur-lg before:opacity-0 before:transition-opacity",
          "hover:before:opacity-100 hover:shadow-xl hover:shadow-primary/25 hover:-translate-y-0.5",
          "active:before:opacity-50 active:shadow-lg active:translate-y-0",
        ],
        success: [
          "bg-primary text-white shadow-md",
          "hover:bg-primary hover:shadow-lg hover:shadow-primary/25 hover:-translate-y-0.5",
          "active:bg-primary active:shadow-sm active:translate-y-0",
        ],
        warning: [
          "bg-amber-500 text-white shadow-md",
          "hover:bg-amber-400 hover:shadow-lg hover:shadow-amber-500/25 hover:-translate-y-0.5",
          "active:bg-amber-600 active:shadow-sm active:translate-y-0",
        ],
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5 text-xs",
        lg: "h-11 rounded-md px-6 has-[>svg]:px-4 text-base",
        xl: "h-12 rounded-lg px-8 has-[>svg]:px-6 text-base font-semibold",
        icon: "size-9",
        "icon-sm": "size-8",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  loading?: boolean
  loadingText?: string
}

function Button({
  className,
  variant,
  size,
  asChild = false,
  loading = false,
  loadingText,
  children,
  disabled,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot : "button"

  // If loading, show loading state
  if (loading) {
    return (
      <button
        type="button"
        data-slot="button"
        className={cn(buttonVariants({ variant, size, className }), "cursor-wait")}
        disabled
        {...props}
      >
        <Loader2 className="size-4 animate-spin" />
        {loadingText && <span>{loadingText}</span>}
        {!loadingText && children}
      </button>
    )
  }

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      disabled={disabled}
      {...props}
    >
      {children}
    </Comp>
  )
}

// Animated button wrapper for special effects
interface AnimatedButtonProps extends ButtonProps {
  shimmer?: boolean
  pulse?: boolean
}

function AnimatedButton({
  className,
  shimmer = false,
  pulse = false,
  children,
  ...props
}: AnimatedButtonProps) {
  return (
    <Button
      className={cn(
        "relative overflow-hidden",
        shimmer && [
          "after:absolute after:inset-0 after:-translate-x-full",
          "after:bg-gradient-to-r after:from-transparent after:via-white/20 after:to-transparent",
          "after:animate-[shimmer_2s_infinite]",
        ],
        pulse && "animate-pulse-subtle",
        className
      )}
      {...props}
    >
      {children}
    </Button>
  )
}

export { Button, AnimatedButton, buttonVariants }
export type { ButtonProps }
