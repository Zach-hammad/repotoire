'use client';

/**
 * Invoice History Component
 *
 * Display past invoices with:
 * - Invoice list with status
 * - Download links
 * - Payment method used
 */

import { useState } from 'react';
import {
  Receipt,
  Download,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  CreditCard,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

type InvoiceStatus = 'paid' | 'open' | 'void' | 'uncollectible' | 'draft';

interface Invoice {
  id: string;
  number: string;
  date: string;
  dueDate?: string;
  amount: number;
  currency: string;
  status: InvoiceStatus;
  pdfUrl?: string;
  hostedUrl?: string;
  paymentMethod?: {
    brand: string;
    last4: string;
  };
  description?: string;
}

interface InvoiceHistoryProps {
  invoices: Invoice[];
  isLoading?: boolean;
  onLoadMore?: () => void;
  hasMore?: boolean;
  className?: string;
}

const statusConfig: Record<InvoiceStatus, { label: string; icon: React.ReactNode; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  paid: {
    label: 'Paid',
    icon: <CheckCircle2 className="h-3 w-3" />,
    variant: 'default',
  },
  open: {
    label: 'Open',
    icon: <Clock className="h-3 w-3" />,
    variant: 'secondary',
  },
  void: {
    label: 'Void',
    icon: <XCircle className="h-3 w-3" />,
    variant: 'outline',
  },
  uncollectible: {
    label: 'Uncollectible',
    icon: <AlertCircle className="h-3 w-3" />,
    variant: 'destructive',
  },
  draft: {
    label: 'Draft',
    icon: <Clock className="h-3 w-3" />,
    variant: 'outline',
  },
};

function formatCurrency(amount: number, currency: string) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency.toUpperCase(),
  }).format(amount / 100);
}

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

function InvoiceRow({ invoice }: { invoice: Invoice }) {
  const [isOpen, setIsOpen] = useState(false);
  const status = statusConfig[invoice.status];

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <TableRow className="group">
        <TableCell>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-auto p-0 hover:bg-transparent">
              {isOpen ? (
                <ChevronUp className="h-4 w-4 mr-2" />
              ) : (
                <ChevronDown className="h-4 w-4 mr-2" />
              )}
              <span className="font-mono">{invoice.number}</span>
            </Button>
          </CollapsibleTrigger>
        </TableCell>
        <TableCell>{formatDate(invoice.date)}</TableCell>
        <TableCell className="font-medium">
          {formatCurrency(invoice.amount, invoice.currency)}
        </TableCell>
        <TableCell>
          <Badge variant={status.variant} className="gap-1">
            {status.icon}
            {status.label}
          </Badge>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
            {invoice.pdfUrl && (
              <Button variant="ghost" size="icon" asChild>
                <a href={invoice.pdfUrl} download title="Download PDF">
                  <Download className="h-4 w-4" />
                </a>
              </Button>
            )}
            {invoice.hostedUrl && (
              <Button variant="ghost" size="icon" asChild>
                <a href={invoice.hostedUrl} target="_blank" rel="noopener noreferrer" title="View online">
                  <ExternalLink className="h-4 w-4" />
                </a>
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>
      <CollapsibleContent asChild>
        <TableRow className="bg-muted/50">
          <TableCell colSpan={5} className="p-4">
            <div className="grid gap-4 md:grid-cols-2">
              {invoice.description && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Description</p>
                  <p className="text-sm">{invoice.description}</p>
                </div>
              )}
              {invoice.dueDate && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Due Date</p>
                  <p className="text-sm">{formatDate(invoice.dueDate)}</p>
                </div>
              )}
              {invoice.paymentMethod && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Payment Method</p>
                  <div className="flex items-center gap-2 text-sm">
                    <CreditCard className="h-4 w-4 text-muted-foreground" />
                    <span className="capitalize">{invoice.paymentMethod.brand}</span>
                    <span className="font-mono">••{invoice.paymentMethod.last4}</span>
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2 md:col-span-2 md:justify-end">
                {invoice.pdfUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={invoice.pdfUrl} download>
                      <Download className="h-4 w-4 mr-2" />
                      Download PDF
                    </a>
                  </Button>
                )}
                {invoice.hostedUrl && (
                  <Button variant="outline" size="sm" asChild>
                    <a href={invoice.hostedUrl} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="h-4 w-4 mr-2" />
                      View Invoice
                    </a>
                  </Button>
                )}
              </div>
            </div>
          </TableCell>
        </TableRow>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function InvoiceHistory({
  invoices,
  isLoading = false,
  onLoadMore,
  hasMore = false,
  className,
}: InvoiceHistoryProps) {
  if (isLoading && invoices.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Receipt className="h-5 w-5" />
            Invoice History
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="animate-pulse flex items-center gap-4">
                <div className="h-4 w-24 bg-muted rounded" />
                <div className="h-4 w-20 bg-muted rounded" />
                <div className="h-4 w-16 bg-muted rounded" />
                <div className="h-6 w-16 bg-muted rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (invoices.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Receipt className="h-5 w-5" />
            Invoice History
          </CardTitle>
          <CardDescription>Your billing history will appear here</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Receipt className="h-12 w-12 text-muted-foreground/50 mb-4" />
            <p className="text-muted-foreground">No invoices yet</p>
            <p className="text-sm text-muted-foreground">
              Invoices will appear here after your first payment
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Calculate totals
  const paidTotal = invoices
    .filter(inv => inv.status === 'paid')
    .reduce((sum, inv) => sum + inv.amount, 0);
  const currency = invoices[0]?.currency || 'usd';

  return (
    <Card className={className}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Receipt className="h-5 w-5" />
              Invoice History
            </CardTitle>
            <CardDescription>
              {invoices.length} invoice{invoices.length !== 1 ? 's' : ''} • {formatCurrency(paidTotal, currency)} paid
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Invoice</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-[100px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invoices.map((invoice) => (
                <InvoiceRow key={invoice.id} invoice={invoice} />
              ))}
            </TableBody>
          </Table>
        </div>

        {hasMore && (
          <div className="mt-4 text-center">
            <Button
              variant="outline"
              onClick={onLoadMore}
              disabled={isLoading}
            >
              {isLoading ? 'Loading...' : 'Load More'}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Compact invoice summary for dashboards
 */
export function InvoiceSummary({
  lastInvoice,
  totalPaid,
  currency = 'usd',
  className,
}: {
  lastInvoice?: Invoice;
  totalPaid: number;
  currency?: string;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center justify-between text-sm', className)}>
      <div>
        <p className="text-muted-foreground">Last invoice</p>
        <p className="font-medium">
          {lastInvoice ? formatCurrency(lastInvoice.amount, lastInvoice.currency) : 'N/A'}
        </p>
      </div>
      <div className="text-right">
        <p className="text-muted-foreground">Total paid</p>
        <p className="font-medium">{formatCurrency(totalPaid, currency)}</p>
      </div>
    </div>
  );
}
