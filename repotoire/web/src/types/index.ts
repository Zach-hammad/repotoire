// Fix status lifecycle
export type FixStatus = 'pending' | 'approved' | 'rejected' | 'applied' | 'failed';

// Confidence levels for AI-generated fixes
export type FixConfidence = 'high' | 'medium' | 'low';

// Types of fixes the system can propose
export type FixType =
  | 'refactor'
  | 'simplify'
  | 'extract'
  | 'rename'
  | 'remove'
  | 'security'
  | 'type_hint'
  | 'documentation';

// Severity levels for findings
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

// Finding status for workflow management
export type FindingStatus =
  | 'open'           // Newly detected, not yet reviewed
  | 'acknowledged'   // Team is aware, may address later
  | 'in_progress'    // Currently being worked on
  | 'resolved'       // Issue has been fixed
  | 'wontfix'        // Intentionally not fixing (acceptable tech debt)
  | 'false_positive' // Not a real issue (detector mistake)
  | 'duplicate';     // Duplicate of another finding

// A single code change within a fix
export interface CodeChange {
  file_path: string;
  original_code: string;
  fixed_code: string;
  start_line: number;
  end_line: number;
  description: string;
}

// Evidence supporting the fix recommendation
export interface Evidence {
  similar_patterns: string[];
  documentation_refs: string[];
  best_practices: string[];
  rag_context_count: number;
}

// A finding (code smell, issue) from analysis
export interface Finding {
  id: string;
  analysis_run_id: string;
  detector: string;
  severity: Severity;
  status: FindingStatus;
  title: string;
  description: string;
  affected_files: string[];
  affected_nodes: string[];
  line_start?: number;
  line_end?: number;
  suggested_fix?: string;
  estimated_effort?: string;
  graph_context?: Record<string, unknown>;
  status_reason?: string;
  status_changed_by?: string;
  status_changed_at?: string;
  created_at: string;
  updated_at?: string;
}

// Filters for findings list
export interface FindingFilters {
  severity?: Severity[];
  status?: FindingStatus[];
  detector?: string;
  analysis_run_id?: string;
  repository_id?: string;
}

// Request to update a single finding's status
export interface UpdateFindingStatusRequest {
  status: FindingStatus;
  reason?: string;
}

// Request for bulk updating finding statuses
export interface BulkUpdateStatusRequest {
  finding_ids: string[];
  status: FindingStatus;
  reason?: string;
}

// Response from bulk status update
export interface BulkUpdateStatusResponse {
  updated_count: number;
  failed_ids: string[];
}

// A complete fix proposal
export interface FixProposal {
  id: string;
  finding_id?: string | null;
  finding?: Finding;
  fix_type: FixType;
  confidence: FixConfidence;
  changes: CodeChange[];
  title: string;
  description: string;
  rationale: string;
  evidence: Evidence;
  status: FixStatus;
  created_at: string;
  applied_at: string | null;
  syntax_valid: boolean;
  tests_generated: boolean;
  test_code: string | null;
  branch_name: string | null;
  commit_message: string | null;
}

// Comment on a fix
export interface FixComment {
  id: string;
  fix_id: string;
  author: string;
  content: string;
  created_at: string;
}

// Dashboard analytics summary (based on analysis findings)
export interface AnalyticsSummary {
  total_findings: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  by_severity: Record<Severity, number>;
  by_detector: Record<string, number>;
}

// Time-series data point for trends (findings by date)
export interface TrendDataPoint {
  date: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
  total: number;
}

// File hotspot analysis (files with most findings)
export interface FileHotspot {
  file_path: string;
  finding_count: number;
  severity_breakdown: Record<Severity, number>;
}

// Health score response
export interface HealthScore {
  score: number | null;  // null indicates not analyzed
  grade: 'A' | 'B' | 'C' | 'D' | 'F' | null;  // null indicates not analyzed
  trend: 'improving' | 'declining' | 'stable' | 'unknown';
  categories: {
    structure: number;
    quality: number;
    architecture: number;
  } | null;  // null indicates not analyzed
}

// API response wrapper
export interface ApiResponse<T> {
  data: T;
  success: boolean;
  error?: string;
}

// Paginated response
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

