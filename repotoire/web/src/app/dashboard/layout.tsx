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
  Package,
  ShieldAlert,
  Boxes,
  GitBranch,
  Database,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useState } from 'react';
import { Sheet, SheetContent, SheetTrigger, SheetTitle, SheetDescription } from '@/components/ui/sheet';
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
import { BackgroundProvider, WireframeBackground } from '@/components/backgrounds';

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
    name: 'Extend',
    items: [
      { name: 'Marketplace', href: '/dashboard/marketplace', icon: Package },
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
        <div className="flex items-center justify-between py-1">
          <span className="text-sm text-muted-foreground">Account</span>
          <UserNav />
        </div>
        <div className="flex items-center justify-between py-1">
          <span className="text-sm text-muted-foreground">Theme</span>
          <ThemeToggle />
        </div>
        <Link
          href="/"
          onClick={onNavigate}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors py-1"
        >
          <ChevronLeft className="h-4 w-4" />
          Back to Home
        </Link>
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
    <ApiAuthProvider>
      <SWRConfig
        value={{
          revalidateOnFocus: false,
          revalidateIfStale: true,
          dedupingInterval: 5000,
        }}
      >
        <RepositoryProvider>
          <BackgroundProvider>
            <div className="flex min-h-screen relative">
              {/* Animated 3D Background */}
              <WireframeBackground className="fixed inset-0 -z-10" />

              {/* Desktop Sidebar */}
              <aside className="hidden w-64 shrink-0 border-r border-border/50 bg-card/80 backdrop-blur-md md:block relative z-10">
                <Sidebar />
              </aside>

              {/* Mobile Sidebar */}
              <Sheet open={open} onOpenChange={setOpen}>
                <SheetTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="fixed left-4 top-4 z-40 md:hidden"
                  >
                    <Menu className="h-5 w-5" />
                    <span className="sr-only">Toggle menu</span>
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-64 p-0" aria-describedby="mobile-nav-description">
                  <SheetTitle className="sr-only">Navigation Menu</SheetTitle>
                  <SheetDescription id="mobile-nav-description" className="sr-only">
                    Main navigation menu for the Repotoire dashboard
                  </SheetDescription>
                  <Sidebar onNavigate={() => setOpen(false)} />
                </SheetContent>
              </Sheet>

              {/* Main Content */}
              <main id="main-content" className="flex-1 overflow-auto relative z-10">
                <div className="container max-w-7xl p-6 md:p-8">
                  <ErrorBoundary>
                    <PageTransition>{children}</PageTransition>
                  </ErrorBoundary>
                </div>
              </main>

              {/* Global keyboard shortcuts */}
              <LazyKeyboardShortcuts />
              {/* Command palette (Cmd+K) */}
              <LazyCommandPalette />
            </div>
          </BackgroundProvider>
        </RepositoryProvider>
      </SWRConfig>
    </ApiAuthProvider>
  );
}
