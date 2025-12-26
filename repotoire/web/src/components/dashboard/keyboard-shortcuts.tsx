'use client';

import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Kbd } from '@/components/ui/kbd';

interface Shortcut {
  keys: string[];
  description: string;
  action: () => void;
  category: 'navigation' | 'actions' | 'general';
}

export function KeyboardShortcuts() {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  const shortcuts: Shortcut[] = [
    // Navigation
    {
      keys: ['g', 'h'],
      description: 'Go to Overview',
      action: () => router.push('/dashboard'),
      category: 'navigation',
    },
    {
      keys: ['g', 'r'],
      description: 'Go to Repositories',
      action: () => router.push('/dashboard/repos'),
      category: 'navigation',
    },
    {
      keys: ['g', 'f'],
      description: 'Go to Findings',
      action: () => router.push('/dashboard/findings'),
      category: 'navigation',
    },
    {
      keys: ['g', 'x'],
      description: 'Go to AI Fixes',
      action: () => router.push('/dashboard/fixes'),
      category: 'navigation',
    },
    {
      keys: ['g', 's'],
      description: 'Go to Settings',
      action: () => router.push('/dashboard/settings'),
      category: 'navigation',
    },
    {
      keys: ['g', 'b'],
      description: 'Go to Billing',
      action: () => router.push('/dashboard/billing'),
      category: 'navigation',
    },
    // Actions
    {
      keys: ['c'],
      description: 'Connect new repository',
      action: () => router.push('/dashboard/repos/connect'),
      category: 'actions',
    },
    {
      keys: ['n'],
      description: 'New analysis',
      action: () => router.push('/dashboard/repos'),
      category: 'actions',
    },
    // General
    {
      keys: ['?'],
      description: 'Show keyboard shortcuts',
      action: () => setOpen(true),
      category: 'general',
    },
    {
      keys: ['Escape'],
      description: 'Close dialog / Cancel',
      action: () => setOpen(false),
      category: 'general',
    },
  ];

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in inputs
      const target = event.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return;
      }

      // Handle '?' key for help
      if (event.key === '?' && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        setOpen(true);
        return;
      }

      // Handle Escape
      if (event.key === 'Escape') {
        setOpen(false);
        return;
      }

      // Handle 'g' prefix shortcuts (vim-style)
      if (event.key === 'g' && !event.ctrlKey && !event.metaKey) {
        // Set up listener for the next key
        const handleSecondKey = (e: KeyboardEvent) => {
          const combo = shortcuts.find(
            (s) => s.keys[0] === 'g' && s.keys[1] === e.key
          );
          if (combo) {
            e.preventDefault();
            combo.action();
          }
          document.removeEventListener('keydown', handleSecondKey);
        };

        // Remove listener after timeout (500ms)
        setTimeout(() => {
          document.removeEventListener('keydown', handleSecondKey);
        }, 500);

        document.addEventListener('keydown', handleSecondKey);
        return;
      }

      // Handle single-key shortcuts
      const shortcut = shortcuts.find(
        (s) => s.keys.length === 1 && s.keys[0] === event.key
      );
      if (shortcut && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        shortcut.action();
      }
    },
    [shortcuts]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  const navigationShortcuts = shortcuts.filter((s) => s.category === 'navigation');
  const actionShortcuts = shortcuts.filter((s) => s.category === 'actions');
  const generalShortcuts = shortcuts.filter((s) => s.category === 'general');

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Keyboard Shortcuts</DialogTitle>
        </DialogHeader>
        <div className="space-y-6 py-4">
          {/* Navigation */}
          <div>
            <h4 className="text-sm font-medium text-muted-foreground mb-3">
              Navigation
            </h4>
            <div className="space-y-2">
              {navigationShortcuts.map((shortcut) => (
                <div
                  key={shortcut.description}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm">{shortcut.description}</span>
                  <div className="flex gap-1">
                    {shortcut.keys.map((key, i) => (
                      <span key={i} className="flex items-center gap-1">
                        <Kbd>{key}</Kbd>
                        {i < shortcut.keys.length - 1 && (
                          <span className="text-muted-foreground text-xs">then</span>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Actions */}
          <div>
            <h4 className="text-sm font-medium text-muted-foreground mb-3">
              Actions
            </h4>
            <div className="space-y-2">
              {actionShortcuts.map((shortcut) => (
                <div
                  key={shortcut.description}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm">{shortcut.description}</span>
                  <div className="flex gap-1">
                    {shortcut.keys.map((key, i) => (
                      <Kbd key={i}>{key}</Kbd>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* General */}
          <div>
            <h4 className="text-sm font-medium text-muted-foreground mb-3">
              General
            </h4>
            <div className="space-y-2">
              {generalShortcuts.map((shortcut) => (
                <div
                  key={shortcut.description}
                  className="flex items-center justify-between"
                >
                  <span className="text-sm">{shortcut.description}</span>
                  <div className="flex gap-1">
                    {shortcut.keys.map((key, i) => (
                      <Kbd key={i}>{key}</Kbd>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="text-xs text-muted-foreground text-center pt-2 border-t">
          Press <Kbd>?</Kbd> anywhere to show this dialog
        </div>
      </DialogContent>
    </Dialog>
  );
}
