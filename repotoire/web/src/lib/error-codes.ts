/**
 * Centralized error codes and user-friendly error messages.
 *
 * This module provides:
 * 1. Machine-readable error codes for support/debugging (ERR_XXX_NNN format)
 * 2. User-friendly error messages with clear explanations
 * 3. Actionable suggestions for each error type
 * 4. Mapping from HTTP status codes and API error codes to user messages
 */

// =============================================================================
// Error Code Definitions
// =============================================================================

/**
 * Error code categories:
 * - AUTH: Authentication and authorization errors (ERR_AUTH_XXX)
 * - API: API communication errors (ERR_API_XXX)
 * - NET: Network and connection errors (ERR_NET_XXX)
 * - VAL: Validation errors (ERR_VAL_XXX)
 * - RES: Resource errors (not found, conflict) (ERR_RES_XXX)
 * - LIMIT: Rate limiting and quota errors (ERR_LIMIT_XXX)
 * - FIX: Fix/autofix related errors (ERR_FIX_XXX)
 * - REPO: Repository related errors (ERR_REPO_XXX)
 * - ANALYSIS: Analysis related errors (ERR_ANALYSIS_XXX)
 * - SYS: System/server errors (ERR_SYS_XXX)
 */

export const ErrorCodes = {
  // Authentication errors (ERR_AUTH_XXX)
  AUTH_SESSION_EXPIRED: 'ERR_AUTH_001',
  AUTH_INVALID_TOKEN: 'ERR_AUTH_002',
  AUTH_MISSING_TOKEN: 'ERR_AUTH_003',
  AUTH_FORBIDDEN: 'ERR_AUTH_004',
  AUTH_ORG_REQUIRED: 'ERR_AUTH_005',
  AUTH_ADMIN_REQUIRED: 'ERR_AUTH_006',
  AUTH_API_KEY_INVALID: 'ERR_AUTH_007',
  AUTH_API_KEY_EXPIRED: 'ERR_AUTH_008',
  AUTH_INSUFFICIENT_SCOPE: 'ERR_AUTH_009',

  // API communication errors (ERR_API_XXX)
  API_BAD_REQUEST: 'ERR_API_001',
  API_INVALID_RESPONSE: 'ERR_API_002',
  API_TIMEOUT: 'ERR_API_003',
  API_UNAVAILABLE: 'ERR_API_004',
  API_VERSION_MISMATCH: 'ERR_API_005',

  // Network errors (ERR_NET_XXX)
  NET_CONNECTION_FAILED: 'ERR_NET_001',
  NET_OFFLINE: 'ERR_NET_002',
  NET_DNS_FAILED: 'ERR_NET_003',
  NET_SSL_ERROR: 'ERR_NET_004',

  // Validation errors (ERR_VAL_XXX)
  VAL_REQUIRED_FIELD: 'ERR_VAL_001',
  VAL_INVALID_FORMAT: 'ERR_VAL_002',
  VAL_OUT_OF_RANGE: 'ERR_VAL_003',
  VAL_QUERY_TOO_SHORT: 'ERR_VAL_004',
  VAL_FILE_TOO_LARGE: 'ERR_VAL_005',

  // Resource errors (ERR_RES_XXX)
  RES_NOT_FOUND: 'ERR_RES_001',
  RES_ALREADY_EXISTS: 'ERR_RES_002',
  RES_CONFLICT: 'ERR_RES_003',
  RES_DELETED: 'ERR_RES_004',
  RES_LOCKED: 'ERR_RES_005',

  // Rate limiting errors (ERR_LIMIT_XXX)
  LIMIT_RATE_EXCEEDED: 'ERR_LIMIT_001',
  LIMIT_QUOTA_EXCEEDED: 'ERR_LIMIT_002',
  LIMIT_CONCURRENT_EXCEEDED: 'ERR_LIMIT_003',
  LIMIT_DAILY_EXCEEDED: 'ERR_LIMIT_004',

  // Billing errors (ERR_BILLING_XXX)
  BILLING_LIMIT_EXCEEDED: 'ERR_BILLING_001',
  BILLING_FEATURE_UNAVAILABLE: 'ERR_BILLING_002',
  BILLING_REPO_LIMIT: 'ERR_BILLING_003',
  BILLING_ANALYSIS_LIMIT: 'ERR_BILLING_004',

  // Fix/autofix errors (ERR_FIX_XXX)
  FIX_PREVIEW_REQUIRED: 'ERR_FIX_001',
  FIX_ALREADY_APPLIED: 'ERR_FIX_002',
  FIX_MERGE_CONFLICT: 'ERR_FIX_003',
  FIX_SYNTAX_ERROR: 'ERR_FIX_004',
  FIX_STALE: 'ERR_FIX_005',
  FIX_SANDBOX_UNAVAILABLE: 'ERR_FIX_006',
  FIX_TEST_FAILED: 'ERR_FIX_007',

  // Repository errors (ERR_REPO_XXX)
  REPO_NOT_FOUND: 'ERR_REPO_001',
  REPO_ACCESS_DENIED: 'ERR_REPO_002',
  REPO_NOT_CONNECTED: 'ERR_REPO_003',
  REPO_CLONE_FAILED: 'ERR_REPO_004',
  REPO_DISABLED: 'ERR_REPO_005',
  REPO_LIMIT_REACHED: 'ERR_REPO_006',

  // Analysis errors (ERR_ANALYSIS_XXX)
  ANALYSIS_FAILED: 'ERR_ANALYSIS_001',
  ANALYSIS_TIMEOUT: 'ERR_ANALYSIS_002',
  ANALYSIS_CANCELLED: 'ERR_ANALYSIS_003',
  ANALYSIS_ALREADY_RUNNING: 'ERR_ANALYSIS_004',
  ANALYSIS_INGESTION_FAILED: 'ERR_ANALYSIS_005',
  ANALYSIS_NO_FILES: 'ERR_ANALYSIS_006',

  // System errors (ERR_SYS_XXX)
  SYS_INTERNAL_ERROR: 'ERR_SYS_001',
  SYS_MAINTENANCE: 'ERR_SYS_002',
  SYS_DATABASE_ERROR: 'ERR_SYS_003',
  SYS_STORAGE_ERROR: 'ERR_SYS_004',
  SYS_GRAPH_ERROR: 'ERR_SYS_005',

  // Generic fallback
  UNKNOWN: 'ERR_UNKNOWN',
} as const;

