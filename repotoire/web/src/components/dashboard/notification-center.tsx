'use client';

import { useState } from 'react';
import { Bell, CheckCircle2, AlertCircle, Lightbulb, X, Check, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';

export type NotificationType = 'analysis_complete' | 'new_finding' | 'fix_suggestion' | 'system';

export interface Notification {
  id: string;
  type: NotificationType;
  title: string;
  message: string;
  timestamp: Date;
  read: boolean;
  actionUrl?: string;
  metadata?: {
    repoName?: string;
    findingCount?: number;
    severity?: 'critical' | 'high' | 'medium' | 'low';
  };
}

const notificationIcons: Record<NotificationType, React.ElementType> = {
  analysis_complete: CheckCircle2,
  new_finding: AlertCircle,
  fix_suggestion: Lightbulb,
  system: Bell,
};

const notificationColors: Record<NotificationType, string> = {
  analysis_complete: 'text-green-500 bg-green-500/10',
  new_finding: 'text-orange-500 bg-orange-500/10',
  fix_suggestion: 'text-blue-500 bg-blue-500/10',
  system: 'text-muted-foreground bg-muted',
};

// Mock notifications - in production, these would come from an API/WebSocket
const mockNotifications: Notification[] = [
  {
    id: '1',
    type: 'analysis_complete',
    title: 'Analysis Complete',
    message: 'repotoire/web finished analyzing with a health score of 87',
    timestamp: new Date(Date.now() - 1000 * 60 * 5), // 5 mins ago
    read: false,
    actionUrl: '/dashboard/repos/1',
    metadata: { repoName: 'repotoire/web' },
  },
  {
    id: '2',
    type: 'new_finding',
    title: '3 New Critical Findings',
    message: 'Found potential security issues in authentication module',
    timestamp: new Date(Date.now() - 1000 * 60 * 30), // 30 mins ago
    read: false,
    actionUrl: '/dashboard/findings?severity=critical',
    metadata: { findingCount: 3, severity: 'critical' },
  },
  {
    id: '3',
    type: 'fix_suggestion',
    title: 'AI Fix Available',
    message: 'Automated fix ready for "unused import" in utils.ts',
    timestamp: new Date(Date.now() - 1000 * 60 * 60 * 2), // 2 hours ago
    read: true,
    actionUrl: '/dashboard/fixes/1',
  },
  {
    id: '4',
    type: 'analysis_complete',
    title: 'Analysis Complete',
    message: 'api-server finished with 12 new findings',
    timestamp: new Date(Date.now() - 1000 * 60 * 60 * 24), // 1 day ago
    read: true,
    actionUrl: '/dashboard/repos/2',
    metadata: { repoName: 'api-server' },
  },
];

interface NotificationItemProps {
  notification: Notification;
  onMarkRead: (id: string) => void;
  onDelete: (id: string) => void;
}

function NotificationItem({ notification, onMarkRead, onDelete }: NotificationItemProps) {
  const Icon = notificationIcons[notification.type];
  const colorClass = notificationColors[notification.type];

  return (
    <div
      className={cn(
        'group relative flex gap-3 p-3 rounded-lg transition-colors',
        notification.read ? 'opacity-60' : 'bg-accent/50'
      )}
    >
      <div className={cn('shrink-0 p-2 rounded-full', colorClass)}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <p className="font-medium text-sm leading-tight">{notification.title}</p>
          {!notification.read && (
            <span className="shrink-0 h-2 w-2 rounded-full bg-primary" />
          )}
        </div>
        <p className="text-xs text-muted-foreground line-clamp-2">
          {notification.message}
        </p>
        <p className="text-xs text-muted-foreground/70">
          {formatDistanceToNow(notification.timestamp, { addSuffix: true })}
        </p>
      </div>
      {/* Actions on hover */}
      <div className="absolute right-2 top-2 hidden group-hover:flex gap-1">
        {!notification.read && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={(e) => {
              e.stopPropagation();
              onMarkRead(notification.id);
            }}
          >
            <Check className="h-3 w-3" />
            <span className="sr-only">Mark as read</span>
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-muted-foreground hover:text-destructive"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(notification.id);
          }}
        >
          <Trash2 className="h-3 w-3" />
          <span className="sr-only">Delete</span>
        </Button>
      </div>
    </div>
  );
}

export function NotificationCenter() {
  const [notifications, setNotifications] = useState<Notification[]>(mockNotifications);
  const [open, setOpen] = useState(false);

  const unreadCount = notifications.filter((n) => !n.read).length;

  const markAsRead = (id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );
  };

  const markAllAsRead = () => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  };

  const deleteNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  const clearAll = () => {
    setNotifications([]);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ''}`}
        >
          <Bell className="h-5 w-5" />
          {unreadCount > 0 && (
            <Badge
              variant="destructive"
              className="absolute -top-1 -right-1 h-5 w-5 p-0 flex items-center justify-center text-xs"
            >
              {unreadCount > 9 ? '9+' : unreadCount}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-96 p-0"
        sideOffset={8}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b">
          <h3 className="font-semibold">Notifications</h3>
          <div className="flex gap-1">
            {unreadCount > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="text-xs h-7"
                onClick={markAllAsRead}
              >
                Mark all read
              </Button>
            )}
            {notifications.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="text-xs h-7 text-muted-foreground hover:text-destructive"
                onClick={clearAll}
              >
                Clear all
              </Button>
            )}
          </div>
        </div>

        {/* Notification list */}
        {notifications.length > 0 ? (
          <ScrollArea className="h-[400px]">
            <div className="p-2 space-y-1">
              {notifications.map((notification) => (
                <NotificationItem
                  key={notification.id}
                  notification={notification}
                  onMarkRead={markAsRead}
                  onDelete={deleteNotification}
                />
              ))}
            </div>
          </ScrollArea>
        ) : (
          <div className="py-12 text-center text-muted-foreground">
            <Bell className="h-8 w-8 mx-auto mb-3 opacity-50" />
            <p className="text-sm">No notifications</p>
            <p className="text-xs mt-1">You're all caught up!</p>
          </div>
        )}

        {/* Footer */}
        {notifications.length > 0 && (
          <div className="border-t px-4 py-2">
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-xs"
              onClick={() => setOpen(false)}
            >
              View all notifications
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
