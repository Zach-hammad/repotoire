'use client';

import Link from 'next/link';
import { FileQuestion, BookOpen, ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function DocsNotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center px-4">
      <div className="text-center space-y-6">
        <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-muted">
          <FileQuestion className="h-10 w-10 text-muted-foreground" />
        </div>

        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Documentation Not Found</h1>
          <p className="text-muted-foreground max-w-md mx-auto">
            The documentation page you&apos;re looking for doesn&apos;t exist or has been moved.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 pt-4">
          <Button asChild variant="default">
            <Link href="/docs">
              <BookOpen className="mr-2 h-4 w-4" />
              Go to Documentation
            </Link>
          </Button>
          <Button variant="outline" onClick={() => window.history.back()}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Go Back
          </Button>
        </div>
      </div>
    </div>
  );
}
