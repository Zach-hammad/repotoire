/**
 * Centralized animation constants for consistent motion design
 * Based on the "Digital Pathologist" design system
 */

// Easing curves - clinical precision
export const EASING = {
  /** Standard ease-out for most animations */
  smooth: [0.22, 1, 0.36, 1] as const,
  /** Bouncy spring for interactive feedback */
  spring: { stiffness: 400, damping: 17 },
  /** Gentle ease for subtle transitions */
  gentle: [0.4, 0, 0.2, 1] as const,
  /** Linear for continuous animations */
  linear: [0, 0, 1, 1] as const,
} as const;

// Duration constants in seconds
export const DURATION = {
  /** Instant feedback (hover states) */
  instant: 0.15,
  /** Fast transitions (buttons, toggles) */
  fast: 0.2,
  /** Normal transitions (cards, modals) */
  normal: 0.3,
  /** Medium transitions (page elements) */
  medium: 0.5,
  /** Slow transitions (hero animations) */
  slow: 0.6,
  /** Extended animations (complex reveals) */
  extended: 0.8,
  /** Counter animations */
  counter: 1.2,
} as const;

// Delay constants for staggered animations
export const DELAY = {
  /** Stagger between list items */
  stagger: 0.05,
  /** Stagger for larger elements */
  staggerLarge: 0.1,
  /** Initial delay for hero content */
  heroContent: 0.2,
  /** Delay for secondary content */
  secondary: 0.3,
  /** Delay for tertiary content */
  tertiary: 0.5,
} as const;

// Distance/offset constants in pixels
export const OFFSET = {
  /** Small vertical movement */
  small: 10,
  /** Medium vertical movement */
  medium: 20,
  /** Large vertical movement */
  large: 30,
  /** Horizontal slide */
  horizontal: 24,
  /** Blur amount for fade animations */
  blur: 10,
} as const;

// Scale constants
export const SCALE = {
  /** Button press effect */
  pressed: 0.98,
  /** Hover lift effect */
  hover: 1.02,
  /** Initial scale for pop-in */
  initial: 0.96,
  /** Pulse minimum */
  pulseMin: 1,
  /** Pulse maximum */
  pulseMax: 1.08,
} as const;

// Common animation variants for Framer Motion
export const fadeUpVariants = {
  hidden: {
    opacity: 0,
    y: OFFSET.medium,
  },
  visible: {
    opacity: 1,
    y: 0,
    transition: {
      duration: DURATION.slow,
      ease: EASING.smooth,
    },
  },
} as const;

export const fadeInVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      duration: DURATION.medium,
      ease: EASING.gentle,
    },
  },
} as const;

export const slideInRightVariants = {
  hidden: {
    opacity: 0,
    x: OFFSET.horizontal,
  },
  visible: {
    opacity: 1,
    x: 0,
    transition: {
      duration: DURATION.slow,
      ease: EASING.smooth,
    },
  },
} as const;

export const scaleInVariants = {
  hidden: {
    opacity: 0,
    scale: SCALE.initial,
  },
  visible: {
    opacity: 1,
    scale: 1,
    transition: {
      duration: DURATION.medium,
      ease: EASING.smooth,
    },
  },
} as const;

export const staggerContainerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: DELAY.stagger,
    },
  },
} as const;

export const staggerItemVariants = {
  hidden: { opacity: 0, y: OFFSET.medium },
  visible: {
    opacity: 1,
    y: 0,
    transition: {
      duration: DURATION.medium,
      ease: EASING.smooth,
    },
  },
} as const;

// Pulse animation for alerts/severity indicators
export const pulseVariants = {
  initial: { scale: SCALE.pulseMin, opacity: 0.4 },
  animate: {
    scale: [SCALE.pulseMin, SCALE.pulseMax, SCALE.pulseMin],
    opacity: [0.4, 0.7, 0.4],
    transition: {
      duration: 2.5,
      repeat: Infinity,
      ease: 'easeInOut',
    },
  },
} as const;

// Shimmer animation for loading/CTA buttons
export const shimmerVariants = {
  initial: { x: '-100%' },
  animate: {
    x: '200%',
    transition: {
      repeat: Infinity,
      repeatDelay: 3,
      duration: 1.5,
      ease: 'easeInOut',
    },
  },
} as const;

// Health gauge constants
export const GAUGE = {
  /** Default gauge radius */
  radius: 45,
  /** Stroke width for gauge arc */
  strokeWidth: {
    sm: 8,
    md: 10,
    lg: 12,
  },
  /** Gauge sizes in pixels */
  size: {
    sm: 120,
    md: 180,
    lg: 240,
  },
} as const;

// Grade thresholds for health score
export const GRADE_THRESHOLDS = {
  A: 90,
  B: 80,
  C: 70,
  D: 60,
  F: 0,
} as const;

// Severity colors as CSS variable references
export const SEVERITY_COLORS = {
  critical: 'var(--severity-critical)',
  high: 'var(--severity-high)',
  medium: 'var(--severity-medium)',
  low: 'var(--severity-low)',
  info: 'var(--severity-info)',
} as const;

export type Severity = keyof typeof SEVERITY_COLORS;
export type Grade = keyof typeof GRADE_THRESHOLDS;