export type ErrorCode = (typeof ErrorCodes)[keyof typeof ErrorCodes];

// =============================================================================
// Error Message Configuration
// =============================================================================

export interface ErrorInfo {
  /** User-friendly title for the error */
  title: string;
  /** Detailed explanation of what went wrong */
  message: string;
  /** Actionable suggestion for the user */
  action: string;
  /** Whether this error should be reported to support */
  reportable: boolean;
  /** Severity level for toast styling */
  severity: 'error' | 'warning' | 'info';
}

export const ErrorMessages: Record<ErrorCode, ErrorInfo> = {
  // Authentication errors
  [ErrorCodes.AUTH_SESSION_EXPIRED]: {
    title: 'Session Expired',
    message: 'Your login session has expired for security reasons.',
    action: 'Please sign in again to continue.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.AUTH_INVALID_TOKEN]: {
    title: 'Authentication Failed',
    message: 'Your authentication token is invalid or has been revoked.',
    action: 'Please sign out and sign in again.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.AUTH_MISSING_TOKEN]: {
    title: 'Authentication Required',
    message: 'You need to be signed in to access this feature.',
    action: 'Please sign in to continue.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.AUTH_FORBIDDEN]: {
    title: 'Access Denied',
    message: 'You do not have permission to perform this action.',
    action: 'Contact your organization administrator if you believe this is an error.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.AUTH_ORG_REQUIRED]: {
    title: 'Organization Required',
    message: 'This feature requires you to be part of an organization.',
    action: 'Create or join an organization to access this feature.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.AUTH_ADMIN_REQUIRED]: {
    title: 'Admin Access Required',
    message: 'This action requires organization administrator privileges.',
    action: 'Contact your organization administrator to perform this action.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.AUTH_API_KEY_INVALID]: {
    title: 'Invalid API Key',
    message: 'The provided API key is invalid or has been revoked.',
    action: 'Generate a new API key from your settings page.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.AUTH_API_KEY_EXPIRED]: {
    title: 'API Key Expired',
    message: 'Your API key has expired.',
    action: 'Generate a new API key from your settings page.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.AUTH_INSUFFICIENT_SCOPE]: {
    title: 'Insufficient Permissions',
    message: 'Your API key does not have the required permissions for this action.',
    action: 'Create a new API key with the appropriate scopes.',
    reportable: false,
    severity: 'error',
  },

  // API communication errors
  [ErrorCodes.API_BAD_REQUEST]: {
    title: 'Invalid Request',
    message: 'The request could not be processed due to invalid data.',
    action: 'Check your input and try again.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.API_INVALID_RESPONSE]: {
    title: 'Unexpected Response',
    message: 'We received an unexpected response from the server.',
    action: 'Refresh the page and try again. If the problem persists, contact support.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.API_TIMEOUT]: {
    title: 'Request Timed Out',
    message: 'The server took too long to respond.',
    action: 'Please wait a moment and try again. Large operations may take longer.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.API_UNAVAILABLE]: {
    title: 'Service Temporarily Unavailable',
    message: 'Our service is temporarily unavailable.',
    action: 'Check our status page at status.repotoire.com for updates.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.API_VERSION_MISMATCH]: {
    title: 'Version Mismatch',
    message: 'Your app version is out of sync with the server.',
    action: 'Please refresh the page to get the latest version.',
    reportable: false,
    severity: 'warning',
  },

  // Network errors
  [ErrorCodes.NET_CONNECTION_FAILED]: {
    title: 'Connection Failed',
    message: 'Unable to connect to the server.',
    action: 'Check your internet connection and try again.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.NET_OFFLINE]: {
    title: 'You\'re Offline',
    message: 'No internet connection detected.',
    action: 'Please check your network connection and try again.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.NET_DNS_FAILED]: {
    title: 'DNS Resolution Failed',
    message: 'Unable to resolve the server address.',
    action: 'Check your network settings or try again later.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.NET_SSL_ERROR]: {
    title: 'Secure Connection Failed',
    message: 'Unable to establish a secure connection.',
    action: 'Check your network settings or try a different network.',
    reportable: true,
    severity: 'error',
  },

  // Validation errors
  [ErrorCodes.VAL_REQUIRED_FIELD]: {
    title: 'Missing Required Field',
    message: 'One or more required fields are missing.',
    action: 'Please fill in all required fields and try again.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.VAL_INVALID_FORMAT]: {
    title: 'Invalid Format',
    message: 'The provided value is not in the expected format.',
    action: 'Please check the format and try again.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.VAL_OUT_OF_RANGE]: {
    title: 'Value Out of Range',
    message: 'The provided value is outside the allowed range.',
    action: 'Please enter a value within the allowed range.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.VAL_QUERY_TOO_SHORT]: {
    title: 'Query Too Short',
    message: 'Your search query must be at least 3 characters.',
    action: 'Please enter a longer search query.',
    reportable: false,
    severity: 'info',
  },
  [ErrorCodes.VAL_FILE_TOO_LARGE]: {
    title: 'File Too Large',
    message: 'The uploaded file exceeds the maximum allowed size.',
    action: 'Please upload a smaller file.',
    reportable: false,
    severity: 'warning',
  },

  // Resource errors
  [ErrorCodes.RES_NOT_FOUND]: {
    title: 'Not Found',
    message: 'The requested resource could not be found.',
    action: 'It may have been deleted or you may not have access to it.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.RES_ALREADY_EXISTS]: {
    title: 'Already Exists',
    message: 'A resource with this identifier already exists.',
    action: 'Use a different name or update the existing resource.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.RES_CONFLICT]: {
    title: 'Conflict Detected',
    message: 'The resource has been modified by another user.',
    action: 'Refresh the page to see the latest changes and try again.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.RES_DELETED]: {
    title: 'Resource Deleted',
    message: 'This resource has been deleted.',
    action: 'The content you\'re looking for is no longer available.',
    reportable: false,
    severity: 'info',
  },
  [ErrorCodes.RES_LOCKED]: {
    title: 'Resource Locked',
    message: 'This resource is currently locked for editing.',
    action: 'Please wait for the current operation to complete.',
    reportable: false,
    severity: 'warning',
  },

  // Rate limiting errors
  [ErrorCodes.LIMIT_RATE_EXCEEDED]: {
    title: 'Too Many Requests',
    message: 'You\'ve made too many requests in a short period.',
    action: 'Please wait a few seconds before trying again.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.LIMIT_QUOTA_EXCEEDED]: {
    title: 'Quota Exceeded',
    message: 'You\'ve reached your plan\'s usage limit.',
    action: 'Upgrade your plan or wait for your quota to reset.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.LIMIT_CONCURRENT_EXCEEDED]: {
    title: 'Concurrent Limit Reached',
    message: 'You\'ve reached the maximum number of concurrent operations.',
    action: 'Wait for current operations to complete before starting new ones.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.LIMIT_DAILY_EXCEEDED]: {
    title: 'Daily Limit Reached',
    message: 'You\'ve reached your daily usage limit.',
    action: 'Your limit will reset at midnight UTC, or upgrade your plan for more.',
    reportable: false,
    severity: 'warning',
  },

  // Billing errors
  [ErrorCodes.BILLING_LIMIT_EXCEEDED]: {
    title: 'Usage Limit Reached',
    message: 'You\'ve reached your plan\'s usage limit.',
    action: 'Upgrade your plan or add more seats for additional capacity.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.BILLING_FEATURE_UNAVAILABLE]: {
    title: 'Feature Not Available',
    message: 'This feature is not available on your current plan.',
    action: 'Upgrade to Pro or Enterprise to access this feature.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.BILLING_REPO_LIMIT]: {
    title: 'Repository Limit Reached',
    message: 'You\'ve reached the maximum number of repositories for your plan.',
    action: 'Disconnect unused repositories or upgrade your plan.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.BILLING_ANALYSIS_LIMIT]: {
    title: 'Analysis Limit Reached',
    message: 'You\'ve reached the maximum number of analyses for this billing period.',
    action: 'Upgrade your plan or wait for your limit to reset next month.',
    reportable: false,
    severity: 'warning',
  },

  // Fix/autofix errors
  [ErrorCodes.FIX_PREVIEW_REQUIRED]: {
    title: 'Preview Required',
    message: 'You must run a preview before applying this fix.',
    action: 'Click "Preview" to verify the fix works correctly.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.FIX_ALREADY_APPLIED]: {
    title: 'Fix Already Applied',
    message: 'This fix has already been applied to your codebase.',
    action: 'Check your repository for the changes.',
    reportable: false,
    severity: 'info',
  },
  [ErrorCodes.FIX_MERGE_CONFLICT]: {
    title: 'Merge Conflict',
    message: 'The target code has changed since this fix was generated.',
    action: 'Regenerate the fix to create an updated version.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.FIX_SYNTAX_ERROR]: {
    title: 'Syntax Error in Fix',
    message: 'The generated fix contains a syntax error.',
    action: 'This has been reported. Try regenerating the fix.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.FIX_STALE]: {
    title: 'Outdated Fix',
    message: 'The code has been modified since this fix was created.',
    action: 'Regenerate the fix to get an updated version.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.FIX_SANDBOX_UNAVAILABLE]: {
    title: 'Test Environment Unavailable',
    message: 'The testing environment is temporarily unavailable.',
    action: 'Please try again in a few minutes.',
    reportable: true,
    severity: 'warning',
  },
  [ErrorCodes.FIX_TEST_FAILED]: {
    title: 'Tests Failed',
    message: 'The fix caused one or more tests to fail.',
    action: 'Review the test results and consider an alternative fix.',
    reportable: false,
    severity: 'warning',
  },

  // Repository errors
  [ErrorCodes.REPO_NOT_FOUND]: {
    title: 'Repository Not Found',
    message: 'The repository could not be found.',
    action: 'Verify the repository exists and you have access to it.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.REPO_ACCESS_DENIED]: {
    title: 'Repository Access Denied',
    message: 'You do not have permission to access this repository.',
    action: 'Request access from the repository owner or connect a different repository.',
    reportable: false,
    severity: 'error',
  },
  [ErrorCodes.REPO_NOT_CONNECTED]: {
    title: 'Repository Not Connected',
    message: 'This repository is not connected to Repotoire.',
    action: 'Connect the repository from the Repositories page.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.REPO_CLONE_FAILED]: {
    title: 'Clone Failed',
    message: 'Unable to clone the repository.',
    action: 'Check the repository permissions and try again.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.REPO_DISABLED]: {
    title: 'Repository Disabled',
    message: 'This repository has been disabled.',
    action: 'Enable the repository to continue analysis.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.REPO_LIMIT_REACHED]: {
    title: 'Repository Limit Reached',
    message: 'You\'ve reached the maximum number of repositories for your plan.',
    action: 'Upgrade your plan or disconnect unused repositories.',
    reportable: false,
    severity: 'warning',
  },

  // Analysis errors
  [ErrorCodes.ANALYSIS_FAILED]: {
    title: 'Analysis Failed',
    message: 'The code analysis encountered an error.',
    action: 'Check the repository for issues and try again.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.ANALYSIS_TIMEOUT]: {
    title: 'Analysis Timed Out',
    message: 'The analysis took too long to complete.',
    action: 'Try analyzing a smaller portion of the codebase.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.ANALYSIS_CANCELLED]: {
    title: 'Analysis Cancelled',
    message: 'The analysis was cancelled.',
    action: 'Start a new analysis when ready.',
    reportable: false,
    severity: 'info',
  },
  [ErrorCodes.ANALYSIS_ALREADY_RUNNING]: {
    title: 'Analysis Already Running',
    message: 'An analysis is already in progress for this repository.',
    action: 'Wait for the current analysis to complete.',
    reportable: false,
    severity: 'warning',
  },
  [ErrorCodes.ANALYSIS_INGESTION_FAILED]: {
    title: 'Code Ingestion Failed',
    message: 'Unable to parse and ingest the repository code.',
    action: 'Check for syntax errors in your code and try again.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.ANALYSIS_NO_FILES]: {
    title: 'No Analyzable Files',
    message: 'No supported source files were found in the repository.',
    action: 'Verify the repository contains Python files.',
    reportable: false,
    severity: 'warning',
  },

  // System errors
  [ErrorCodes.SYS_INTERNAL_ERROR]: {
    title: 'Internal Error',
    message: 'An unexpected error occurred on our end.',
    action: 'We\'ve been notified. Please try again or contact support if the issue persists.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.SYS_MAINTENANCE]: {
    title: 'Scheduled Maintenance',
    message: 'We\'re currently performing scheduled maintenance.',
    action: 'Please check back in a few minutes.',
    reportable: false,
    severity: 'info',
  },
  [ErrorCodes.SYS_DATABASE_ERROR]: {
    title: 'Database Error',
    message: 'Unable to access the database.',
    action: 'We\'ve been notified. Please try again in a few minutes.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.SYS_STORAGE_ERROR]: {
    title: 'Storage Error',
    message: 'Unable to access file storage.',
    action: 'We\'ve been notified. Please try again in a few minutes.',
    reportable: true,
    severity: 'error',
  },
  [ErrorCodes.SYS_GRAPH_ERROR]: {
    title: 'Graph Database Error',
    message: 'Unable to access the code knowledge graph.',
    action: 'We\'ve been notified. Please try again in a few minutes.',
    reportable: true,
    severity: 'error',
  },

  // Unknown/fallback
  [ErrorCodes.UNKNOWN]: {
    title: 'Unexpected Error',
    message: 'An unexpected error occurred.',
    action: 'Please try again. If the problem persists, contact support.',
    reportable: true,
    severity: 'error',
  },
};

