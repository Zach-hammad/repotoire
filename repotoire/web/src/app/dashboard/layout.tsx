'use client';

import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard,
  ListChecks,
  Settings,
  FileCode2,
  ChevronLeft,
  Menu,
  CreditCard,
  AlertCircle,
  FolderGit2,
  ShieldAlert,
  Boxes,
  GitBranch,
  Database,
  Home,
  Terminal,
  BookOpen,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useState } from 'react';
import { Sheet, SheetContent, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { SWRConfig } from 'swr';
import { ThemeToggle } from '@/components/dashboard/theme-toggle';
import { UserNav } from '@/components/auth/user-nav';
import { ApiAuthProvider } from '@/components/providers/api-auth-provider';
import { SafeOrganizationSwitcher } from '@/components/auth/safe-organization-switcher';
import { PageTransition } from '@/components/transitions/page-transition';
import { ErrorBoundary } from '@/components/error-boundary';
import { LazyNotificationCenter, LazyKeyboardShortcuts, LazyCommandPalette } from '@/components/lazy-components';
import { RepositoryProvider } from '@/contexts/repository-context';
import { RepositorySelector } from '@/components/dashboard/repository-selector';
import { OnboardingChecklist, useOnboardingProgress } from '@/components/onboarding/onboarding-checklist';
import { RequireAuth } from '@/components/auth/require-auth';

// Grouped navigation for better information architecture
const sidebarSections = [
  {
    name: 'Analyze',
    items: [
      { name: 'Overview', href: '/dashboard', icon: LayoutDashboard },
      { name: 'Repositories', href: '/dashboard/repos', icon: FolderGit2 },
      { name: 'Findings', href: '/dashboard/findings', icon: AlertCircle },
      { name: 'Graph Explorer', href: '/dashboard/graph', icon: Database },
    ],
  },
  {
    name: 'Improve',
    items: [
      { name: 'AI Fixes', href: '/dashboard/fixes', icon: ListChecks },
      { name: 'File Browser', href: '/dashboard/files', icon: FileCode2 },
    ],
  },
  {
    name: 'Security',
    items: [
      { name: 'Secrets Scanner', href: '/dashboard/security/secrets', icon: ShieldAlert },
    ],
  },
  {
    name: 'Monorepo',
    items: [
      { name: 'Packages', href: '/dashboard/monorepo', icon: Boxes },
      { name: 'Dependencies', href: '/dashboard/monorepo/dependencies', icon: GitBranch },
    ],
  },
  {
    name: 'Account',
    items: [
      { name: 'Billing', href: '/dashboard/billing', icon: CreditCard },
      { name: 'Settings', href: '/dashboard/settings', icon: Settings },
    ],
  },
];

function SidebarOnboardingChecklist() {
  const { progress, isLoading } = useOnboardingProgress();

  if (isLoading) return null;

  // Check if user has completed basic onboarding
  const hasCompletedBasic = progress.hasGitHubConnected && progress.hasRepositories && progress.hasCompletedAnalysis;
  
  // Only show checklist to newer users who haven't completed everything
  const isNewUser = !hasCompletedBasic || 
    !progress.hasReviewedFindings || 
    !progress.hasTriedAiFix || 
    !progress.hasConfiguredNotifications;

  if (!isNewUser) return null;

  return (
    <div className="px-3 pb-3">
      <OnboardingChecklist
        hasGitHubConnected={progress.hasGitHubConnected}
        hasRepositories={progress.hasRepositories}
        hasCompletedAnalysis={progress.hasCompletedAnalysis}
        hasReviewedFindings={progress.hasReviewedFindings}
        hasTriedAiFix={progress.hasTriedAiFix}
        hasConfiguredNotifications={progress.hasConfiguredNotifications}
      />
    </div>
  );
}

const topNavLinks = [
  { href: '/', label: 'Home', icon: Home },
  { href: '/cli', label: 'CLI', icon: Terminal },
  { href: '/docs', label: 'Docs', icon: BookOpen },
];

function DashboardHeader({ onMenuClick }: { onMenuClick?: () => void }) {
  return (
    <header className="h-16 border-b border-border/50 bg-background/95 backdrop-blur-sm flex items-center justify-between px-4 md:px-6">
      {/* Left side: Menu button (mobile) + Nav links */}
      <div className="flex items-center gap-4">
        {/* Mobile menu button */}
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden shrink-0"
          onClick={onMenuClick}
        >
          <Menu className="h-5 w-5" />
          <span className="sr-only">Toggle menu</span>
        </Button>
        
        {/* Nav links - icons only on mobile, full on desktop */}
        <nav className="flex items-center gap-2 md:gap-6">
          {topNavLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors p-2 md:p-0"
              title={link.label}
            >
              <link.icon className="h-4 w-4" />
              <span className="hidden md:inline">{link.label}</span>
            </Link>
          ))}
        </nav>
      </div>
      
      {/* Right side: Theme + User */}
      <div className="flex items-center gap-2 md:gap-4">
        <ThemeToggle />
        <UserNav />
      </div>
    </header>
  );
}

