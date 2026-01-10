/**
 * Friendly names, descriptions, and utilities for AI fixes pages.
 * Makes technical information accessible to non-technical users.
 */

import { FixConfidence, FixStatus, FixType } from '@/types';

// ==========================================
// Confidence Configuration
// ==========================================

export interface ConfidenceConfig {
  label: string;
  emoji: string;
  plainEnglish: string;
  description: string;
  shortHelp: string;
  whatItMeans: string;
  color: string;
  bgColor: string;
  borderColor: string;
}

export const confidenceConfig: Record<FixConfidence, ConfidenceConfig> = {
  high: {
    label: 'High',
    emoji: '🎯',
    plainEnglish: 'Very likely correct',
    description: 'The AI is confident this fix is accurate and safe to apply.',
    shortHelp: 'Well-tested pattern, safe to apply',
    whatItMeans: 'The AI found strong evidence for this fix: similar patterns in your codebase, relevant documentation, and best practices that support this change. You can apply it with minimal review.',
    color: 'text-green-800 dark:text-green-200',
    bgColor: 'bg-green-100 dark:bg-green-900',
    borderColor: 'border-green-500',
  },
  medium: {
    label: 'Medium',
    emoji: '🤔',
    plainEnglish: 'Probably correct',
    description: 'The AI believes this fix is good but recommends careful review.',
    shortHelp: 'Good suggestion, review before applying',
    whatItMeans: 'The AI found some supporting evidence for this fix, but there may be edge cases or alternatives worth considering. Review the changes carefully before approving.',
    color: 'text-yellow-800 dark:text-yellow-200',
    bgColor: 'bg-yellow-100 dark:bg-yellow-900',
    borderColor: 'border-yellow-500',
  },
  low: {
    label: 'Low',
    emoji: '⚠️',
    plainEnglish: 'Needs verification',
    description: 'The AI suggests this fix but recommends thorough testing before applying.',
    shortHelp: 'Experimental suggestion, test thoroughly',
    whatItMeans: 'The AI generated this fix based on limited context or patterns. Thoroughly review the code changes, understand the implications, and test in a safe environment before applying.',
    color: 'text-red-800 dark:text-red-200',
    bgColor: 'bg-red-100 dark:bg-red-900',
    borderColor: 'border-red-500',
  },
};

// ==========================================
// Status Configuration
// ==========================================

export interface StatusConfig {
  label: string;
  emoji: string;
  plainEnglish: string;
  description: string;
  nextAction: string;
  category: 'review' | 'approved' | 'closed';
}

export const statusConfig: Record<FixStatus, StatusConfig> = {
  pending: {
    label: 'Pending Review',
    emoji: '🔵',
    plainEnglish: 'Waiting for your review',
    description: 'This fix is waiting for you to review and approve or reject it.',
    nextAction: 'Run preview to test the fix, then approve or reject it.',
    category: 'review',
  },
  approved: {
    label: 'Approved',
    emoji: '✅',
    plainEnglish: 'Ready to apply',
    description: 'You approved this fix. It can now be applied to your codebase.',
    nextAction: 'Click "Apply Fix" to implement the changes in your code.',
    category: 'approved',
  },
  rejected: {
    label: 'Rejected',
    emoji: '❌',
    plainEnglish: 'Not applying',
    description: 'You decided not to use this fix.',
    nextAction: 'No action needed. This fix will not be applied.',
    category: 'closed',
  },
  applied: {
    label: 'Applied',
    emoji: '🎉',
    plainEnglish: 'Done!',
    description: 'This fix has been successfully applied to your codebase.',
    nextAction: 'The fix is complete. Consider running your tests to verify.',
    category: 'closed',
  },
  failed: {
    label: 'Failed',
    emoji: '💥',
    plainEnglish: 'Something went wrong',
    description: 'The fix could not be applied due to an error.',
    nextAction: 'Check the error details and try again, or reject this fix.',
    category: 'closed',
  },
  stale: {
    label: 'Stale',
    emoji: '⏰',
    plainEnglish: 'Code has changed',
    description: 'The target code has been modified since this fix was generated.',
    nextAction: 'Regenerate the fix to get an updated version.',
    category: 'closed',
  },
};

// ==========================================
// Fix Type Configuration
// ==========================================