// Filter options for fix list
export interface FixFilters {
  status?: FixStatus[];
  confidence?: FixConfidence[];
  fix_type?: FixType[];
  date_from?: string;
  date_to?: string;
  file_path?: string;
  search?: string;
  repository_id?: string;
}

// Sort options
export interface SortOptions {
  field: 'created_at' | 'confidence' | 'status' | 'fix_type';
  direction: 'asc' | 'desc';
}

// Subscription plan tiers
export type PlanTier = 'free' | 'pro' | 'enterprise';

// Subscription status
export type SubscriptionStatus =
  | 'active'
  | 'past_due'
  | 'canceled'
  | 'trialing'
  | 'incomplete'
  | 'incomplete_expired'
  | 'unpaid'
  | 'paused';

// Usage information
export interface UsageInfo {
  repos: number;
  analyses: number;
  limits: {
    repos: number;      // -1 for unlimited
    analyses: number;   // -1 for unlimited
  };
}

// Subscription response from API
export interface Subscription {
  tier: PlanTier;
  status: SubscriptionStatus;
  seats: number;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  usage: UsageInfo;
  monthly_cost_cents: number;
}

// Plan information with per-seat pricing
export interface PlanInfo {
  tier: PlanTier;
  name: string;
  base_price_cents: number;      // Base platform fee
  price_per_seat_cents: number;  // Price per seat
  min_seats: number;
  max_seats: number;             // -1 for unlimited
  repos_per_seat: number;        // -1 for unlimited
  analyses_per_seat: number;     // -1 for unlimited
  features: string[];
}

// Available plans response
export interface PlansResponse {
  plans: PlanInfo[];
  current_tier: PlanTier;
  current_seats: number;
}

// Checkout request
export interface CheckoutRequest {
  tier: PlanTier;
  seats: number;
}

// Checkout response
export interface CheckoutResponse {
  checkout_url: string;
}

// Portal response
export interface PortalResponse {
  portal_url: string;
}

// Price calculation request
export interface PriceCalculationRequest {
  tier: PlanTier;
  seats: number;
}

// Price calculation response
export interface PriceCalculationResponse {
  tier: PlanTier;
  seats: number;
  base_price_cents: number;
  seat_price_cents: number;
  total_monthly_cents: number;
  repos_limit: number;
  analyses_limit: number;
}

// Preview check result
export interface PreviewCheck {
  name: string;  // 'syntax', 'import', 'type', 'tests'
  passed: boolean;
  message: string;
  duration_ms: number;
}

// Preview execution result
export interface PreviewResult {
  success: boolean;
  stdout: string;
  stderr: string;
  duration_ms: number;
  checks: PreviewCheck[];
  error: string | null;
  cached_at: string | null;  // ISO timestamp if cached
}

// Repository analysis status
export type AnalysisStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed';

// Repository with full details
export interface Repository {
  id: string;
  full_name: string;
  github_repo_id: number;
  health_score: number | null;
  last_analyzed_at: string | null;
  analysis_status: AnalysisStatus;
  is_enabled: boolean;
  default_branch: string;
  created_at: string;
  updated_at: string;
  // Linked Repository UUID for analysis data (findings, etc.)
  repository_id: string | null;
}

// GitHub App installation
export interface GitHubInstallation {
  id: string;
  uuid: string;
  installation_id: number;
  account_login: string;
  account_type: 'User' | 'Organization';
  account_avatar_url?: string;
  repo_count: number;
  created_at: string;
  updated_at: string;
}

// Available GitHub repo (not yet connected)
export interface GitHubAvailableRepo {
  id: number;
  full_name: string;
  description: string | null;
  private: boolean;
  default_branch: string;
}