function Sidebar({ className, onNavigate }: { className?: string; onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <div className={cn('flex h-full flex-col gap-2', className)}>
      <div className="flex h-16 items-center border-b border-border/50 px-4">
        <Link href="/dashboard" className="flex items-center" aria-label="Repotoire dashboard home" onClick={onNavigate}>
          <Image
            src="/logo.png"
            alt="Repotoire"
            width={120}
            height={28}
            className="h-7 w-auto dark:hidden"
            priority
          />
          <Image
            src="/logo-grayscale.png"
            alt="Repotoire"
            width={120}
            height={28}
            className="h-7 w-auto hidden dark:block brightness-200"
            priority
          />
        </Link>
      </div>
      {/* Repository Selector - Above navigation */}
      <div className="px-3 py-3 border-b border-border/50">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2 block px-3">Repository</span>
        <RepositorySelector className="w-full justify-between" />
      </div>

      <nav className="flex-1 space-y-6 px-3 py-4 overflow-y-auto">
        {sidebarSections.map((section) => (
          <div key={section.name}>
            <h3 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
              {section.name}
            </h3>
            <div className="space-y-1">
              {section.items.map((link) => {
                const isActive = pathname === link.href ||
                  (link.href !== '/dashboard' && pathname.startsWith(link.href));
                return (
                  <Link
                    key={link.href}
                    href={link.href}
                    onClick={onNavigate}
                    className={cn(
                      'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-200',
                      isActive
                        ? 'bg-brand-gradient text-white shadow-sm'
                        : 'text-muted-foreground hover:bg-secondary hover:text-foreground'
                    )}
                  >
                    <link.icon className="h-4 w-4" />
                    {link.name}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      
      {/* Onboarding Checklist for new users */}
      <SidebarOnboardingChecklist />
      
      <div className="border-t border-border/50 p-4 space-y-4">
        <div className="space-y-2">
          <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">Organization</span>
          <SafeOrganizationSwitcher
            hidePersonal={false}
            afterCreateOrganizationUrl="/dashboard"
            afterSelectOrganizationUrl="/dashboard"
            afterLeaveOrganizationUrl="/dashboard"
            appearance={{
              elements: {
                rootBox: "w-full",
                organizationSwitcherTrigger: "w-full justify-between px-3 py-2 border border-border/50 rounded-lg hover:bg-secondary transition-colors",
              },
            }}
          />
        </div>
        <div className="flex items-center justify-between py-1">
          <span className="text-sm text-muted-foreground">Notifications</span>
          <LazyNotificationCenter />
        </div>
      </div>
    </div>
  );
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <RequireAuth>
      <ApiAuthProvider>
        <SWRConfig
          value={{
            revalidateOnFocus: false,
            revalidateIfStale: true,
            revalidateOnReconnect: false,
            dedupingInterval: 10000, // 10s dedup window
            focusThrottleInterval: 30000, // 30s between focus revalidations
            errorRetryCount: 3,
            errorRetryInterval: 5000,
            shouldRetryOnError: (error) => {
              // Don't retry on auth errors
              if (error?.status === 401 || error?.status === 403) return false;
              return true;
            },
          }}
        >
          <RepositoryProvider>
            <div className="flex min-h-screen relative">
              {/* Desktop Sidebar */}
              <aside className="hidden w-64 shrink-0 border-r border-border/50 bg-card/80 backdrop-blur-md md:block relative z-10">
                <Sidebar />
              </aside>

              {/* Mobile Sidebar */}
              <Sheet open={open} onOpenChange={setOpen}>
                <SheetContent side="left" className="w-64 p-0" aria-describedby="mobile-nav-description">
                  <SheetTitle className="sr-only">Navigation Menu</SheetTitle>
                  <SheetDescription id="mobile-nav-description" className="sr-only">
                    Main navigation menu for the Repotoire dashboard
                  </SheetDescription>
                  <Sidebar onNavigate={() => setOpen(false)} />
                </SheetContent>
              </Sheet>

              {/* Main Content Area */}
              <div className="flex-1 flex flex-col overflow-hidden">
                {/* Top Header */}
                <DashboardHeader onMenuClick={() => setOpen(true)} />
                
                {/* Main Content */}
                <main id="main-content" className="flex-1 overflow-auto relative z-10">
                  <div className="container max-w-7xl p-6 md:p-8">
                    <ErrorBoundary>
                      <PageTransition>{children}</PageTransition>
                    </ErrorBoundary>
                  </div>
                </main>
              </div>

              {/* Global keyboard shortcuts */}
              <LazyKeyboardShortcuts />
              {/* Command palette (Cmd+K) */}
              <LazyCommandPalette />
            </div>
          </RepositoryProvider>
        </SWRConfig>
      </ApiAuthProvider>
    </RequireAuth>
  );
}
