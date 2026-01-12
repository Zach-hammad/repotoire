'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Command } from 'cmdk';
import type { LucideIcon } from 'lucide-react';
import {
  LayoutDashboard,
  ListChecks,
  Settings,
  FileCode2,
  CreditCard,
  AlertCircle,
  FolderGit2,
  Package,
  Search,
  Wand2,
  GitBranch,
  TrendingUp,
  Moon,
  Sun,
  LogOut,
} from 'lucide-react';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { useTheme } from 'next-themes';
import { useClerk } from '@clerk/nextjs';
import { useRepositoryContext } from '@/contexts/repository-context';

interface CommandItem {
  id: string;
  title: string;
  subtitle?: string;
  icon: LucideIcon;
  action: () => void;
  keywords?: string[];
  group: string;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const router = useRouter();
  const { setTheme, theme } = useTheme();
  const { signOut } = useClerk();
  const { repositories, setSelectedRepositoryId } = useRepositoryContext();

  // Toggle the menu when ⌘K is pressed
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((open) => !open);
      }
    };

    document.addEventListener('keydown', down);
    return () => document.removeEventListener('keydown', down);
  }, []);

  const runCommand = useCallback((command: () => void) => {
    setOpen(false);
    command();
  }, []);

  // Navigation commands
  const navigationCommands: CommandItem[] = [
    {
      id: 'dashboard',
      title: 'Dashboard',
      subtitle: 'View overview and analytics',
      icon: LayoutDashboard,
      action: () => router.push('/dashboard'),
      keywords: ['home', 'overview', 'analytics'],
      group: 'Navigation',
    },
    {
      id: 'findings',
      title: 'Findings',
      subtitle: 'Browse detected issues',
      icon: AlertCircle,
      action: () => router.push('/dashboard/findings'),
      keywords: ['issues', 'problems', 'bugs', 'smells'],
      group: 'Navigation',
    },
    {
      id: 'fixes',
      title: 'AI Fixes',
      subtitle: 'Review and apply fixes',
      icon: ListChecks,
      action: () => router.push('/dashboard/fixes'),
      keywords: ['suggestions', 'recommendations', 'apply'],
      group: 'Navigation',
    },
    {
      id: 'repos',
      title: 'Repositories',
      subtitle: 'Manage connected repos',
      icon: FolderGit2,
      action: () => router.push('/dashboard/repos'),
      keywords: ['github', 'connect', 'projects'],
      group: 'Navigation',
    },
    {
      id: 'files',
      title: 'File Browser',
      subtitle: 'Browse repository files',
      icon: FileCode2,
      action: () => router.push('/dashboard/files'),
      keywords: ['code', 'source', 'tree'],
      group: 'Navigation',
    },
    {
      id: 'marketplace',
      title: 'Marketplace',
      subtitle: 'Explore integrations',
      icon: Package,
      action: () => router.push('/dashboard/marketplace'),
      keywords: ['extensions', 'plugins', 'integrations'],
      group: 'Navigation',
    },
    {
      id: 'billing',
      title: 'Billing',
      subtitle: 'Manage subscription',
      icon: CreditCard,
      action: () => router.push('/dashboard/billing'),
      keywords: ['subscription', 'plan', 'payment'],
      group: 'Navigation',
    },
    {
      id: 'settings',
      title: 'Settings',
      subtitle: 'Configure preferences',
      icon: Settings,
      action: () => router.push('/dashboard/settings'),
      keywords: ['preferences', 'config', 'options'],
      group: 'Navigation',
    },
  ];

  // Quick actions
  const quickActions: CommandItem[] = [
    {
      id: 'critical-findings',
      title: 'View Critical Issues',
      subtitle: 'Jump to critical severity findings',
      icon: AlertCircle,
      action: () => router.push('/dashboard/findings?severity=critical'),
      keywords: ['urgent', 'important', 'severe'],
      group: 'Quick Actions',
    },
    {
      id: 'pending-fixes',
      title: 'Review Pending Fixes',
      subtitle: 'Jump to fixes awaiting review',
      icon: Wand2,
      action: () => router.push('/dashboard/fixes?status=pending'),
      keywords: ['approve', 'review', 'suggestions'],
      group: 'Quick Actions',
    },
    {
      id: 'new-analysis',
      title: 'New Analysis',
      subtitle: 'Start a new code analysis',
      icon: TrendingUp,
      action: () => router.push('/dashboard/repos'),
      keywords: ['analyze', 'scan', 'check'],
      group: 'Quick Actions',
    },
  ];

  // Repository switching
  const repoCommands: CommandItem[] = [
    {
      id: 'all-repos',
      title: 'All Repositories',
      subtitle: 'View data from all repos',
      icon: FolderGit2,
      action: () => setSelectedRepositoryId(null),
      keywords: ['global', 'aggregate'],
      group: 'Switch Repository',
    },
    ...repositories.map((repo) => ({
      id: `repo-${repo.id}`,
      title: repo.full_name,
      subtitle: repo.health_score !== null ? `Health: ${repo.health_score}%` : 'Not analyzed',
      icon: GitBranch,
      action: () => setSelectedRepositoryId(repo.id),
      keywords: [repo.full_name.split('/')[0], repo.full_name.split('/')[1]],
      group: 'Switch Repository',
    })),
  ];

  // Theme commands
  const themeCommands: CommandItem[] = [
    {
      id: 'theme-light',
      title: 'Light Mode',
      subtitle: 'Switch to light theme',
      icon: Sun,
      action: () => setTheme('light'),
      keywords: ['bright', 'day'],
      group: 'Theme',
    },
    {
      id: 'theme-dark',
      title: 'Dark Mode',
      subtitle: 'Switch to dark theme',
      icon: Moon,
      action: () => setTheme('dark'),
      keywords: ['night', 'dim'],
      group: 'Theme',
    },
  ];

  // Account commands
  const accountCommands: CommandItem[] = [
    {
      id: 'sign-out',
      title: 'Sign Out',
      subtitle: 'Sign out of your account',
      icon: LogOut,
      action: () => signOut(),
      keywords: ['logout', 'exit'],
      group: 'Account',
    },
  ];

  const allCommands = [
    ...navigationCommands,
    ...quickActions,
    ...repoCommands,
    ...themeCommands,
    ...accountCommands,
  ];

  // Group commands
  const groupedCommands = allCommands.reduce((acc, cmd) => {
    if (!acc[cmd.group]) {
      acc[cmd.group] = [];
    }
    acc[cmd.group].push(cmd);
    return acc;
  }, {} as Record<string, CommandItem[]>);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="overflow-hidden p-0 shadow-lg max-w-lg">
        <Command className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group]:not([hidden])_~[cmdk-group]]:pt-0 [&_[cmdk-group]]:px-2 [&_[cmdk-input-wrapper]_svg]:h-5 [&_[cmdk-input-wrapper]_svg]:w-5 [&_[cmdk-input]]:h-12 [&_[cmdk-item]]:px-2 [&_[cmdk-item]]:py-3 [&_[cmdk-item]_svg]:h-5 [&_[cmdk-item]_svg]:w-5">
          <div className="flex items-center border-b px-3">
            <Search className="mr-2 h-4 w-4 shrink-0 text-muted-foreground" />
            <Command.Input
              placeholder="Type a command or search..."
              value={search}
              onValueChange={setSearch}
              className="flex h-12 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Search commands"
            />
            <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
              <span className="text-xs">⌘</span>K
            </kbd>
          </div>
          <Command.List className="max-h-[400px] overflow-y-auto p-2">
            <Command.Empty className="py-6 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>
            {Object.entries(groupedCommands).map(([group, commands]) => (
              <Command.Group key={group} heading={group} className="text-xs font-medium text-muted-foreground mb-2">
                {commands.map((cmd) => (
                  <Command.Item
                    key={cmd.id}
                    value={`${cmd.title} ${cmd.subtitle || ''} ${cmd.keywords?.join(' ') || ''}`}
                    onSelect={() => runCommand(cmd.action)}
                    className="flex items-center gap-3 rounded-lg px-3 py-2 cursor-pointer hover:bg-accent aria-selected:bg-accent"
                  >
                    <cmd.icon className="h-4 w-4 text-muted-foreground" />
                    <div className="flex flex-col gap-0.5">
                      <span className="text-sm font-medium">{cmd.title}</span>
                      {cmd.subtitle && (
                        <span className="text-xs text-muted-foreground">{cmd.subtitle}</span>
                      )}
                    </div>
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
