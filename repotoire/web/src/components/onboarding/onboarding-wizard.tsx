'use client';

import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
  TooltipProvider,
} from '@/components/ui/tooltip';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Github,
  FolderGit2,
  Zap,
  CheckCircle2,
  ArrowRight,
  ExternalLink,
  Sparkles,
  BarChart3,
  Shield,
  BookOpen,
  Loader2,
  HelpCircle,
  Clock,
  X,
  PartyPopper,
} from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

// Constants for localStorage keys
const ONBOARDING_DISMISSED_KEY = 'repotoire_onboarding_dismissed';
const ONBOARDING_PROGRESS_KEY = 'repotoire_onboarding_progress';
const ONBOARDING_SKIPPED_KEY = 'repotoire_onboarding_skipped';

interface OnboardingProgress {
  dismissedAt?: string;
  lastStep?: string;
  completedSteps?: string[];
  skippedAt?: string;
}

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
  tooltip: string;
  icon: React.ReactNode;
  completed: boolean;
  action?: {
    label: string;
    href: string;
    external?: boolean;
  };
}

interface OnboardingWizardProps {
  hasGitHubConnected: boolean;
  hasRepositories: boolean;
  hasCompletedAnalysis: boolean;
  onDismiss?: () => void;
  /** Called when onboarding progress changes (for syncing to backend) */
  onProgressChange?: (progress: OnboardingProgress) => void;
}

// Helper to safely read from localStorage
function getStoredProgress(): OnboardingProgress | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = localStorage.getItem(ONBOARDING_PROGRESS_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

// Helper to save progress to localStorage
function saveProgress(progress: OnboardingProgress): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(ONBOARDING_PROGRESS_KEY, JSON.stringify(progress));
  } catch {
    // Ignore storage errors
  }
}

// Check if onboarding was previously dismissed
function wasOnboardingDismissed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true';
  } catch {
    return false;
  }
}

// Check if onboarding was skipped
function wasOnboardingSkipped(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem(ONBOARDING_SKIPPED_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Reset onboarding state - useful for settings page or testing.
 * Clears dismissed state and progress from localStorage.
 */
export function resetOnboardingProgress(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(ONBOARDING_DISMISSED_KEY);
    localStorage.removeItem(ONBOARDING_PROGRESS_KEY);
    localStorage.removeItem(ONBOARDING_SKIPPED_KEY);
  } catch {
    // Ignore storage errors
  }
}

/**
 * Get the current onboarding progress from localStorage.
 * Useful for syncing to backend or displaying in settings.
 */
export function getOnboardingProgress(): OnboardingProgress | null {
  return getStoredProgress();
}

// Export the type for use in API calls
export type { OnboardingProgress };

// Confetti particle component
function ConfettiParticle({ delay, color }: { delay: number; color: string }) {
  return (
    <motion.div
      className="absolute w-3 h-3 rounded-sm"
      style={{ backgroundColor: color }}
      initial={{ 
        x: 0, 
        y: 0, 
        opacity: 1, 
        scale: 1,
        rotate: 0 
      }}
      animate={{ 
        x: (Math.random() - 0.5) * 400, 
        y: Math.random() * 300 + 100,
        opacity: 0,
        scale: 0,
        rotate: Math.random() * 720 - 360
      }}
      transition={{ 
        duration: 2.5, 
        delay, 
        ease: [0.25, 0.1, 0.25, 1] 
      }}
    />
  );
}

// Celebration animation component
function CelebrationAnimation({ onComplete }: { onComplete: () => void }) {
  const colors = ['#f43f5e', '#8b5cf6', '#06b6d4', '#22c55e', '#eab308', '#f97316'];
  const particles = Array.from({ length: 50 }, (_, i) => ({
    id: i,
    delay: Math.random() * 0.5,
    color: colors[Math.floor(Math.random() * colors.length)],
  }));

  useEffect(() => {
    const timer = setTimeout(onComplete, 3000);
    return () => clearTimeout(timer);
  }, [onComplete]);

  return (
    <motion.div 
      className="fixed inset-0 pointer-events-none z-50 flex items-center justify-center overflow-hidden"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      {/* Center burst */}
      <div className="relative">
        {particles.map((particle) => (
          <ConfettiParticle 
            key={particle.id} 
            delay={particle.delay} 
            color={particle.color}
          />
        ))}
      </div>
      
      {/* Success message */}
      <motion.div
        className="absolute bg-card border-2 border-primary/50 rounded-2xl p-8 shadow-2xl"
        initial={{ scale: 0, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.8, opacity: 0 }}
        transition={{ type: 'spring', damping: 15, stiffness: 300 }}
      >
        <div className="flex flex-col items-center gap-4 text-center">
          <motion.div
            initial={{ rotate: -20, scale: 0 }}
            animate={{ rotate: 0, scale: 1 }}
            transition={{ delay: 0.2, type: 'spring', damping: 10 }}
          >
            <PartyPopper className="h-16 w-16 text-primary" />
          </motion.div>
          <motion.h2 
            className="text-2xl font-bold"
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.3 }}
          >
            You're all set! 🎉
          </motion.h2>
          <motion.p 
            className="text-muted-foreground max-w-sm"
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.4 }}
          >
            Congratulations! You've completed the onboarding. Time to explore your code health insights!
          </motion.p>
        </div>
      </motion.div>
    </motion.div>
  );
}

