'use client';

import { HelpCircle } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface HelpTooltipProps {
  content: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  className?: string;
  iconClassName?: string;
}

/**
 * HelpTooltip - A "What's this?" help icon with tooltip
 * 
 * Use this to provide contextual help for complex features.
 * The icon appears inline and shows a tooltip on hover.
 */
export function HelpTooltip({
  content,
  side = 'top',
  className,
  iconClassName,
}: HelpTooltipProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            'inline-flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors cursor-help focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 rounded-sm',
            className
          )}
          aria-label="Help"
        >
          <HelpCircle className={cn('h-4 w-4', iconClassName)} />
        </button>
      </TooltipTrigger>
      <TooltipContent side={side} className="max-w-xs text-sm">
        {content}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * LabelWithHelp - A label with an inline help tooltip
 * 
 * Combines a text label with a help icon for common patterns.
 */
export function LabelWithHelp({
  label,
  help,
  side = 'top',
  className,
  labelClassName,
}: {
  label: string;
  help: string;
  side?: 'top' | 'right' | 'bottom' | 'left';
  className?: string;
  labelClassName?: string;
}) {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className={labelClassName}>{label}</span>
      <HelpTooltip content={help} side={side} />
    </span>
  );
}
