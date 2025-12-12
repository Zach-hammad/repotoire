"use client";

import { useEffect } from "react";

interface ChangelogContentProps {
  html: string;
}

/**
 * Client component to render changelog HTML content with proper styling
 */
export function ChangelogContent({ html }: ChangelogContentProps) {
  // Apply syntax highlighting if Prism is available
  useEffect(() => {
    if (typeof window !== "undefined" && (window as any).Prism) {
      (window as any).Prism.highlightAll();
    }
  }, [html]);

  return (
    <div
      className="prose prose-neutral dark:prose-invert max-w-none
        prose-headings:scroll-mt-20
        prose-h2:text-2xl prose-h2:font-bold prose-h2:mt-8 prose-h2:mb-4
        prose-h3:text-xl prose-h3:font-semibold prose-h3:mt-6 prose-h3:mb-3
        prose-p:leading-7
        prose-a:text-primary prose-a:no-underline hover:prose-a:underline
        prose-code:text-sm prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
        prose-pre:bg-muted prose-pre:border
        prose-img:rounded-lg prose-img:border
        prose-ul:my-4 prose-ol:my-4
        prose-li:my-1
        prose-blockquote:border-l-primary prose-blockquote:bg-muted/50 prose-blockquote:py-1 prose-blockquote:px-4 prose-blockquote:rounded-r-lg
        prose-table:border prose-th:bg-muted prose-th:px-4 prose-th:py-2 prose-td:px-4 prose-td:py-2 prose-td:border-t
        prose-hr:my-8"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
