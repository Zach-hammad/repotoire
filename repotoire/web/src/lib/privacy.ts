import { createHash } from 'node:crypto';

export function hashIdentifier(value: string): string {
  return createHash('sha256').update(value.trim().toLowerCase()).digest('hex');
}

export function summarizeTextLength(value: string | null | undefined): number {
  return value ? value.length : 0;
}
