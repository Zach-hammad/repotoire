-- TimescaleDB schema for Repotoire metrics tracking
-- This file contains the database schema for storing code health metrics over time

-- Install TimescaleDB extension (run as superuser)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Main metrics table
CREATE TABLE IF NOT EXISTS code_health_metrics (
    -- Temporal dimension
    time TIMESTAMPTZ NOT NULL,

    -- Multi-tenant isolation (REPO-600)
    tenant_id TEXT NOT NULL,

    -- Repository context
    repository TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    commit_sha TEXT,

    -- Overall health scores (0-100)
    overall_health FLOAT,
    structure_health FLOAT,
    quality_health FLOAT,
    architecture_health FLOAT,

    -- Issue counts by severity
    critical_count INT DEFAULT 0,
    high_count INT DEFAULT 0,
    medium_count INT DEFAULT 0,
    low_count INT DEFAULT 0,
    total_findings INT DEFAULT 0,

    -- Codebase statistics
    total_files INT DEFAULT 0,
    total_classes INT DEFAULT 0,
    total_functions INT DEFAULT 0,
    total_loc INT DEFAULT 0,

    -- Structural metrics
    modularity FLOAT DEFAULT 0.0,
    avg_coupling FLOAT DEFAULT 0.0,
    circular_dependencies INT DEFAULT 0,
    bottleneck_count INT DEFAULT 0,

    -- Quality metrics
    dead_code_percentage FLOAT DEFAULT 0.0,
    duplication_percentage FLOAT DEFAULT 0.0,
    god_class_count INT DEFAULT 0,

    -- Architecture metrics
    layer_violations INT DEFAULT 0,
    boundary_violations INT DEFAULT 0,
    abstraction_ratio FLOAT DEFAULT 0.0,

    -- Additional metadata (JSON format for flexibility)
    metadata JSONB,

    -- Primary key for deduplication (includes tenant_id for isolation)
    PRIMARY KEY (time, tenant_id, repository, branch)
);

-- Convert to hypertable (partitioned by time)
-- Chunk interval: 7 days (good balance for code health metrics)
SELECT create_hypertable(
    'code_health_metrics',
    'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Indexes for common query patterns (tenant_id first for isolation)
CREATE INDEX IF NOT EXISTS idx_tenant_repo_time
    ON code_health_metrics (tenant_id, repository, time DESC);

CREATE INDEX IF NOT EXISTS idx_tenant_branch
    ON code_health_metrics (tenant_id, repository, branch, time DESC);

CREATE INDEX IF NOT EXISTS idx_commit
    ON code_health_metrics (commit_sha)
    WHERE commit_sha IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_metadata
    ON code_health_metrics USING GIN (metadata);

-- Compression policy (compress chunks older than 30 days)
SELECT add_compression_policy(
    'code_health_metrics',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Retention policy (delete data older than 1 year)
SELECT add_retention_policy(
    'code_health_metrics',
    INTERVAL '1 year',
    if_not_exists => TRUE
);

-- Continuous aggregate: daily health summary (with tenant isolation)
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_health_summary
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS day,
    tenant_id,
    repository,
    branch,
    AVG(overall_health) AS avg_health,
    MIN(overall_health) AS min_health,
    MAX(overall_health) AS max_health,
    AVG(total_findings) AS avg_issues,
    SUM(critical_count) AS total_critical,
    SUM(high_count) AS total_high,
    COUNT(*) AS num_analyses
FROM code_health_metrics
GROUP BY day, tenant_id, repository, branch;

-- Refresh policy for daily summary (update every hour)
SELECT add_continuous_aggregate_policy(
    'daily_health_summary',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Continuous aggregate: weekly trend analysis (with tenant isolation)
CREATE MATERIALIZED VIEW IF NOT EXISTS weekly_trend_analysis
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 week', time) AS week,
    tenant_id,
    repository,
    branch,
    AVG(overall_health) AS avg_health,
    AVG(structure_health) AS avg_structure,
    AVG(quality_health) AS avg_quality,
    AVG(architecture_health) AS avg_architecture,
    AVG(total_findings) AS avg_issues,
    AVG(total_files) AS avg_files,
    COUNT(*) AS num_analyses
FROM code_health_metrics
GROUP BY week, tenant_id, repository, branch;

-- Refresh policy for weekly summary
SELECT add_continuous_aggregate_policy(
    'weekly_trend_analysis',
    start_offset => INTERVAL '3 weeks',
    end_offset => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- View: Latest metrics for each tenant/repository/branch
CREATE OR REPLACE VIEW latest_metrics AS
SELECT DISTINCT ON (tenant_id, repository, branch)
    *
FROM code_health_metrics
ORDER BY tenant_id, repository, branch, time DESC;

-- View: Regression candidates (health score dropped >5 points, tenant-isolated)
CREATE OR REPLACE VIEW potential_regressions AS
WITH scored AS (
    SELECT
        *,
        LAG(overall_health) OVER (
            PARTITION BY tenant_id, repository, branch
            ORDER BY time
        ) AS prev_health
    FROM code_health_metrics
)
SELECT
    time,
    tenant_id,
    repository,
    branch,
    commit_sha,
    overall_health AS current_health,
    prev_health,
    (prev_health - overall_health) AS health_drop
FROM scored
WHERE prev_health IS NOT NULL
  AND (prev_health - overall_health) > 5.0
ORDER BY time DESC;

-- Function: Get trend direction (improving/declining/stable) with tenant isolation
CREATE OR REPLACE FUNCTION get_trend_direction(
    p_tenant_id TEXT,
    p_repository TEXT,
    p_branch TEXT DEFAULT 'main',
    p_days INT DEFAULT 30
)
RETURNS TEXT AS $$
DECLARE
    trend_slope FLOAT;
BEGIN
    SELECT
        regr_slope(overall_health, EXTRACT(EPOCH FROM time))
    INTO trend_slope
    FROM code_health_metrics
    WHERE tenant_id = p_tenant_id
      AND repository = p_repository
      AND branch = p_branch
      AND time > NOW() - (p_days || ' days')::INTERVAL;

    RETURN CASE
        WHEN trend_slope > 0.01 THEN 'improving'
        WHEN trend_slope < -0.01 THEN 'declining'
        ELSE 'stable'
    END;
END;
$$ LANGUAGE plpgsql;

-- Comments for documentation
COMMENT ON TABLE code_health_metrics IS 'Time-series storage for code health metrics from Repotoire analysis';
COMMENT ON COLUMN code_health_metrics.time IS 'Timestamp of analysis (partitioning key)';
COMMENT ON COLUMN code_health_metrics.tenant_id IS 'Organization UUID for multi-tenant isolation (REPO-600)';
COMMENT ON COLUMN code_health_metrics.repository IS 'Repository identifier (path or name)';
COMMENT ON COLUMN code_health_metrics.branch IS 'Git branch name';
COMMENT ON COLUMN code_health_metrics.metadata IS 'Additional context (team, version, CI build ID, etc.)';
