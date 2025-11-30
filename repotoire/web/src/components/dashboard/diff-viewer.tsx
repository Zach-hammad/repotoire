'use client';

import { useMemo } from 'react';
import { CodeChange } from '@/types';
import { cn } from '@/lib/utils';

interface DiffLine {
  type: 'added' | 'removed' | 'context';
  content: string;
  oldLineNumber?: number;
  newLineNumber?: number;
}

function computeDiff(original: string, fixed: string): DiffLine[] {
  const originalLines = original.split('\n');
  const fixedLines = fixed.split('\n');
  const diff: DiffLine[] = [];

  // Simple LCS-based diff algorithm
  const lcs: number[][] = [];
  for (let i = 0; i <= originalLines.length; i++) {
    lcs[i] = [];
    for (let j = 0; j <= fixedLines.length; j++) {
      if (i === 0 || j === 0) {
        lcs[i][j] = 0;
      } else if (originalLines[i - 1] === fixedLines[j - 1]) {
        lcs[i][j] = lcs[i - 1][j - 1] + 1;
      } else {
        lcs[i][j] = Math.max(lcs[i - 1][j], lcs[i][j - 1]);
      }
    }
  }

  // Backtrack to find the diff
  let i = originalLines.length;
  let j = fixedLines.length;
  const ops: Array<{ type: 'same' | 'add' | 'remove'; line: string; oi?: number; fi?: number }> = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && originalLines[i - 1] === fixedLines[j - 1]) {
      ops.unshift({ type: 'same', line: originalLines[i - 1], oi: i, fi: j });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || lcs[i][j - 1] >= lcs[i - 1][j])) {
      ops.unshift({ type: 'add', line: fixedLines[j - 1], fi: j });
      j--;
    } else {
      ops.unshift({ type: 'remove', line: originalLines[i - 1], oi: i });
      i--;
    }
  }

  // Convert to diff lines
  for (const op of ops) {
    if (op.type === 'same') {
      diff.push({
        type: 'context',
        content: op.line,
        oldLineNumber: op.oi,
        newLineNumber: op.fi,
      });
    } else if (op.type === 'remove') {
      diff.push({
        type: 'removed',
        content: op.line,
        oldLineNumber: op.oi,
      });
    } else {
      diff.push({
        type: 'added',
        content: op.line,
        newLineNumber: op.fi,
      });
    }
  }

  return diff;
}

interface DiffViewerProps {
  change: CodeChange;
  className?: string;
}

export function DiffViewer({ change, className }: DiffViewerProps) {
  const diff = useMemo(
    () => computeDiff(change.original_code, change.fixed_code),
    [change.original_code, change.fixed_code]
  );

  return (
    <div className={cn('rounded-lg border overflow-hidden', className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b bg-muted/50 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{change.file_path}</span>
          <span className="text-xs text-muted-foreground">
            Lines {change.start_line}-{change.end_line}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-red-500">
            -{diff.filter((l) => l.type === 'removed').length}
          </span>
          <span className="text-green-500">
            +{diff.filter((l) => l.type === 'added').length}
          </span>
        </div>
      </div>

      {/* Diff Content */}
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-sm">
          <tbody>
            {diff.map((line, index) => (
              <tr
                key={index}
                className={cn(
                  'border-b last:border-0',
                  line.type === 'added' && 'bg-green-500/10',
                  line.type === 'removed' && 'bg-red-500/10'
                )}
              >
                <td className="w-12 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                  {line.oldLineNumber || ''}
                </td>
                <td className="w-12 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                  {line.newLineNumber || ''}
                </td>
                <td className="w-6 select-none px-2 py-0.5 text-center">
                  {line.type === 'added' && (
                    <span className="text-green-500">+</span>
                  )}
                  {line.type === 'removed' && (
                    <span className="text-red-500">-</span>
                  )}
                </td>
                <td className="whitespace-pre px-2 py-0.5">
                  {line.content || ' '}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Description */}
      {change.description && (
        <div className="border-t bg-muted/30 px-4 py-2">
          <p className="text-sm text-muted-foreground">{change.description}</p>
        </div>
      )}
    </div>
  );
}

interface SplitDiffViewerProps {
  change: CodeChange;
  className?: string;
}

export function SplitDiffViewer({ change, className }: SplitDiffViewerProps) {
  const originalLines = change.original_code.split('\n');
  const fixedLines = change.fixed_code.split('\n');
  const maxLines = Math.max(originalLines.length, fixedLines.length);

  return (
    <div className={cn('rounded-lg border overflow-hidden', className)}>
      {/* Header */}
      <div className="grid grid-cols-2 border-b">
        <div className="border-r bg-red-500/5 px-4 py-2">
          <span className="font-mono text-sm text-red-500">Original</span>
        </div>
        <div className="bg-green-500/5 px-4 py-2">
          <span className="font-mono text-sm text-green-500">Fixed</span>
        </div>
      </div>

      {/* File path */}
      <div className="border-b bg-muted/50 px-4 py-2">
        <span className="font-mono text-sm">{change.file_path}</span>
        <span className="ml-2 text-xs text-muted-foreground">
          Lines {change.start_line}-{change.end_line}
        </span>
      </div>

      {/* Split view content */}
      <div className="grid grid-cols-2">
        {/* Original */}
        <div className="border-r overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <tbody>
              {originalLines.map((line, index) => (
                <tr key={index} className="border-b last:border-0 bg-red-500/5">
                  <td className="w-12 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                    {change.start_line + index}
                  </td>
                  <td className="whitespace-pre px-2 py-0.5">{line || ' '}</td>
                </tr>
              ))}
              {/* Padding rows if needed */}
              {Array.from({ length: maxLines - originalLines.length }).map(
                (_, index) => (
                  <tr key={`pad-${index}`} className="border-b last:border-0">
                    <td className="w-12 select-none border-r px-2 py-0.5"></td>
                    <td className="px-2 py-0.5">&nbsp;</td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        </div>

        {/* Fixed */}
        <div className="overflow-x-auto">
          <table className="w-full font-mono text-sm">
            <tbody>
              {fixedLines.map((line, index) => (
                <tr key={index} className="border-b last:border-0 bg-green-500/5">
                  <td className="w-12 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                    {change.start_line + index}
                  </td>
                  <td className="whitespace-pre px-2 py-0.5">{line || ' '}</td>
                </tr>
              ))}
              {/* Padding rows if needed */}
              {Array.from({ length: maxLines - fixedLines.length }).map(
                (_, index) => (
                  <tr key={`pad-${index}`} className="border-b last:border-0">
                    <td className="w-12 select-none border-r px-2 py-0.5"></td>
                    <td className="px-2 py-0.5">&nbsp;</td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Description */}
      {change.description && (
        <div className="border-t bg-muted/30 px-4 py-2">
          <p className="text-sm text-muted-foreground">{change.description}</p>
        </div>
      )}
    </div>
  );
}
