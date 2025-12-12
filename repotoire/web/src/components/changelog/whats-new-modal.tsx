"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@clerk/nextjs";
import { Sparkles, X, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ChangelogEntry,
  fetchWhatsNew,
  markEntriesRead,
  getCategoryLabel,
  getCategoryColor,
  formatRelativeDate,
} from "@/lib/changelog-api";

interface WhatsNewModalProps {
  /** Automatically show modal if there are new entries */
  autoShow?: boolean;
  /** Callback when modal is dismissed */
  onDismiss?: () => void;
}

export function WhatsNewModal({ autoShow = true, onDismiss }: WhatsNewModalProps) {
  const { isSignedIn, getToken } = useAuth();
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<ChangelogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [hasChecked, setHasChecked] = useState(false);

  // Check for new entries on mount
  useEffect(() => {
    if (!isSignedIn || hasChecked) return;

    const checkForNew = async () => {
      try {
        const token = await getToken();
        if (!token) return;

        const result = await fetchWhatsNew(token, 5);
        if (result.has_new && result.entries.length > 0) {
          setEntries(result.entries);
          if (autoShow) {
            setOpen(true);
          }
        }
      } catch (error) {
        console.error("Failed to check for new entries:", error);
      } finally {
        setLoading(false);
        setHasChecked(true);
      }
    };

    checkForNew();
  }, [isSignedIn, getToken, autoShow, hasChecked]);

  const handleDismiss = async () => {
    setOpen(false);
    onDismiss?.();

    // Mark entries as read
    try {
      const token = await getToken();
      if (token && entries.length > 0) {
        await markEntriesRead(token, entries[0].id);
      }
    } catch (error) {
      console.error("Failed to mark entries as read:", error);
    }
  };

  // Don't render anything if not signed in or no entries
  if (!isSignedIn || entries.length === 0) {
    return null;
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-primary" />
            What&apos;s New
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="max-h-[60vh]">
          <div className="space-y-4 pr-4">
            {entries.map((entry) => (
              <Link
                key={entry.id}
                href={`/changelog/${entry.slug}`}
                onClick={handleDismiss}
                className="block"
              >
                <article className="rounded-lg border p-4 transition-colors hover:bg-accent/50">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {entry.version && (
                        <span className="text-xs font-mono text-muted-foreground">
                          {entry.version}
                        </span>
                      )}
                      <Badge
                        variant="outline"
                        className={`text-xs ${getCategoryColor(entry.category)}`}
                      >
                        {getCategoryLabel(entry.category)}
                      </Badge>
                      {entry.is_major && (
                        <Badge variant="default" className="text-xs bg-primary">
                          Major
                        </Badge>
                      )}
                    </div>
                    {entry.published_at && (
                      <time className="text-xs text-muted-foreground whitespace-nowrap">
                        {formatRelativeDate(entry.published_at)}
                      </time>
                    )}
                  </div>

                  <h4 className="font-medium mb-1 group-hover:text-primary">
                    {entry.title}
                  </h4>
                  <p className="text-sm text-muted-foreground line-clamp-2">
                    {entry.summary}
                  </p>
                </article>
              </Link>
            ))}
          </div>
        </ScrollArea>

        <div className="flex items-center justify-between pt-2 border-t">
          <Link
            href="/changelog"
            onClick={handleDismiss}
            className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            View all updates
            <ExternalLink className="h-3 w-3" />
          </Link>
          <Button onClick={handleDismiss}>
            Got it
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Hook to manually trigger the What's New modal
 */
export function useWhatsNew() {
  const [showModal, setShowModal] = useState(false);

  return {
    showModal,
    openWhatsNew: () => setShowModal(true),
    closeWhatsNew: () => setShowModal(false),
  };
}