export interface FixTypeConfig {
  label: string;
  emoji: string;
  description: string;
  example: string;
}

export const fixTypeConfig: Record<FixType, FixTypeConfig> = {
  refactor: {
    label: 'Refactor',
    emoji: '🔧',
    description: 'Restructures code to improve readability and maintainability without changing behavior.',
    example: 'Breaking a large function into smaller, focused functions.',
  },
  simplify: {
    label: 'Simplify',
    emoji: '✨',
    description: 'Reduces complexity by removing unnecessary code or using cleaner patterns.',
    example: 'Replacing nested if-else statements with early returns.',
  },
  extract: {
    label: 'Extract',
    emoji: '📤',
    description: 'Pulls out reusable code into separate functions, classes, or modules.',
    example: 'Moving repeated logic into a shared utility function.',
  },
  rename: {
    label: 'Rename',
    emoji: '🏷️',
    description: 'Changes names of variables, functions, or classes to be more descriptive.',
    example: 'Renaming "x" to "userAge" for clarity.',
  },
  remove: {
    label: 'Remove',
    emoji: '🗑️',
    description: 'Deletes unused, dead, or redundant code.',
    example: 'Removing an unused import or unreachable code block.',
  },
  security: {
    label: 'Security Fix',
    emoji: '🔒',
    description: 'Addresses potential security vulnerabilities in the code.',
    example: 'Sanitizing user input to prevent injection attacks.',
  },
  type_hint: {
    label: 'Add Types',
    emoji: '📝',
    description: 'Adds type annotations to improve code clarity and catch errors early.',
    example: 'Adding return type hints to function signatures.',
  },
  documentation: {
    label: 'Documentation',
    emoji: '📚',
    description: 'Adds or improves code comments and documentation.',
    example: 'Adding docstrings to explain function parameters and return values.',
  },
};

// ==========================================
// Jargon Explanations
// ==========================================

export interface JargonExplanation {
  term: string;
  plainEnglish: string;
  fullExplanation: string;
}

export const jargonExplanations: Record<string, JargonExplanation> = {
  rag_context: {
    term: 'RAG Context',
    plainEnglish: 'Related code snippets',
    fullExplanation: 'RAG (Retrieval-Augmented Generation) means the AI searched your codebase to find similar code patterns and examples. These "contexts" are code snippets the AI used to understand how to fix the issue correctly for your specific project.',
  },
  evidence: {
    term: 'Evidence',
    plainEnglish: 'Supporting information',
    fullExplanation: 'Evidence is the information the AI gathered to justify this fix. It includes similar patterns found in your code, documentation references, and best practices from programming standards. More evidence generally means higher confidence.',
  },
  rationale: {
    term: 'AI Rationale',
    plainEnglish: 'Why the AI suggested this',
    fullExplanation: 'The rationale is the AI\'s explanation of why it chose this particular fix. It describes the problem identified, the approach taken, and why this solution is appropriate for your codebase.',
  },
  similar_patterns: {
    term: 'Similar Patterns',
    plainEnglish: 'Code examples from your project',
    fullExplanation: 'These are existing pieces of code in your project that follow a similar pattern to what the fix is implementing. The AI uses these to ensure the fix matches your codebase\'s style and conventions.',
  },
  best_practices: {
    term: 'Best Practices',
    plainEnglish: 'Recommended coding standards',
    fullExplanation: 'Best practices are widely-accepted guidelines for writing good code. The AI references these to ensure the fix follows industry standards for readability, maintainability, and security.',
  },
  documentation_refs: {
    term: 'Documentation References',
    plainEnglish: 'Official docs and guides',
    fullExplanation: 'Links to official documentation, style guides, or technical references that support the fix. These help you verify the AI\'s suggestions against authoritative sources.',
  },
  preview: {
    term: 'Preview',
    plainEnglish: 'Test run in a safe environment',
    fullExplanation: 'Preview runs the fix in an isolated sandbox environment to verify it works correctly without affecting your actual code. It checks syntax, runs tests, and validates the changes are safe to apply.',
  },
  sandbox: {
    term: 'Sandbox',
    plainEnglish: 'Safe testing environment',
    fullExplanation: 'A sandbox is an isolated environment where code runs without access to your real files or secrets. It\'s like a "playground" where the AI can safely test fixes before you apply them to your actual codebase.',
  },
};

