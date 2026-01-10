'use client';

/**
 * Seat Management Component
 *
 * Interface for managing team seats:
 * - Add/remove seats
 * - Cost calculator
 * - Team member assignment preview
 */

import { useState } from 'react';
import { Minus, Plus, Users, AlertCircle, Calculator, ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

interface SeatManagementProps {
  currentSeats: number;
  usedSeats: number;
  minSeats: number;
  maxSeats: number;
  pricePerSeat: number;
  basePrice: number;
  planName: string;
  onUpdateSeats?: (newSeats: number) => Promise<void>;
  isLoading?: boolean;
}

export function SeatManagement({
  currentSeats,
  usedSeats,
  minSeats,
  maxSeats,
  pricePerSeat,
  basePrice,
  planName,
  onUpdateSeats,
  isLoading = false,
}: SeatManagementProps) {
  const [targetSeats, setTargetSeats] = useState(currentSeats);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isUpdating, setIsUpdating] = useState(false);

  const isUnlimited = maxSeats === -1;
  const canDecrease = targetSeats > Math.max(minSeats, usedSeats);
  const canIncrease = isUnlimited || targetSeats < maxSeats;
  const hasChanges = targetSeats !== currentSeats;

  const currentMonthlyTotal = basePrice + Math.max(0, currentSeats - minSeats) * pricePerSeat;
  const newMonthlyTotal = basePrice + Math.max(0, targetSeats - minSeats) * pricePerSeat;
  const priceDifference = newMonthlyTotal - currentMonthlyTotal;

  const handleIncrement = () => {
    if (canIncrease) {
      setTargetSeats(prev => prev + 1);
    }
  };

  const handleDecrement = () => {
    if (canDecrease) {
      setTargetSeats(prev => prev - 1);
    }
  };

  const handleConfirm = async () => {
    if (!hasChanges || !onUpdateSeats) return;

    setIsUpdating(true);
    try {
      await onUpdateSeats(targetSeats);
      setIsDialogOpen(false);
    } catch (error) {
      console.error('Failed to update seats:', error);
    } finally {
      setIsUpdating(false);
    }
  };

  const handleCancel = () => {
    setTargetSeats(currentSeats);
    setIsDialogOpen(false);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Users className="h-5 w-5 text-muted-foreground" />
            <CardTitle>Team Seats</CardTitle>
          </div>
          <Badge variant="secondary">{planName}</Badge>
        </div>
        <CardDescription>
          Manage the number of seats in your subscription
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Current Usage */}
        <div className="flex items-center justify-between rounded-lg bg-muted/50 p-4">
          <div>
            <p className="text-sm font-medium">Seats in use</p>
            <p className="text-2xl font-bold">{usedSeats} <span className="text-base font-normal text-muted-foreground">/ {currentSeats}</span></p>
          </div>
          <div className="text-right">
            <p className="text-sm font-medium">Available</p>
            <p className="text-2xl font-bold text-green-600 dark:text-green-400">{currentSeats - usedSeats}</p>
          </div>
        </div>

        {/* Seat Adjuster */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Adjust seats</span>
            <div className="flex items-center gap-3">
              <Button
                variant="outline"
                size="icon"
                onClick={handleDecrement}
                disabled={!canDecrease || isLoading}
              >
                <Minus className="h-4 w-4" />
              </Button>
              <span className="w-12 text-center text-xl font-bold">{targetSeats}</span>
              <Button
                variant="outline"
                size="icon"
                onClick={handleIncrement}
                disabled={!canIncrease || isLoading}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>

          {/* Warning for removing seats with users */}
          {targetSeats < usedSeats && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                You have {usedSeats} team members. Remove members before reducing seats below this number.
              </AlertDescription>
            </Alert>
          )}

          {/* Limits info */}
          <p className="text-xs text-muted-foreground text-center">
            {isUnlimited
              ? `Minimum ${minSeats} seat${minSeats !== 1 ? 's' : ''} required`
              : `${minSeats} to ${maxSeats} seats available on {planName}`
            }
          </p>
        </div>

        <Separator />

        {/* Cost Calculator */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Calculator className="h-4 w-4" />
            Cost breakdown
          </div>

          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Base price</span>
              <span>${basePrice}/mo</span>
            </div>
            {targetSeats > minSeats && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">
                  Additional seats ({targetSeats - minSeats} × ${pricePerSeat})
                </span>
                <span>${(targetSeats - minSeats) * pricePerSeat}/mo</span>
              </div>
            )}
            <Separator />
            <div className="flex justify-between font-medium">
              <span>New monthly total</span>
              <span>${newMonthlyTotal}/mo</span>
            </div>

            {hasChanges && (
              <div className={cn(
                'flex justify-between text-sm',
                priceDifference > 0 ? 'text-yellow-600' : 'text-green-600'
              )}>
                <span>Change from current</span>
                <span>
                  {priceDifference > 0 ? '+' : ''}{priceDifference === 0 ? 'No change' : `$${priceDifference}/mo`}
                </span>
              </div>
            )}
          </div>
        </div>
      </CardContent>

      <CardFooter>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <Button
              className="w-full"
              disabled={!hasChanges || isLoading}
            >
              {hasChanges ? (
                <>
                  Update to {targetSeats} seat{targetSeats !== 1 ? 's' : ''}
                  <ArrowRight className="ml-2 h-4 w-4" />
                </>
              ) : (
                'No changes'
              )}
            </Button>
          </DialogTrigger>

          <DialogContent>
            <DialogHeader>
              <DialogTitle>Confirm seat change</DialogTitle>
              <DialogDescription>
                You are {targetSeats > currentSeats ? 'adding' : 'removing'} {Math.abs(targetSeats - currentSeats)} seat{Math.abs(targetSeats - currentSeats) !== 1 ? 's' : ''}.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              <div className="flex items-center justify-center gap-4">
                <div className="text-center">
                  <p className="text-sm text-muted-foreground">Current</p>
                  <p className="text-2xl font-bold">{currentSeats}</p>
                </div>
                <ArrowRight className="h-5 w-5 text-muted-foreground" />
                <div className="text-center">
                  <p className="text-sm text-muted-foreground">New</p>
                  <p className="text-2xl font-bold">{targetSeats}</p>
                </div>
              </div>

              <div className="rounded-lg bg-muted p-4 text-center">
                <p className="text-sm text-muted-foreground">
                  {priceDifference > 0
                    ? `Your bill will increase by $${priceDifference}/month`
                    : priceDifference < 0
                      ? `Your bill will decrease by $${Math.abs(priceDifference)}/month`
                      : 'No change to your bill'
                  }
                </p>
                {priceDifference > 0 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Prorated charge applied immediately
                  </p>
                )}
              </div>
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={handleCancel} disabled={isUpdating}>
                Cancel
              </Button>
              <Button onClick={handleConfirm} disabled={isUpdating}>
                {isUpdating ? 'Updating...' : 'Confirm change'}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardFooter>
    </Card>
  );
}

/**
 * Compact seat indicator for headers
 */
export function SeatIndicator({
  used,
  total,
  className
}: {
  used: number;
  total: number;
  className?: string;
}) {
  const percentage = (used / total) * 100;
  const isNearLimit = percentage >= 80;

  return (
    <div className={cn('flex items-center gap-2 text-sm', className)}>
      <Users className="h-4 w-4 text-muted-foreground" />
      <span className={cn(
        'font-medium',
        isNearLimit && 'text-yellow-600 dark:text-yellow-400'
      )}>
        {used}/{total} seats
      </span>
    </div>
  );
}