// =============================================================================
// HTTP Status Code Mapping
// =============================================================================

/**
 * Map HTTP status codes to appropriate error codes.
 */
export function getErrorCodeFromStatus(status: number): ErrorCode {
  switch (status) {
    case 400:
      return ErrorCodes.API_BAD_REQUEST;
    case 401:
      return ErrorCodes.AUTH_SESSION_EXPIRED;
    case 403:
      return ErrorCodes.AUTH_FORBIDDEN;
    case 404:
      return ErrorCodes.RES_NOT_FOUND;
    case 408:
      return ErrorCodes.API_TIMEOUT;
    case 409:
      return ErrorCodes.RES_CONFLICT;
    case 422:
      return ErrorCodes.VAL_INVALID_FORMAT;
    case 429:
      return ErrorCodes.LIMIT_RATE_EXCEEDED;
    case 500:
      return ErrorCodes.SYS_INTERNAL_ERROR;
    case 502:
    case 503:
    case 504:
      return ErrorCodes.API_UNAVAILABLE;
    default:
      return status >= 500 ? ErrorCodes.SYS_INTERNAL_ERROR : ErrorCodes.UNKNOWN;
  }
}

// =============================================================================
// Error Message Pattern Matching
// =============================================================================

interface ErrorPattern {
  patterns: string[];
  code: ErrorCode;
}

