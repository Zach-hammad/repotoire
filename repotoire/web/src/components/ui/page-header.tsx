import { cn } from '@/lib/utils';
import { Breadcrumb } from './breadcrumb';
import { HelpCircle } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from './tooltip';

interface PageHeaderProps {
  title: string;
  description?: string;
  helpText?: string;
  breadcrumbs?: { label: string; href?: string }[];
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({ 
  title, 
  description, 
  helpText,
  breadcrumbs, 
  actions,
  className 
}: PageHeaderProps) {
  return (
    <div className={cn("mb-6", className)}>
      {breadcrumbs && <Breadcrumb items={breadcrumbs} className="mb-3" />}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
            {helpText && (
              <Tooltip>
                <TooltipTrigger>
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">
                  {helpText}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
          {description && (
            <p className="text-muted-foreground mt-1">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
