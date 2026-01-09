/**
 * Type definitions for Repotoire Web.
 *
 * This file re-exports types from the auto-generated OpenAPI types where available,
 * and provides manual type definitions for types not in the API spec.
 *
 * Run `npm run generate:types` to regenerate api.generated.ts from the backend.
 */

import type { components } from './api.generated';

// =============================================================================
// Enums and Literal Types (must be defined first for use in interfaces)
// =============================================================================

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

// Repository analysis status
export type AnalysisStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed';

// Impact level classification for health score changes
export type ImpactLevel = 'critical' | 'high' | 'medium' | 'low' | 'negligible';

// Warning level for quota usage
export type SandboxWarningLevel = 'ok' | 'warning' | 'critical' | 'exceeded';

// =============================================================================
// Re-exported from Generated OpenAPI Types
// =============================================================================

// Findings - use generated type but override fields with proper types
type FindingBase = components['schemas']['FindingResponse'];
export interface Finding extends Omit<FindingBase, 'severity' | 'status' | 'affected_files' | 'affected_nodes'> {
  severity: Severity;
  status: FindingStatus;
  // Make arrays required (backend always returns them, even if empty)
  affected_files: string[];
  affected_nodes: string[];
}
export type FindingsSummary = components['schemas']['FindingsSummary'];
export type FindingsByDetector = components['schemas']['FindingsByDetector'];
export type PaginatedFindingsResponse = components['schemas']['PaginatedFindingsResponse'];
export type UpdateFindingStatusRequest = components['schemas']['UpdateFindingStatusRequest'];
export type BulkUpdateStatusRequest = components['schemas']['BulkUpdateStatusRequest'];
// Override to make failed_ids required (backend always returns it)
type BulkUpdateStatusResponseBase = components['schemas']['BulkUpdateStatusResponse'];
export interface BulkUpdateStatusResponse extends Omit<BulkUpdateStatusResponseBase, 'failed_ids'> {
  failed_ids: string[];
}

// Analytics
export type AnalyticsSummary = components['schemas']['AnalyticsSummary'];
export type TrendDataPoint = components['schemas']['TrendDataPoint'];
export type FileHotspot = components['schemas']['FileHotspot'];
export type HealthScore = components['schemas']['HealthScoreResponse'];
export type FixStatistics = components['schemas']['FixStatistics'];

// Repositories
export type RepositoryInfo = components['schemas']['RepositoryInfo'];
export type RepositoryDetailResponse = components['schemas']['RepositoryDetailResponse'];
export type GitHubInstallation = components['schemas']['GitHubInstallationResponse'];
export type GitHubAvailableRepo = components['schemas']['GitHubRepoResponse'];

// Override AnalysisStatusResponse to use AnalysisStatus literal type
type AnalysisStatusResponseBase = components['schemas']['AnalysisStatusResponse'];
export interface AnalysisRunStatus extends Omit<AnalysisStatusResponseBase, 'status'> {
  status: AnalysisStatus;
}

// Billing & Subscription
export type Subscription = components['schemas']['SubscriptionResponse'];
export type UsageInfo = components['schemas']['UsageInfo'];
export type PlanTier = components['schemas']['PlanTier'];
export type SubscriptionStatus = components['schemas']['SubscriptionStatus'];

// Git Provenance
// Extend CommitProvenance with optional fields for detailed commit view (not always populated by backend)
type CommitProvenanceBase = components['schemas']['CommitProvenance'];
export interface CommitProvenance extends CommitProvenanceBase {
  // Optional: aggregate stats for commit
  insertions?: number;
  deletions?: number;
  // Optional: list of changed file paths for detailed commit view
  changed_files?: string[];
}
export type IssueOrigin = components['schemas']['IssueOriginResponse'];
export type ProvenanceConfidence = components['schemas']['ProvenanceConfidence'];
export type ProvenanceSettings = components['schemas']['ProvenanceSettingsResponse'];
export type GitHistoryStatus = components['schemas']['GitHistoryStatusResponse'];
export type BackfillJobStatus = components['schemas']['BackfillJobStatusResponse'];
export type CommitHistoryResponse = components['schemas']['CommitHistoryResponse'];