// Analysis run status for polling
export interface AnalysisRunStatus {
  id: string;
  repository_id: string;
  full_name: string | null;  // Repository full name (owner/repo) for GitHub URLs
  commit_sha: string;
  branch: string;
  status: AnalysisStatus;
  progress_percent: number;
  current_step: string | null;
  health_score: number | null;
  structure_score: number | null;
  quality_score: number | null;
  architecture_score: number | null;
  findings_count: number;
  files_analyzed: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

// ==========================================
// API Keys Types
// ==========================================

// Available scopes for API keys
export type ApiKeyScope =
  | 'read:analysis'
  | 'write:analysis'
  | 'read:findings'
  | 'write:findings'
  | 'read:fixes'
  | 'write:fixes'
  | 'read:repositories'
  | 'write:repositories';

// An API key (without the secret - used for listing)
export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;  // First 8 chars of key for identification
  key_suffix: string;  // Last 4 chars of key
  scopes: ApiKeyScope[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  created_by: string;  // User ID who created the key
}

// Response when creating a new API key (includes full secret once)
export interface ApiKeyCreateResponse {
  id: string;
  name: string;
  key: string;  // Full key - only shown once on creation
  scopes: ApiKeyScope[];
  created_at: string;
  expires_at: string | null;
}

// Request to create a new API key
export interface ApiKeyCreateRequest {
  name: string;
  scopes: ApiKeyScope[];
  expires_in_days?: number;  // Optional expiration
}

// ==========================================
// Git Provenance Types
// ==========================================

/** Confidence level for provenance detection */
export type ProvenanceConfidence = 'high' | 'medium' | 'low' | 'unknown';

/**
 * Information about a git commit that introduced or modified code
 */
export interface CommitProvenance {
  /** Full commit SHA */
  commit_sha: string;
  /** Author's display name */
  author_name: string;
  /** Author's email address */
  author_email: string;
  /** ISO 8601 timestamp when the commit was made */
  committed_date: string;
  /** Commit message (first line or full) */
  message: string;
  /** List of files changed in this commit */
  changed_files: string[];
  /** Number of lines added */
  insertions: number;
  /** Number of lines deleted */
  deletions: number;
}

/**
 * Origin information for a code finding, showing when the issue was introduced
 */
export interface IssueOrigin {
  /** ID of the finding this origin relates to */
  finding_id: string;
  /** The commit that introduced the issue (null if unknown) */
  introduced_in: CommitProvenance | null;
  /** Confidence level of the origin detection */
  confidence: ProvenanceConfidence;
  /** Explanation of why this confidence level was assigned */
  confidence_reason: string;
  /** Related commits that may have affected this issue */
  related_commits: CommitProvenance[];
  /** Whether a user has manually corrected this attribution */
  user_corrected?: boolean;
  /** The SHA of the user-corrected commit (if corrected) */
  corrected_commit_sha?: string;
}

/**
 * User preferences for provenance display (privacy-first defaults)
 */
export interface ProvenanceSettings {
  /** Show real author names (default: false for privacy) */
  show_author_names: boolean;
  /** Show author avatars/gravatars (default: false for privacy) */
  show_author_avatars: boolean;
  /** Show confidence level badges (default: true) */
  show_confidence_badges: boolean;
  /** Automatically query provenance on page load (default: false for performance) */
  auto_query_provenance: boolean;
}

/**
 * Git history status for a repository
 */
export interface GitHistoryStatus {
  /** Whether git history has been ingested */
  has_git_history: boolean;
  /** Number of commits that have been ingested */
  commits_ingested: number;
  /** Date of the oldest ingested commit */
  oldest_commit_date: string | null;
  /** Date of the newest ingested commit */
  newest_commit_date: string | null;
  /** Percentage of findings with provenance data */
  coverage_percent: number;
  /** Whether more history can be backfilled */
  can_backfill: boolean;
  /** Estimated number of additional commits available */
  backfill_estimate_commits: number;
}

/**
 * Status of a backfill job
 */
export interface BackfillJobStatus {
  /** Unique job ID */
  job_id: string;
  /** Current status */
  status: 'queued' | 'running' | 'completed' | 'failed';
  /** Number of commits processed so far */
  commits_processed: number;
  /** Total commits to process */
  commits_total: number;
  /** Error message if failed */
  error_message?: string;
  /** Timestamp when job started */
  started_at?: string;
  /** Timestamp when job completed */
  completed_at?: string;
}

/**
 * Response from historical query endpoint
 */
export interface HistoricalQueryResponse {
  /** Natural language answer to the query */
  answer: string;
  /** Commits referenced in the answer */
  referenced_commits: CommitProvenance[];
  /** Confidence in the answer */
  confidence: ProvenanceConfidence;
}

/**
 * Response from commit history endpoint
 */
export interface CommitHistoryResponse {
  /** List of recent commits */
  commits: CommitProvenance[];
  /** Total number of commits available */
  total_count: number;
  /** Whether there are more commits to fetch */
  has_more: boolean;
}
