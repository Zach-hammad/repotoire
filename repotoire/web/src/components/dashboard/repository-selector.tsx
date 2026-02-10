'use client';

import { FolderGit2, ChevronDown, Check } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useRepositoryContext } from '@/contexts/repository-context';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

interface RepositorySelectorProps {
  /** Compact mode for inline display */
  compact?: boolean;
  /** Class name for customization */
  className?: string;
}

export function RepositorySelector({ compact = false, className }: RepositorySelectorProps) {
  const { selectedRepository, selectedRepositoryId, setSelectedRepositoryId, repositories, isLoading } = useRepositoryContext();

  if (isLoading) {
    return (
      <div className={cn('h-8 w-32 animate-pulse rounded-md bg-muted', className)} />
    );
  }

  if (repositories.length === 0) {
    return null;
  }

  const displayName = selectedRepository?.full_name || 'All Repositories';
  const shortName = selectedRepository?.full_name?.split('/')[1] || 'All Repos';

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size={compact ? 'sm' : 'default'}
          className={cn(
            'gap-2 font-normal',
            compact && 'h-7 px-2 text-xs',
            className
          )}
        >
          <FolderGit2 className={cn('h-4 w-4 text-muted-foreground', compact && 'h-3 w-3')} />
          <span className="truncate max-w-[150px]">
            {compact ? shortName : displayName}
          </span>
          <ChevronDown className={cn('h-4 w-4 text-muted-foreground', compact && 'h-3 w-3')} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-64">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Select Repository
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => setSelectedRepositoryId(null)}
          className="flex items-center justify-between"
        >
          <span>All Repositories</span>
          {!selectedRepositoryId && <Check className="h-4 w-4" />}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {repositories.map((repo) => (
          <DropdownMenuItem
            key={repo.id}
            onClick={() => setSelectedRepositoryId(repo.id)}
            className="flex items-center justify-between"
          >
            <div className="flex items-center gap-2 min-w-0">
              <FolderGit2 className="h-4 w-4 text-muted-foreground shrink-0" />
              <span className="truncate">{repo.full_name}</span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {repo.health_score !== null && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                  {repo.health_score}%
                </Badge>
              )}
              {selectedRepositoryId === repo.id && <Check className="h-4 w-4" />}
            </div>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Compact badge showing current repository */
export function RepositoryBadge({ className }: { className?: string }) {
  const { selectedRepository } = useRepositoryContext();

  if (!selectedRepository) {
    return null;
  }

  return (
    <Badge variant="secondary" className={cn('gap-1.5 font-normal', className)}>
      <FolderGit2 className="h-3 w-3" />
      {selectedRepository.full_name.split('/')[1]}
    </Badge>
  );
}
