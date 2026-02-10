'use client';

import { useCallback } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Bell,
  CheckCircle2,
  AlertCircle,
  Lightbulb,
  AlertTriangle,
  Users,
  CreditCard,
  X,
  Check,
  Trash2,
  Loader2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'sonner';
import { mutate } from 'swr';
import {
  useNotifications,
  useMarkNotificationsRead,
  useMarkAllNotificationsRead,
  useDeleteNotifications,
  useDeleteAllNotifications,
  type NotificationItem,
} from '@/lib/hooks';

export type NotificationType = NotificationItem['type'];

const notificationIcons: Record<NotificationType, LucideIcon> = {
  analysis_complete: CheckCircle2,
  analysis_failed: X,
  new_finding: AlertCircle,
  fix_suggestion: Lightbulb,
  health_regression: AlertTriangle,
  team_invite: Users,
  team_role_change: Users,
  billing_event: CreditCard,
  system: Bell,
};

const notificationColors: Record<NotificationType, string> = {
  analysis_complete: 'text-success bg-success-muted',
  analysis_failed: 'text-error bg-error-muted',
  new_finding: 'text-warning bg-warning-muted',
  fix_suggestion: 'text-info-semantic bg-info-muted',
  health_regression: 'text-warning bg-warning-muted',
  team_invite: 'text-primary bg-primary/10',
  team_role_change: 'text-primary bg-primary/10',
  billing_event: 'text-success bg-success-muted',
  system: 'text-muted-foreground bg-muted',
};

interface NotificationItemComponentProps {
  notification: NotificationItem;
  onMarkRead: (id: string) => void;
  onDelete: (id: string) => void;
  isMarkingRead?: boolean;
  isDeleting?: boolean;
}

function NotificationItemComponent({
  notification,
  onMarkRead,
  onDelete,
  isMarkingRead,
  isDeleting,
}: NotificationItemComponentProps) {
  const Icon = notificationIcons[notification.type] || Bell;
  const colorClass = notificationColors[notification.type] || notificationColors.system;

  const handleClick = () => {
    if (notification.action_url && !notification.read) {
      onMarkRead(notification.id);
    }
    if (notification.action_url) {
      window.location.href = notification.action_url;
    }
  };

  return (
    <div
      className={cn(
        'group relative flex gap-3 p-3 rounded-lg transition-colors cursor-pointer',
        notification.read ? 'opacity-60' : 'bg-accent/50',
        notification.action_url && 'hover:bg-accent'
      )}
      onClick={handleClick}
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
          {formatDistanceToNow(new Date(notification.created_at), { addSuffix: true })}
        </p>
      </div>
      {/* Actions on hover */}
      <div className="absolute right-2 top-2 hidden group-hover:flex gap-1">
        {!notification.read && (
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            disabled={isMarkingRead}
            onClick={(e) => {
              e.stopPropagation();
              onMarkRead(notification.id);
            }}
          >
            {isMarkingRead ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Check className="h-3 w-3" />
            )}
            <span className="sr-only">Mark as read</span>
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-6 w-6 text-muted-foreground hover:text-destructive"
          disabled={isDeleting}
          onClick={(e) => {
            e.stopPropagation();
            onDelete(notification.id);
          }}
        >
          {isDeleting ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <Trash2 className="h-3 w-3" />
          )}
          <span className="sr-only">Delete</span>
        </Button>
      </div>
    </div>
  );
}

export function NotificationCenter() {
  const {
    notifications,
    unreadCount,
    isLoading,
    refresh,
  } = useNotifications(50);

  const { trigger: markRead, isMutating: isMarkingRead } = useMarkNotificationsRead();
  const { trigger: markAllRead, isMutating: isMarkingAllRead } = useMarkAllNotificationsRead();
  const { trigger: deleteNotification, isMutating: isDeleting } = useDeleteNotifications();
  const { trigger: deleteAll, isMutating: isDeletingAll } = useDeleteAllNotifications();

  const handleMarkRead = useCallback(async (id: string) => {
    try {
      await markRead([id]);
      // Optimistically update local state and refresh
      await refresh();
      // Also refresh the unread count
      await mutate('notifications-unread-count');
    } catch (error) {
      toast.error('Failed to mark notification as read');
      console.error('Failed to mark notification as read:', error);
    }
  }, [markRead, refresh]);

  const handleMarkAllRead = useCallback(async () => {
    try {
      await markAllRead();
      await refresh();
      await mutate('notifications-unread-count');
      toast.success('All notifications marked as read');
    } catch (error) {
      toast.error('Failed to mark all notifications as read');
      console.error('Failed to mark all notifications as read:', error);
    }
  }, [markAllRead, refresh]);

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteNotification([id]);
      await refresh();
      await mutate('notifications-unread-count');
    } catch (error) {
      toast.error('Failed to delete notification');
      console.error('Failed to delete notification:', error);
    }
  }, [deleteNotification, refresh]);

  const handleClearAll = useCallback(async () => {
    try {
      await deleteAll();
      await refresh();
      await mutate('notifications-unread-count');
      toast.success('All notifications cleared');
    } catch (error) {
      toast.error('Failed to clear notifications');
      console.error('Failed to clear notifications:', error);
    }
  }, [deleteAll, refresh]);

  return (
    <Popover>
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
                onClick={handleMarkAllRead}
                disabled={isMarkingAllRead}
              >
                {isMarkingAllRead ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : null}
                Mark all read
              </Button>
            )}
            {notifications.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="text-xs h-7 text-muted-foreground hover:text-destructive"
                onClick={handleClearAll}
                disabled={isDeletingAll}
              >
                {isDeletingAll ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : null}
                Clear all
              </Button>
            )}
          </div>
        </div>

        {/* Notification list */}
        {isLoading ? (
          <div className="py-12 text-center text-muted-foreground">
            <Loader2 className="h-8 w-8 mx-auto mb-3 animate-spin opacity-50" />
            <p className="text-sm">Loading notifications...</p>
          </div>
        ) : notifications.length > 0 ? (
          <ScrollArea className="h-[400px]">
            <div className="p-2 space-y-1">
              {notifications.map((notification) => (
                <NotificationItemComponent
                  key={notification.id}
                  notification={notification}
                  onMarkRead={handleMarkRead}
                  onDelete={handleDelete}
                  isMarkingRead={isMarkingRead}
                  isDeleting={isDeleting}
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
              onClick={() => {
                window.location.href = '/dashboard/settings/notifications';
              }}
            >
              Notification settings
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
