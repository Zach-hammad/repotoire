import Link from 'next/link';
import { ChevronRight, Home } from 'lucide-react';
import { cn } from '@/lib/utils';

interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
  className?: string;
  showHome?: boolean;
}

export function Breadcrumb({ items, className, showHome = true }: BreadcrumbProps) {
  return (
    <nav aria-label="Breadcrumb" className={cn("flex items-center gap-1 text-sm text-muted-foreground", className)}>
      {showHome && (
        <Link href="/dashboard" className="hover:text-foreground transition-colors">
          <Home className="h-4 w-4" />
        </Link>
      )}
      {items.map((item, index) => (
        <div key={index} className="flex items-center gap-1">
          {(showHome || index > 0) && <ChevronRight className="h-4 w-4" />}
          {item.href ? (
            <Link href={item.href} className="hover:text-foreground transition-colors">
              {item.label}
            </Link>
          ) : (
            <span className="text-foreground font-medium">{item.label}</span>
          )}
        </div>
      ))}
    </nav>
  );
}