const errorPatterns: ErrorPattern[] = [
  // Auth patterns
  { patterns: ['session expired', 'jwt expired', 'token expired'], code: ErrorCodes.AUTH_SESSION_EXPIRED },
  { patterns: ['invalid token', 'jwt invalid', 'malformed token'], code: ErrorCodes.AUTH_INVALID_TOKEN },
  { patterns: ['missing authorization', 'no auth'], code: ErrorCodes.AUTH_MISSING_TOKEN },
  { patterns: ['forbidden', 'not allowed', 'permission denied'], code: ErrorCodes.AUTH_FORBIDDEN },
  { patterns: ['organization required', 'org required'], code: ErrorCodes.AUTH_ORG_REQUIRED },
  { patterns: ['admin required', 'admin access'], code: ErrorCodes.AUTH_ADMIN_REQUIRED },
  { patterns: ['invalid api key', 'api key invalid'], code: ErrorCodes.AUTH_API_KEY_INVALID },
  { patterns: ['api key expired'], code: ErrorCodes.AUTH_API_KEY_EXPIRED },
  { patterns: ['insufficient scope', 'missing scope'], code: ErrorCodes.AUTH_INSUFFICIENT_SCOPE },

  // Network patterns
  { patterns: ['fetch failed', 'network error', 'failed to fetch'], code: ErrorCodes.NET_CONNECTION_FAILED },
  { patterns: ['offline', 'no internet'], code: ErrorCodes.NET_OFFLINE },
  { patterns: ['dns', 'host not found'], code: ErrorCodes.NET_DNS_FAILED },
  { patterns: ['ssl', 'certificate', 'tls'], code: ErrorCodes.NET_SSL_ERROR },

  // Timeout patterns
  { patterns: ['timeout', 'timed out', 'took too long'], code: ErrorCodes.API_TIMEOUT },

  // Rate limit patterns
  { patterns: ['rate limit', 'too many requests', '429'], code: ErrorCodes.LIMIT_RATE_EXCEEDED },
  { patterns: ['quota exceeded', 'quota limit'], code: ErrorCodes.LIMIT_QUOTA_EXCEEDED },
  { patterns: ['concurrent limit', 'concurrent exceeded'], code: ErrorCodes.LIMIT_CONCURRENT_EXCEEDED },

  // Billing patterns
  { patterns: ['usage_limit_exceeded', 'usage limit exceeded'], code: ErrorCodes.BILLING_LIMIT_EXCEEDED },
  { patterns: ['feature_not_available', 'feature not available'], code: ErrorCodes.BILLING_FEATURE_UNAVAILABLE },
  { patterns: ['repository limit reached', 'repo limit reached'], code: ErrorCodes.BILLING_REPO_LIMIT },
  { patterns: ['analysis limit reached', 'analyses limit'], code: ErrorCodes.BILLING_ANALYSIS_LIMIT },

  // Fix patterns
  { patterns: ['preview required', 'run preview first'], code: ErrorCodes.FIX_PREVIEW_REQUIRED },
  { patterns: ['already applied', 'fix applied'], code: ErrorCodes.FIX_ALREADY_APPLIED },
  { patterns: ['merge conflict', 'conflict detected'], code: ErrorCodes.FIX_MERGE_CONFLICT },
  { patterns: ['syntax error', 'parse error'], code: ErrorCodes.FIX_SYNTAX_ERROR },
  { patterns: ['sandbox unavailable', 'e2b', 'testing environment'], code: ErrorCodes.FIX_SANDBOX_UNAVAILABLE },
  { patterns: ['test failed', 'tests failed'], code: ErrorCodes.FIX_TEST_FAILED },
  { patterns: ['code has changed', 'stale'], code: ErrorCodes.FIX_STALE },

  // Resource patterns
  { patterns: ['not found', '404'], code: ErrorCodes.RES_NOT_FOUND },
  { patterns: ['already exists', 'duplicate'], code: ErrorCodes.RES_ALREADY_EXISTS },
  { patterns: ['conflict', '409'], code: ErrorCodes.RES_CONFLICT },

  // Analysis patterns
  { patterns: ['analysis failed'], code: ErrorCodes.ANALYSIS_FAILED },
  { patterns: ['analysis timeout', 'analysis timed out'], code: ErrorCodes.ANALYSIS_TIMEOUT },
  { patterns: ['analysis cancelled', 'analysis canceled'], code: ErrorCodes.ANALYSIS_CANCELLED },
  { patterns: ['already running', 'in progress'], code: ErrorCodes.ANALYSIS_ALREADY_RUNNING },

  // Repository patterns
  { patterns: ['repository not found', 'repo not found'], code: ErrorCodes.REPO_NOT_FOUND },
  { patterns: ['clone failed', 'failed to clone'], code: ErrorCodes.REPO_CLONE_FAILED },
  { patterns: ['repository disabled', 'repo disabled'], code: ErrorCodes.REPO_DISABLED },

  // Server patterns
  { patterns: ['internal server error', 'internal error', '500'], code: ErrorCodes.SYS_INTERNAL_ERROR },
  { patterns: ['maintenance', 'under maintenance'], code: ErrorCodes.SYS_MAINTENANCE },
  { patterns: ['database error', 'db error'], code: ErrorCodes.SYS_DATABASE_ERROR },
];

/**
 * Get error code from an error message by pattern matching.
 */
export function getErrorCodeFromMessage(message: string): ErrorCode {
  const lowerMessage = message.toLowerCase();

  for (const { patterns, code } of errorPatterns) {
    if (patterns.some((pattern) => lowerMessage.includes(pattern))) {
      return code;
    }
  }

  return ErrorCodes.UNKNOWN;
}

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Get complete error information from an error code.
 */
export function getErrorInfo(code: ErrorCode): ErrorInfo {
  return ErrorMessages[code] || ErrorMessages[ErrorCodes.UNKNOWN];
}

/**
 * Format an error message with the error code for support reference.
 */
export function formatErrorWithCode(code: ErrorCode, customMessage?: string): string {
  const info = getErrorInfo(code);
  const message = customMessage || info.message;
  return `${message} (${code})`;
}

/**
 * Create a support reference string from an error code.
 */
export function getSupportReference(code: ErrorCode): string {
  return `Reference code: ${code}`;
}

/**
 * Check if an error should be automatically reported to the error tracking service.
 */
export function shouldReportError(code: ErrorCode): boolean {
  const info = getErrorInfo(code);
  return info.reportable;
}