export function OnboardingWizard({
  hasGitHubConnected,
  hasRepositories,
  hasCompletedAnalysis,
  onDismiss,
  onProgressChange,
}: OnboardingWizardProps) {
  const [dismissed, setDismissed] = useState(false);
  const [showDismissDialog, setShowDismissDialog] = useState(false);
  const [showSkipDialog, setShowSkipDialog] = useState(false);
  const [isInstallingGitHub, setIsInstallingGitHub] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);
  const [showCelebration, setShowCelebration] = useState(false);
  const [hasSeenCelebration, setHasSeenCelebration] = useState(false);

  // Load dismissed state from localStorage on mount
  useEffect(() => {
    setIsHydrated(true);
    if (wasOnboardingDismissed() || wasOnboardingSkipped()) {
      setDismissed(true);
    }
  }, []);

  // Persist progress when steps complete
  useEffect(() => {
    if (!isHydrated) return;

    const completedSteps: string[] = [];
    if (hasGitHubConnected) completedSteps.push('connect');
    if (hasRepositories) completedSteps.push('select');
    if (hasCompletedAnalysis) completedSteps.push('analyze');

    const currentStep = !hasGitHubConnected
      ? 'connect'
      : !hasRepositories
      ? 'select'
      : !hasCompletedAnalysis
      ? 'analyze'
      : 'complete';

    const progress: OnboardingProgress = {
      lastStep: currentStep,
      completedSteps,
    };

    saveProgress(progress);
    onProgressChange?.(progress);

    // Trigger celebration when all steps complete
    if (completedSteps.length === 3 && !hasSeenCelebration && !dismissed) {
      setShowCelebration(true);
      setHasSeenCelebration(true);
    }
  }, [hasGitHubConnected, hasRepositories, hasCompletedAnalysis, isHydrated, onProgressChange, hasSeenCelebration, dismissed]);

  const handleDismiss = useCallback(() => {
    setShowDismissDialog(true);
  }, []);

  const handleSkip = useCallback(() => {
    setShowSkipDialog(true);
  }, []);

  const confirmDismiss = useCallback(() => {
    setDismissed(true);
    setShowDismissDialog(false);

    // Persist dismissal to localStorage
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
        const progress = getStoredProgress() || {};
        progress.dismissedAt = new Date().toISOString();
        saveProgress(progress);
      } catch {
        // Ignore storage errors
      }
    }

    onDismiss?.();
  }, [onDismiss]);

  const confirmSkip = useCallback(() => {
    setDismissed(true);
    setShowSkipDialog(false);

    // Persist skip to localStorage
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem(ONBOARDING_SKIPPED_KEY, 'true');
        const progress = getStoredProgress() || {};
        progress.skippedAt = new Date().toISOString();
        saveProgress(progress);
      } catch {
        // Ignore storage errors
      }
    }

    onDismiss?.();
  }, [onDismiss]);

  const handleGitHubInstall = useCallback(() => {
    setIsInstallingGitHub(true);
    // The navigation will happen via the Link component
    // We keep loading state until page unloads
  }, []);

  const handleCelebrationComplete = useCallback(() => {
    setShowCelebration(false);
    // Auto-dismiss after celebration
    setTimeout(() => {
      setDismissed(true);
      if (typeof window !== 'undefined') {
        localStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
      }
    }, 500);
  }, []);

  // Don't render until hydrated to avoid flash
  if (!isHydrated) return null;
  if (dismissed) return null;

  const steps: OnboardingStep[] = [
    {
      id: 'connect',
      title: 'Connect GitHub',
      description: 'Install the Repotoire GitHub App to access your repositories',
      tooltip: 'We use a secure GitHub App integration to access your repositories. You control exactly which repos we can see.',
      icon: <Github className="h-5 w-5" />,
      completed: hasGitHubConnected,
      action: !hasGitHubConnected
        ? { label: 'Connect GitHub', href: '/dashboard/repos/connect' }
        : undefined,
    },
    {
      id: 'select',
      title: 'Select Repositories',
      description: 'Choose which repositories to analyze',
      tooltip: 'Pick the repos you want to analyze. You can always add or remove repositories later from the settings.',
      icon: <FolderGit2 className="h-5 w-5" />,
      completed: hasRepositories,
      action:
        hasGitHubConnected && !hasRepositories
          ? { label: 'Select Repos', href: '/dashboard/repos' }
          : undefined,
    },
    {
      id: 'analyze',
      title: 'Run First Analysis',
      description: 'Get your code health score and actionable insights',
      tooltip: 'Our AI will analyze your codebase for code quality, security vulnerabilities, and best practices. Takes just a few minutes!',
      icon: <Zap className="h-5 w-5" />,
      completed: hasCompletedAnalysis,
      action:
        hasRepositories && !hasCompletedAnalysis
          ? { label: 'Go to Repos', href: '/dashboard/repos' }
          : undefined,
    },
  ];

  const completedSteps = steps.filter((s) => s.completed).length;
  const progress = (completedSteps / steps.length) * 100;
  const currentStep = steps.find((s) => !s.completed) || steps[steps.length - 1];

  // If all steps complete, show celebration then hide
  if (completedSteps === steps.length && !showCelebration) {
    return null;
  }

  return (
    <TooltipProvider delayDuration={300}>
      {/* Celebration animation */}
      <AnimatePresence>
        {showCelebration && (
          <CelebrationAnimation onComplete={handleCelebrationComplete} />
        )}
      </AnimatePresence>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -20 }}
        transition={{ duration: 0.3 }}
      >
        <Card className="border-2 border-primary/20 bg-gradient-to-br from-primary/5 via-background to-primary/5">
          <CardHeader className="pb-4">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                  <Sparkles className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <CardTitle className="text-xl">Welcome to Repotoire!</CardTitle>
                  <CardDescription>
                    Let's get you set up in just a few steps
                  </CardDescription>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-muted-foreground hover:text-foreground"
                      onClick={handleSkip}
                    >
                      <Clock className="h-4 w-4 mr-1" />
                      Complete later
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    Skip for now and complete onboarding later from the sidebar
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-muted-foreground hover:text-foreground h-8 w-8"
                      onClick={handleDismiss}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    Dismiss onboarding
                  </TooltipContent>
                </Tooltip>
              </div>
            </div>

            {/* Progress indicator */}
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">Setup progress</span>
                <span className="font-medium">
                  {completedSteps} of {steps.length} complete
                </span>
              </div>
              <Progress value={progress} className="h-2" />
              
              {/* Step indicators */}
              <div className="flex justify-between px-1 mt-2">
                {steps.map((step, index) => (
                  <Tooltip key={step.id}>
                    <TooltipTrigger asChild>
                      <div className="flex flex-col items-center gap-1">
                        <motion.div
                          className={cn(
                            'w-8 h-8 rounded-full flex items-center justify-center text-xs font-medium transition-colors',
                            step.completed
                              ? 'bg-success text-success-foreground'
                              : step.id === currentStep.id
                              ? 'bg-primary text-primary-foreground'
                              : 'bg-muted text-muted-foreground'
                          )}
                          initial={false}
                          animate={step.completed ? { scale: [1, 1.2, 1] } : {}}
                          transition={{ duration: 0.3 }}
                        >
                          {step.completed ? (
                            <CheckCircle2 className="h-4 w-4" />
                          ) : (
                            index + 1
                          )}
                        </motion.div>
                        <span className="text-[10px] text-muted-foreground hidden sm:block">
                          {step.title.split(' ')[0]}
                        </span>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">
                      <div className="max-w-xs">
                        <p className="font-medium">{step.title}</p>
                        <p className="text-muted-foreground">{step.tooltip}</p>
                      </div>
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Steps */}
            <div className="space-y-3">
              {steps.map((step, index) => {
                const isActive = step.id === currentStep.id;

                return (
                  <motion.div
                    key={step.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.1 }}
                    className={cn(
                      'flex items-center gap-4 rounded-lg border p-4 transition-all',
                      step.completed
                        ? 'border-success bg-success-muted'
                        : isActive
                        ? 'border-primary/50 bg-primary/5'
                        : 'border-border bg-muted/30'
                    )}
                  >
                    <div
                      className={cn(
                        'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
                        step.completed
                          ? 'bg-success-muted text-success'
                          : isActive
                          ? 'bg-primary/20 text-primary'
                          : 'bg-muted text-muted-foreground'
                      )}
                    >
                      {step.completed ? (
                        <CheckCircle2 className="h-5 w-5" />
                      ) : (
                        step.icon
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h4
                          className={cn(
                            'font-medium',
                            step.completed
                              ? 'text-success'
                              : isActive
                              ? 'text-foreground'
                              : 'text-muted-foreground'
                          )}
                        >
                          {step.title}
                        </h4>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button type="button" className="text-muted-foreground hover:text-foreground transition-colors" aria-label="Help">
                              <HelpCircle className="h-4 w-4" />
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="right" className="max-w-xs">
                            {step.tooltip}
                          </TooltipContent>
                        </Tooltip>
                        {step.completed && (
                          <Badge
                            variant="outline"
                            className="border-success bg-success-muted text-success"
                          >
                            Done
                          </Badge>
                        )}
                        {isActive && !step.completed && (
                          <Badge variant="outline" className="border-primary/30 bg-primary/10">
                            Current
                          </Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground">{step.description}</p>
                    </div>
                    {step.action && (
                      <Link
                        href={step.action.href}
                        onClick={step.id === 'connect' && !hasGitHubConnected ? handleGitHubInstall : undefined}
                      >
                        <Button
                          size="sm"
                          variant={isActive ? 'default' : 'outline'}
                          className={cn(isActive && 'bg-primary')}
                          disabled={step.id === 'connect' && isInstallingGitHub}
                        >
                          {step.id === 'connect' && isInstallingGitHub ? (
                            <>
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              Redirecting...
                            </>
                          ) : (
                            <>
                              {step.action.label}
                              <ArrowRight className="ml-2 h-4 w-4" />
                            </>
                          )}
                        </Button>
                      </Link>
                    )}
                  </motion.div>
                );
              })}
            </div>

            {/* Quick links */}
            <div className="rounded-lg border bg-muted/30 p-4">
              <h4 className="mb-3 text-sm font-medium">While you wait, explore:</h4>
              <div className="grid gap-2 sm:grid-cols-3">
                <Link
                  href="/docs/getting-started"
                  className="flex items-center gap-2 rounded-md p-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <BookOpen className="h-4 w-4" />
                  <span>Documentation</span>
                </Link>
                <a
                  href="https://github.com/repotoire/repotoire"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 rounded-md p-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <Github className="h-4 w-4" />
                  <span>Star on GitHub</span>
                  <ExternalLink className="h-3 w-3" />
                </a>
                <Link
                  href="/dashboard/settings"
                  className="flex items-center gap-2 rounded-md p-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  <Sparkles className="h-4 w-4" />
                  <span>Settings</span>
                </Link>
              </div>
            </div>

            {/* Value props */}
            <div className="grid gap-4 sm:grid-cols-3 pt-2">
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10">
                  <BarChart3 className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <h5 className="text-sm font-medium">Health Scores</h5>
                  <p className="text-xs text-muted-foreground">
                    Get actionable insights about your code quality
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10">
                  <Shield className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <h5 className="text-sm font-medium">Security Scanning</h5>
                  <p className="text-xs text-muted-foreground">
                    Find vulnerabilities before they reach production
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary/10">
                  <Zap className="h-4 w-4 text-primary" />
                </div>
                <div>
                  <h5 className="text-sm font-medium">AI-Powered Fixes</h5>
                  <p className="text-xs text-muted-foreground">
                    Auto-generate fix proposals for detected issues
                  </p>
                </div>
              </div>
            </div>
          </CardContent>

          {/* Dismiss Confirmation Dialog */}
          <AlertDialog open={showDismissDialog} onOpenChange={setShowDismissDialog}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Dismiss onboarding?</AlertDialogTitle>
                <AlertDialogDescription>
                  You can always access these setup steps from Settings &gt; Getting Started.
                  Your progress ({completedSteps} of {steps.length} steps) will be saved.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Continue Setup</AlertDialogCancel>
                <AlertDialogAction onClick={confirmDismiss}>
                  Dismiss
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {/* Skip Confirmation Dialog */}
          <AlertDialog open={showSkipDialog} onOpenChange={setShowSkipDialog}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Complete later?</AlertDialogTitle>
                <AlertDialogDescription>
                  No worries! You can complete the onboarding anytime from the checklist in the sidebar.
                  We'll keep track of your progress.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Continue Now</AlertDialogCancel>
                <AlertDialogAction onClick={confirmSkip}>
                  Complete Later
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </Card>
      </motion.div>
    </TooltipProvider>
  );
}