// ==========================================
// Workflow Guide
// ==========================================

export interface WorkflowStep {
  step: number;
  title: string;
  description: string;
  icon: string;
  status: 'completed' | 'current' | 'upcoming';
}

export function getWorkflowSteps(fixStatus: FixStatus, hasRunPreview: boolean): WorkflowStep[] {
  const steps: WorkflowStep[] = [
    {
      step: 1,
      title: 'Review Changes',
      description: 'Look at the code diff to understand what will change.',
      icon: '👀',
      status: 'completed',
    },
    {
      step: 2,
      title: 'Run Preview',
      description: 'Test the fix in a safe sandbox environment.',
      icon: '🧪',
      status: hasRunPreview ? 'completed' : (fixStatus === 'pending' ? 'current' : 'completed'),
    },
    {
      step: 3,
      title: 'Approve or Reject',
      description: 'Decide whether to use this fix.',
      icon: '✅',
      status: fixStatus === 'pending' && hasRunPreview ? 'current' : (fixStatus === 'pending' ? 'upcoming' : 'completed'),
    },
    {
      step: 4,
      title: 'Apply Fix',
      description: 'Implement the changes in your codebase.',
      icon: '🚀',
      status: fixStatus === 'approved' ? 'current' : (fixStatus === 'applied' ? 'completed' : 'upcoming'),
    },
  ];

  return steps;
}

// ==========================================
// Helper Functions
// ==========================================

export function getConfidenceTooltip(confidence: FixConfidence): string {
  const config = confidenceConfig[confidence];
  return `${config.emoji} ${config.plainEnglish}\n\n${config.whatItMeans}`;
}

export function getStatusTooltip(status: FixStatus): string {
  const config = statusConfig[status];
  return `${config.emoji} ${config.plainEnglish}\n\n${config.description}\n\nNext: ${config.nextAction}`;
}

export function getFixTypeTooltip(fixType: FixType): string {
  const config = fixTypeConfig[fixType];
  return `${config.emoji} ${config.description}\n\nExample: ${config.example}`;
}

export function getJargonExplanation(term: string): JargonExplanation | undefined {
  return jargonExplanations[term];
}

// ==========================================
// Batch Action Warnings
// ==========================================

export interface BatchActionWarning {
  title: string;
  description: string;
  confirmText: string;
  cancelText: string;
}

export const batchActionWarnings: Record<string, BatchActionWarning> = {
  approve: {
    title: 'Approve Multiple Fixes',
    description: 'You are about to approve multiple fixes at once. Each fix will still need to be applied individually. Make sure you have reviewed all selected fixes before approving.',
    confirmText: 'Approve All',
    cancelText: 'Cancel',
  },
  reject: {
    title: 'Reject Multiple Fixes',
    description: 'You are about to reject multiple fixes. This action helps the AI learn from your feedback. Please provide a reason for rejection.',
    confirmText: 'Reject All',
    cancelText: 'Cancel',
  },
};

// ==========================================
// User-Friendly Error Messages
// ==========================================

export function getFriendlyFixErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message.toLowerCase();

    if (message.includes('preview') && message.includes('required')) {
      return 'Please run a preview first to verify the fix works correctly.';
    }
    if (message.includes('already applied')) {
      return 'This fix has already been applied to your codebase.';
    }
    if (message.includes('conflict') || message.includes('merge')) {
      return 'The code has changed since this fix was generated. Try regenerating the fix.';
    }
    if (message.includes('syntax')) {
      return 'The fix contains a syntax error. This has been reported for improvement.';
    }
    if (message.includes('timeout')) {
      return 'The operation took too long. Please try again.';
    }
    if (message.includes('sandbox') || message.includes('e2b')) {
      return 'The testing environment is temporarily unavailable. Please try again in a moment.';
    }
    if (message.includes('401') || message.includes('unauthorized')) {
      return 'Your session expired. Please log in again.';
    }
    if (message.includes('403') || message.includes('forbidden')) {
      return "You don't have permission to perform this action.";
    }
    if (message.includes('404') || message.includes('not found')) {
      return "This fix couldn't be found. It may have been deleted.";
    }
  }
  return 'Something went wrong. Please try again.';
}
