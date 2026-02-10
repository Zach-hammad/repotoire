/**
 * Friendly names, descriptions, and utilities for findings pages.
 * Makes technical information accessible to non-technical users.
 */

import { FindingStatus, Severity } from '@/types';

// ==========================================
// Severity Configuration
// ==========================================

export interface SeverityConfig {
  label: string;
  emoji: string;
  plainEnglish: string;
  description: string;
  shortHelp: string;
  color: string;
  bgColor: string;
  borderColor: string;
}

export const severityConfig: Record<Severity, SeverityConfig> = {
  critical: {
    label: 'Critical',
    emoji: '🚨',
    plainEnglish: 'Dangerous - Fix right away',
    description: 'Requires immediate attention. May cause security vulnerabilities, system failures, or data loss.',
    shortHelp: 'Security risks or system breakage',
    color: 'text-red-800 dark:text-red-200',
    bgColor: 'bg-red-100 dark:bg-red-900',
    borderColor: 'border-red-500',
  },
  high: {
    label: 'High',
    emoji: '⚠️',
    plainEnglish: 'Important - Fix soon',
    description: 'Should be addressed soon. Can lead to significant technical debt, maintenance issues, or performance problems.',
    shortHelp: 'Performance, maintenance, or reliability problems',
    color: 'text-orange-800 dark:text-orange-200',
    bgColor: 'bg-orange-100 dark:bg-orange-900',
    borderColor: 'border-orange-500',
  },
  medium: {
    label: 'Medium',
    emoji: '⚡',
    plainEnglish: 'Helpful - Fix eventually',
    description: 'Worth addressing in regular development cycles. Improves code quality and maintainability.',
    shortHelp: 'Code quality improvements for team',
    color: 'text-yellow-800 dark:text-yellow-200',
    bgColor: 'bg-yellow-100 dark:bg-yellow-900',
    borderColor: 'border-yellow-500',
  },
  low: {
    label: 'Low',
    emoji: '💡',
    plainEnglish: 'Nice to have - Fix when nearby',
    description: 'Minor improvement opportunity. Can be addressed when working in the affected area.',
    shortHelp: 'Small improvements you can fix anytime',
    color: 'text-blue-800 dark:text-blue-200',
    bgColor: 'bg-blue-100 dark:bg-blue-900',
    borderColor: 'border-blue-500',
  },
  info: {
    label: 'Info',
    emoji: 'ℹ️',
    plainEnglish: 'Just so you know',
    description: 'Informational note for awareness. Consider addressing for alignment with best practices.',
    shortHelp: 'Notes and suggestions',
    color: 'text-gray-800 dark:text-gray-200',
    bgColor: 'bg-gray-100 dark:bg-gray-900',
    borderColor: 'border-gray-500',
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
  category: 'progress' | 'closed';
}

export const statusConfig: Record<FindingStatus, StatusConfig> = {
  open: {
    label: 'New',
    emoji: '🔵',
    plainEnglish: 'Not started',
    description: 'Just discovered, needs review',
    category: 'progress',
  },
  acknowledged: {
    label: 'Noted',
    emoji: '👀',
    plainEnglish: 'Team aware',
    description: 'Team knows about it, may fix later',
    category: 'progress',
  },
  in_progress: {
    label: 'Looking at It',
    emoji: '🔄',
    plainEnglish: 'Being fixed',
    description: 'Someone is working on it now',
    category: 'progress',
  },
  resolved: {
    label: 'Fixed',
    emoji: '✅',
    plainEnglish: 'Done',
    description: 'Issue has been successfully fixed',
    category: 'progress',
  },
  wontfix: {
    label: "Won't Fix",
    emoji: '🚫',
    plainEnglish: 'Not fixing',
    description: 'Team decided not to fix (acceptable risk)',
    category: 'closed',
  },
  false_positive: {
    label: 'Not a Problem',
    emoji: '❌',
    plainEnglish: 'Not a real issue',
    description: 'System made a mistake, not a real issue',
    category: 'closed',
  },
  duplicate: {
    label: 'Already Found',
    emoji: '📋',
    plainEnglish: 'Duplicate',
    description: 'Same issue already reported elsewhere',
    category: 'closed',
  },
};

// ==========================================
// Detector Configuration
// ==========================================

export interface DetectorConfig {
  friendlyName: string;
  description: string;
  category: string;
  icon?: string;
}

export const detectorConfig: Record<string, DetectorConfig> = {
  // Design Issues
  GodClassDetector: {
    friendlyName: 'Overloaded Class',
    description: 'A class that handles too many responsibilities and is doing too much work',
    category: 'Design',
  },
  FeatureEnvyDetector: {
    friendlyName: 'Misplaced Method',
    description: 'A method that uses other classes more than its own, suggesting it belongs elsewhere',
    category: 'Design',
  },
  LazyClassDetector: {
    friendlyName: 'Underutilized Class',
    description: 'A class with very few methods doing minimal work; may be unnecessary',
    category: 'Design',
  },
  RefusedBequestDetector: {
    friendlyName: 'Broken Inheritance',
    description: 'Child class inherits but doesn\'t use parent functionality',
    category: 'Design',
  },

  // Architecture Issues
  CircularDependencyDetector: {
    friendlyName: 'Circular Import',
    description: 'Files import each other creating circular references that cause coupling',
    category: 'Architecture',
  },
  ArchitecturalBottleneckDetector: {
    friendlyName: 'Critical Bottleneck',
    description: 'A function sits on many execution paths; changes could impact system',
    category: 'Architecture',
  },
  CoreUtilityDetector: {
    friendlyName: 'Core Utility Module',
    description: 'A utility module relied on by many parts of the codebase',
    category: 'Architecture',
  },
  DegreeCentralityDetector: {
    friendlyName: 'Highly Connected',
    description: 'A module with many connections; a central hub in dependencies',
    category: 'Architecture',
  },
  ShotgunSurgeryDetector: {
    friendlyName: 'High-Impact Class',
    description: 'Changes to this class require updates throughout the codebase',
    category: 'Architecture',
  },
  ModuleCohesionDetector: {
    friendlyName: 'Low Module Cohesion',
    description: 'Module functions don\'t work well together toward a single purpose',
    category: 'Architecture',
  },

  // Code Quality
  DeadCodeDetector: {
    friendlyName: 'Unused Code',
    description: 'Functions, classes, or code blocks that are never called or used',
    category: 'Code Quality',
  },
  TrulyUnusedImportsDetector: {
    friendlyName: 'Unnecessary Import',
    description: 'Modules are imported but never used',
    category: 'Code Quality',
  },
  TypeHintCoverageDetector: {
    friendlyName: 'Missing Type Hints',
    description: 'Functions lack type annotations for parameters or return values',
    category: 'Code Quality',
  },
  LongParameterListDetector: {
    friendlyName: 'Too Many Parameters',
    description: 'A function has too many parameters, suggesting poor design',
    category: 'Code Quality',
  },
  MessageChainDetector: {
    friendlyName: 'Deep Method Chain',
    description: 'Long chains of method calls (4+ levels) violating the Law of Demeter',
    category: 'Code Quality',
  },
  DataClumpsDetector: {
    friendlyName: 'Repeated Parameter Groups',
    description: 'The same parameters appear together frequently across functions',
    category: 'Code Quality',
  },

  // Code Duplication
  JscpdDetector: {
    friendlyName: 'Duplicate Code',
    description: 'Identical or near-identical code blocks exist elsewhere',
    category: 'Code Duplication',
  },
  DuplicateRustDetector: {
    friendlyName: 'Rust Code Duplication',
    description: 'Duplicate patterns in Rust code',
    category: 'Code Duplication',
  },

  // Performance
  AsyncAntipatternDetector: {
    friendlyName: 'Async Problem',
    description: 'Blocking calls or inefficient patterns in asynchronous code',
    category: 'Performance',
  },
  GeneratorMisuseDetector: {
    friendlyName: 'Generator Problem',
    description: 'Generators used incorrectly (unconsumed, not iterated, etc.)',
    category: 'Performance',
  },

  // Coupling
  InappropriateIntimacyDetector: {
    friendlyName: 'Overly Coupled Classes',
    description: 'Two classes access each other\'s internals excessively',
    category: 'Coupling',
  },
  MiddleManDetector: {
    friendlyName: 'Unnecessary Wrapper',
    description: 'A class mostly delegates to others without adding value',
    category: 'Coupling',
  },

  // Maintenance
  SATDDetector: {
    friendlyName: 'Technical Debt Marker',
    description: 'Comments marking known issues (TODO, FIXME, HACK, BUG)',
    category: 'Maintenance',
  },
  TestSmellDetector: {
    friendlyName: 'Test Quality Issue',
    description: 'Test code exhibits smells like duplication, poor naming, etc.',
    category: 'Maintenance',
  },
  VultureDetector: {
    friendlyName: 'Unused Code (Vulture)',
    description: 'Unused code detected via AST analysis',
    category: 'Maintenance',
  },

  // Security
  BanditDetector: {
    friendlyName: 'Security Issue',
    description: 'Potential security issues detected',
    category: 'Security',
  },
  SemgrepDetector: {
    friendlyName: 'Advanced Security Issue',
    description: 'Pattern-based security checks (OWASP Top 10, etc.)',
    category: 'Security',
  },
  TaintDetector: {
    friendlyName: 'Taint Flow Issue',
    description: 'Untrusted data flows to sensitive operations',
    category: 'Security',
  },

  // Linting & Style
  RuffLintDetector: {
    friendlyName: 'Code Style Issue',
    description: 'Code style, formatting, or basic linting issues (400+ rule types)',
    category: 'Style',
  },
  RuffImportDetector: {
    friendlyName: 'Import Style Issue',
    description: 'Import ordering or style issues',
    category: 'Style',
  },
  PylintDetector: {
    friendlyName: 'Code Quality Issue',
    description: 'Pylint-specific quality checks',
    category: 'Quality',
  },
  MypyDetector: {
    friendlyName: 'Type Checking Error',
    description: 'Type checking violations found by mypy',
    category: 'Types',
  },

  // Complexity
  RadonDetector: {
    friendlyName: 'High Complexity',
    description: 'Function or module is too complex to understand/maintain',
    category: 'Complexity',
  },

  // ML/AI Detectors
  GraphSAGEDetector: {
    friendlyName: 'Anomaly Detected',
    description: 'Machine learning detected unusual code patterns',
    category: 'AI Analysis',
  },
  MLBugDetector: {
    friendlyName: 'Bug Risk',
    description: 'ML model predicted this code likely contains bugs',
    category: 'AI Analysis',
  },
  MultimodalDetector: {
    friendlyName: 'Multi-Signal Issue',
    description: 'Issue detected across multiple analysis methods',
    category: 'AI Analysis',
  },
  InfluentialCodeDetector: {
    friendlyName: 'Influential Code',
    description: 'Code that significantly impacts system behavior when changed',
    category: 'AI Analysis',
  },
};

// Get friendly detector name with fallback
export function getDetectorFriendlyName(detector: string): string {
  const config = detectorConfig[detector];
  if (config) {
    return config.friendlyName;
  }
  // Fallback: remove "Detector" suffix and add spaces before capitals
  return detector
    .replace('Detector', '')
    .replace(/([A-Z])/g, ' $1')
    .trim();
}

// Get detector description with fallback
export function getDetectorDescription(detector: string): string {
  const config = detectorConfig[detector];
  if (config) {
    return config.description;
  }
  return 'Detects potential issues in your code';
}

// Get detector category with fallback
export function getDetectorCategory(detector: string): string {
  const config = detectorConfig[detector];
  if (config) {
    return config.category;
  }
  return 'Other';
}

// ==========================================
// Filter Presets
// ==========================================

export interface FilterPreset {
  id: string;
  label: string;
  emoji: string;
  description: string;
  filters: {
    severity?: Severity[];
    status?: FindingStatus[];
    detectors?: string[];
  };
  sortBy?: string;
  sortDirection?: 'asc' | 'desc';
}

export const filterPresets: FilterPreset[] = [
  {
    id: 'important',
    label: 'Important Stuff',
    emoji: '🔥',
    description: 'Critical and high severity issues',
    filters: { severity: ['critical', 'high'] },
    sortBy: 'severity',
    sortDirection: 'desc',
  },
  {
    id: 'fix-first',
    label: 'Fix First',
    emoji: '⚡',
    description: 'High-impact issues you can fix quickly',
    filters: { severity: ['critical', 'high', 'medium'] },
    sortBy: 'created_at',
    sortDirection: 'desc',
  },
  {
    id: 'security',
    label: 'Security Risks',
    emoji: '🔒',
    description: 'Security, type safety, and injection risks',
    filters: {
      detectors: ['BanditDetector', 'SemgrepDetector', 'TaintDetector'],
    },
  },
  {
    id: 'quick-wins',
    label: 'Quick Wins',
    emoji: '✨',
    description: 'Low-effort issues you can fix today',
    filters: { severity: ['low', 'info'] },
    sortBy: 'created_at',
    sortDirection: 'desc',
  },
  {
    id: 'needs-review',
    label: 'Needs Review',
    emoji: '👀',
    description: 'New findings waiting for review',
    filters: { status: ['open'] },
    sortBy: 'created_at',
    sortDirection: 'desc',
  },
];

// ==========================================
// Graph Context Formatting
// ==========================================

export interface FormattedContextItem {
  label: string;
  value: string;
  isImportant: boolean;
}

// Format graph context for human-readable display
export function formatGraphContext(context: Record<string, unknown>): FormattedContextItem[] {
  const items: FormattedContextItem[] = [];

  const labelMap: Record<string, string> = {
    files_affected: 'Files Affected',
    imports_count: 'Imported By',
    callers_count: 'Called By',
    callees_count: 'Calls To',
    complexity: 'Complexity Score',
    lines_of_code: 'Lines of Code',
    methods_count: 'Number of Methods',
    dependencies: 'Dependencies',
    dependents: 'Dependents',
    depth: 'Call Depth',
    centrality: 'Centrality Score',
    cohesion: 'Cohesion Score',
    coupling: 'Coupling Score',
  };

  const importantKeys = ['files_affected', 'imports_count', 'complexity', 'callers_count'];

  for (const [key, value] of Object.entries(context)) {
    if (value === null || value === undefined) continue;

    const label = labelMap[key] || key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    let displayValue: string;

    if (Array.isArray(value)) {
      displayValue = `${value.length} items`;
    } else if (typeof value === 'object') {
      displayValue = JSON.stringify(value);
    } else {
      displayValue = String(value);
    }

    items.push({
      label,
      value: displayValue,
      isImportant: importantKeys.includes(key),
    });
  }

  // Sort with important items first
  return items.sort((a, b) => {
    if (a.isImportant && !b.isImportant) return -1;
    if (!a.isImportant && b.isImportant) return 1;
    return 0;
  });
}

// ==========================================
// User-Friendly Error Messages
// ==========================================

import { parseError, formatErrorWithCode } from './error-utils';
import { ErrorCodes } from './error-codes';

/**
 * Get a user-friendly error message for findings-related errors.
 *
 * Uses the centralized error code system to provide:
 * - Specific, actionable error messages
 * - Error codes for support reference
 * - Consistent messaging across the application
 */
export function getFriendlyErrorMessage(error: unknown): string {
  const parsed = parseError(error);

  // Return message with action and error code
  let message = parsed.message;

  // Add action if different from message
  if (parsed.action && !message.includes(parsed.action)) {
    message = `${message} ${parsed.action}`;
  }

  // Add error code for non-generic errors
  if (parsed.code !== ErrorCodes.UNKNOWN) {
    message = `${message} (Ref: ${parsed.code})`;
  }

  return message;
}

/**
 * Get structured error information for UI display.
 */
export function getFriendlyErrorInfo(error: unknown): {
  title: string;
  message: string;
  action: string;
  code: string;
} {
  const parsed = parseError(error);
  return {
    title: parsed.title,
    message: parsed.message,
    action: parsed.action,
    code: parsed.code,
  };
}
