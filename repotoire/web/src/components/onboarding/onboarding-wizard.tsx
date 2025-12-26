'use client';

import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
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
} from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
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
}

export function OnboardingWizard({
  hasGitHubConnected,
  hasRepositories,
  hasCompletedAnalysis,
  onDismiss,
}: OnboardingWizardProps) {
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const steps: OnboardingStep[] = [
    {
      id: 'connect',
      title: 'Connect GitHub',
      description: 'Install the Repotoire GitHub App to access your repositories',
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

  // If all steps complete, don't show
  if (completedSteps === steps.length) {
    return null;
  }

  return (
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
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => {
              setDismissed(true);
              onDismiss?.();
            }}
          >
            Dismiss
          </Button>
        </div>
        <div className="mt-4 space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Setup progress</span>
            <span className="font-medium">
              {completedSteps} of {steps.length} complete
            </span>
          </div>
          <Progress value={progress} className="h-2" />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Steps */}
        <div className="space-y-3">
          {steps.map((step, index) => {
            const isActive = step.id === currentStep.id;
            const isPast = steps.indexOf(step) < steps.indexOf(currentStep);

            return (
              <div
                key={step.id}
                className={cn(
                  'flex items-center gap-4 rounded-lg border p-4 transition-all',
                  step.completed
                    ? 'border-green-500/30 bg-green-500/5'
                    : isActive
                    ? 'border-primary/50 bg-primary/5'
                    : 'border-border bg-muted/30'
                )}
              >
                <div
                  className={cn(
                    'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
                    step.completed
                      ? 'bg-green-500/20 text-green-600'
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
                          ? 'text-green-600 dark:text-green-400'
                          : isActive
                          ? 'text-foreground'
                          : 'text-muted-foreground'
                      )}
                    >
                      {step.title}
                    </h4>
                    {step.completed && (
                      <Badge
                        variant="outline"
                        className="border-green-500/30 bg-green-500/10 text-green-600"
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
                  <Link href={step.action.href}>
                    <Button
                      size="sm"
                      variant={isActive ? 'default' : 'outline'}
                      className={cn(isActive && 'bg-primary')}
                    >
                      {step.action.label}
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </Link>
                )}
              </div>
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
              href="/dashboard/marketplace"
              className="flex items-center gap-2 rounded-md p-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              <Sparkles className="h-4 w-4" />
              <span>Marketplace</span>
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
    </Card>
  );
}
