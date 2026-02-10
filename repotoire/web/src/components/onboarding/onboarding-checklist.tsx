'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  TooltipProvider,
} from '@/components/ui/tooltip';
import {
  CheckCircle2,
  Circle,
  Github,
  Zap,
  Search,
  Wrench,
  Bell,
  ChevronDown,
  ChevronUp,
  X,
  Sparkles,
  PartyPopper,
} from 'lucide-react';

// Constants for localStorage keys
const CHECKLIST_DISMISSED_KEY = 'repotoire_checklist_dismissed';
const CHECKLIST_COLLAPSED_KEY = 'repotoire_checklist_collapsed';

interface ChecklistStep {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  href: string;
  completed: boolean;
}

interface OnboardingChecklistProps {
  hasGitHubConnected?: boolean;
  hasRepositories?: boolean;
  hasCompletedAnalysis?: boolean;
  hasReviewedFindings?: boolean;
  hasTriedAiFix?: boolean;
  hasConfiguredNotifications?: boolean;
  className?: string;
}

// Helper to check if checklist was dismissed
function wasChecklistDismissed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem(CHECKLIST_DISMISSED_KEY) === 'true';
  } catch {
    return false;
  }
}

// Helper to check if checklist is collapsed
function isChecklistCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem(CHECKLIST_COLLAPSED_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Reset checklist dismissed state - useful for settings page
 */
export function resetChecklistDismissed(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(CHECKLIST_DISMISSED_KEY);
    localStorage.removeItem(CHECKLIST_COLLAPSED_KEY);
  } catch {
    // Ignore storage errors
  }
}

export function OnboardingChecklist({
  hasGitHubConnected = false,
  hasRepositories = false,
  hasCompletedAnalysis = false,
  hasReviewedFindings = false,
  hasTriedAiFix = false,
  hasConfiguredNotifications = false,
  className,
}: OnboardingChecklistProps) {
  const [dismissed, setDismissed] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);

  // Load state from localStorage on mount
  useEffect(() => {
    setIsHydrated(true);
    if (wasChecklistDismissed()) {
      setDismissed(true);
    }
    if (isChecklistCollapsed()) {
      setCollapsed(true);
    }
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissed(true);
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem(CHECKLIST_DISMISSED_KEY, 'true');
      } catch {
        // Ignore storage errors
      }
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const newValue = !prev;
      if (typeof window !== 'undefined') {
        try {
          localStorage.setItem(CHECKLIST_COLLAPSED_KEY, String(newValue));
        } catch {
          // Ignore storage errors
        }
      }
      return newValue;
    });
  }, []);

  // Don't render until hydrated
  if (!isHydrated) return null;
  if (dismissed) return null;

  const steps: ChecklistStep[] = [
    {
      id: 'connect',
      title: 'Connect repository',
      description: 'Link your GitHub account',
      icon: <Github className="h-4 w-4" />,
      href: '/dashboard/repos/connect',
      completed: hasGitHubConnected && hasRepositories,
    },
    {
      id: 'analyze',
      title: 'Run first analysis',
      description: 'Analyze your codebase',
      icon: <Zap className="h-4 w-4" />,
      href: '/dashboard/repos',
      completed: hasCompletedAnalysis,
    },
    {
      id: 'review',
      title: 'Review findings',
      description: 'Check detected issues',
      icon: <Search className="h-4 w-4" />,
      href: '/dashboard/findings',
      completed: hasReviewedFindings,
    },
    {
      id: 'fix',
      title: 'Try an AI fix',
      description: 'Let AI propose a solution',
      icon: <Wrench className="h-4 w-4" />,
      href: '/dashboard/fixes',
      completed: hasTriedAiFix,
    },
    {
      id: 'notify',
      title: 'Configure notifications',
      description: 'Set up alerts',
      icon: <Bell className="h-4 w-4" />,
      href: '/dashboard/settings#notifications',
      completed: hasConfiguredNotifications,
    },
  ];

  const completedCount = steps.filter((s) => s.completed).length;
  const totalCount = steps.length;
  const progress = (completedCount / totalCount) * 100;
  const isComplete = completedCount === totalCount;

  // Don't show if all complete
  if (isComplete) {
    return null;
  }

  return (
    <TooltipProvider delayDuration={300}>
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -10 }}
        className={cn(
          'rounded-lg border bg-card p-3',
          className
        )}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">Getting Started</span>
          </div>
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={toggleCollapsed}
                >
                  {collapsed ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronUp className="h-3.5 w-3.5" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {collapsed ? 'Expand' : 'Collapse'}
              </TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-foreground"
                  onClick={handleDismiss}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Don't show again</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {/* Progress bar */}
        <div className="mb-2">
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
            <span>{completedCount} of {totalCount} complete</span>
            <span>{Math.round(progress)}%</span>
          </div>
          <Progress value={progress} className="h-1.5" />
        </div>

        {/* Steps list */}
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="space-y-1 pt-1">
                {steps.map((step, index) => (
                  <motion.div
                    key={step.id}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.05 }}
                  >
                    <Link
                      href={step.href}
                      className={cn(
                        'flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors',
                        step.completed
                          ? 'text-muted-foreground'
                          : 'text-foreground hover:bg-muted'
                      )}
                    >
                      <div
                        className={cn(
                          'flex-shrink-0',
                          step.completed ? 'text-success' : 'text-muted-foreground'
                        )}
                      >
                        {step.completed ? (
                          <CheckCircle2 className="h-4 w-4" />
                        ) : (
                          <Circle className="h-4 w-4" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <span
                          className={cn(
                            'block truncate',
                            step.completed && 'line-through'
                          )}
                        >
                          {step.title}
                        </span>
                      </div>
                      {!step.completed && (
                        <div className="flex-shrink-0 opacity-60">
                          {step.icon}
                        </div>
                      )}
                    </Link>
                  </motion.div>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Collapsed summary */}
        {collapsed && (
          <div className="text-xs text-muted-foreground">
            {totalCount - completedCount} steps remaining
          </div>
        )}
      </motion.div>
    </TooltipProvider>
  );
}

// Hook to track onboarding progress from API or local state
export function useOnboardingProgress() {
  const [progress, setProgress] = useState({
    hasGitHubConnected: false,
    hasRepositories: false,
    hasCompletedAnalysis: false,
    hasReviewedFindings: false,
    hasTriedAiFix: false,
    hasConfiguredNotifications: false,
  });

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Check localStorage for tracked actions
    if (typeof window === 'undefined') return;

    try {
      const stored = localStorage.getItem('repotoire_onboarding_actions');
      if (stored) {
        const parsed = JSON.parse(stored);
        setProgress((prev) => ({ ...prev, ...parsed }));
      }
    } catch {
      // Ignore storage errors
    }

    setIsLoading(false);
  }, []);

  const markComplete = useCallback((key: keyof typeof progress) => {
    setProgress((prev) => {
      const updated = { ...prev, [key]: true };
      if (typeof window !== 'undefined') {
        try {
          localStorage.setItem('repotoire_onboarding_actions', JSON.stringify(updated));
        } catch {
          // Ignore storage errors
        }
      }
      return updated;
    });
  }, []);

  return { progress, isLoading, markComplete };
}
