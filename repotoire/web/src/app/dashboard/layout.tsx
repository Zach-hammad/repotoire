'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard,
  ListChecks,
  Settings,
  FileCode2,
  ChevronLeft,
  Menu,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useState } from 'react';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { SWRConfig } from 'swr';
import { ThemeToggle } from '@/components/dashboard/theme-toggle';

const sidebarLinks = [
  {
    name: 'Overview',
    href: '/dashboard',
    icon: LayoutDashboard,
  },
  {
    name: 'Fixes',
    href: '/dashboard/fixes',
    icon: ListChecks,
  },
  {
    name: 'Files',
    href: '/dashboard/files',
    icon: FileCode2,
  },
  {
    name: 'Settings',
    href: '/dashboard/settings',
    icon: Settings,
  },
];

function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname();

  return (
    <div className={cn('flex h-full flex-col gap-2', className)}>
      <div className="flex h-14 items-center border-b px-4">
        <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <FileCode2 className="h-4 w-4" />
          </div>
          <span>Repotoire</span>
        </Link>
      </div>
      <nav className="flex-1 space-y-1 px-2 py-4">
        {sidebarLinks.map((link) => {
          const isActive = pathname === link.href ||
            (link.href !== '/dashboard' && pathname.startsWith(link.href));
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-secondary text-secondary-foreground'
                  : 'text-muted-foreground hover:bg-secondary/50 hover:text-secondary-foreground'
              )}
            >
              <link.icon className="h-4 w-4" />
              {link.name}
            </Link>
          );
        })}
      </nav>
      <div className="border-t p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">Theme</span>
          <ThemeToggle />
        </div>
        <Link href="/" className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
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
    <SWRConfig
      value={{
        revalidateOnFocus: false,
        revalidateIfStale: true,
        dedupingInterval: 5000,
      }}
    >
      <div className="flex min-h-screen">
        {/* Desktop Sidebar */}
        <aside className="hidden w-64 shrink-0 border-r bg-card md:block">
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
          <SheetContent side="left" className="w-64 p-0">
            <Sidebar />
          </SheetContent>
        </Sheet>

        {/* Main Content */}
        <main className="flex-1 overflow-auto">
          <div className="container max-w-7xl p-6 md:p-8">{children}</div>
        </main>
      </div>
    </SWRConfig>
  );
}