// Manual definition - generated type is incomplete
export interface HistoricalQueryResponse {
  /** Natural language answer to the query */
  answer: string;
  /** Commits referenced in the answer */
  referenced_commits: CommitProvenance[];
  /** Confidence in the answer */
  confidence: ProvenanceConfidence;
}

// Code Search & RAG
export type CodeEntity = components['schemas']['CodeEntity'];
export type CodeSearchResponse = components['schemas']['CodeSearchResponse'];
export type CodeAskResponse = components['schemas']['CodeAskResponse'];
export type EmbeddingsStatusResponse = components['schemas']['EmbeddingsStatusResponse'];
export type ModuleStats = components['schemas']['ModuleStats'];
export type ArchitectureResponse = components['schemas']['ArchitectureResponse'];

// Health Score Delta - override impact_level to use ImpactLevel literal type
type HealthScoreDeltaBase = components['schemas']['HealthScoreDeltaResponse'];
export interface HealthScoreDelta extends Omit<HealthScoreDeltaBase, 'impact_level'> {
  impact_level: ImpactLevel;
}
export type BatchHealthScoreDelta = components['schemas']['BatchHealthScoreDeltaResponse'];

// Preview
export type PreviewCheck = components['schemas']['PreviewCheck'];
export type PreviewResult = components['schemas']['PreviewResult'];

// Sandbox Metrics
export type SandboxCostSummary = components['schemas']['CostSummary'];
export type SandboxOperationTypeCost = components['schemas']['OperationTypeCost'];
export type SandboxSlowOperation = components['schemas']['SlowOperation'];
export type SandboxFailedOperation = components['schemas']['FailedOperation'];
export type SandboxFailureRate = components['schemas']['FailureRate'];
export type SandboxUsageStats = components['schemas']['UsageStats'];
export type SandboxQuotaLimits = components['schemas']['QuotaLimitResponse'];
export type SandboxBillingUsage = components['schemas']['BillingUsageResponse'];
export type SandboxBillingStatus = components['schemas']['BillingStatusResponse'];

// Override QuotaUsageItem to use SandboxWarningLevel literal type
type QuotaUsageItemBase = components['schemas']['QuotaUsageItem'];
export interface SandboxQuotaUsageItem extends Omit<QuotaUsageItemBase, 'warning_level'> {
  warning_level: SandboxWarningLevel;
}

// Override QuotaStatusResponse to use our typed QuotaUsageItem
type QuotaStatusResponseBase = components['schemas']['QuotaStatusResponse'];
export interface SandboxQuotaStatus extends Omit<QuotaStatusResponseBase, 'concurrent' | 'daily_minutes' | 'monthly_minutes' | 'daily_sessions' | 'overall_warning_level'> {
  concurrent: SandboxQuotaUsageItem;
  daily_minutes: SandboxQuotaUsageItem;
  monthly_minutes: SandboxQuotaUsageItem;
  daily_sessions: SandboxQuotaUsageItem;
  overall_warning_level: SandboxWarningLevel;
}

// =============================================================================
// Manual Types (not in OpenAPI spec or need different structure)
// =============================================================================

// Filters for findings list (frontend-only, not a backend model)
export interface FindingFilters {
  severity?: Severity[];
  status?: FindingStatus[];
  detector?: string;
  analysis_run_id?: string;
  repository_id?: string;
}

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

// A complete fix proposal (backend returns dict, not typed)
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
  // Validation fields from backend
  import_valid?: boolean | null;
  type_valid?: boolean | null;
  validation_errors?: string[];
  validation_warnings?: string[];
}

// Comment on a fix
export interface FixComment {
  id: string;
  fix_id: string;
  author: string;
  content: string;
  created_at: string;
}

// Filter options for fix list (frontend-only)
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

// Sort options (frontend-only)
export interface SortOptions {
  field: 'created_at' | 'confidence' | 'status' | 'fix_type';
  direction: 'asc' | 'desc';
}

// API response wrapper (generic, frontend-only)
export interface ApiResponse<T> {
  data: T;
  success: boolean;
  error?: string;
}

// Paginated response (generic, frontend-only)
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

// Repository with full details (combines multiple API responses)
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

// =============================================================================
// API Keys Types (manual - not in OpenAPI)
// =============================================================================

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

// =============================================================================
// Billing Types (manual - more detailed than generated)
// =============================================================================

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
