-- TimescaleDB schema for sandbox execution metrics
-- This file contains the database schema for tracking E2B sandbox operation costs

-- Install TimescaleDB extension (run as superuser if not already installed)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- Main sandbox metrics table
CREATE TABLE IF NOT EXISTS sandbox_metrics (
    -- Temporal dimension (partitioning key)
    time TIMESTAMPTZ NOT NULL,

    -- Operation identification
    operation_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,  -- 'test_execution', 'skill_run', 'tool_run', 'code_validation'
    sandbox_id TEXT,

    -- Timing metrics
    duration_ms INTEGER,

    -- Resource usage
    cpu_seconds FLOAT,
    memory_gb_seconds FLOAT,

    -- Cost tracking (USD)
    cost_usd FLOAT,

    -- Status
    success BOOLEAN DEFAULT FALSE,
    exit_code INTEGER,
    error_message TEXT,

    -- Context for billing and analytics
    customer_id TEXT,
    project_id TEXT,
    repository_id TEXT,

    -- Subscription tier info
    tier TEXT,          -- 'FREE', 'PRO', 'ENTERPRISE'
    template TEXT       -- E2B template used
);

-- Convert to hypertable (partitioned by time)
-- Chunk interval: 1 day (appropriate for high-volume operational metrics)
SELECT create_hypertable(
    'sandbox_metrics',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Indexes for common query patterns

-- Customer-based queries (billing, usage by customer)
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_customer
    ON sandbox_metrics (customer_id, time DESC);

-- Operation type queries (analytics by operation)
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_operation
    ON sandbox_metrics (operation_type, time DESC);

-- Project-based queries
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_project
    ON sandbox_metrics (project_id, time DESC)
    WHERE project_id IS NOT NULL;

-- Repository-based queries
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_repository
    ON sandbox_metrics (repository_id, time DESC)
    WHERE repository_id IS NOT NULL;

-- Success/failure queries
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_success
    ON sandbox_metrics (success, time DESC);

-- Tier-based queries
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_tier
    ON sandbox_metrics (tier, time DESC)
    WHERE tier IS NOT NULL;

-- Operation ID lookup
CREATE INDEX IF NOT EXISTS idx_sandbox_metrics_operation_id
    ON sandbox_metrics (operation_id);

-- Compression policy
-- Note: Timescale Cloud manages compression automatically via tiered storage.
-- For self-hosted TimescaleDB, uncomment and run:
-- ALTER TABLE sandbox_metrics SET (timescaledb.compress);
-- SELECT add_compression_policy('sandbox_metrics', INTERVAL '7 days', if_not_exists => TRUE);

-- Retention policy (delete data older than 90 days)
-- Adjust based on compliance requirements
SELECT add_retention_policy(
    'sandbox_metrics',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

-- Continuous aggregate: hourly cost summary
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_sandbox_costs
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS hour,
    customer_id,
    operation_type,
    COUNT(*) AS operation_count,
    SUM(CASE WHEN success THEN 1 ELSE 0 END) AS success_count,
    SUM(cost_usd) AS total_cost,
    SUM(cpu_seconds) AS total_cpu_seconds,
    SUM(memory_gb_seconds) AS total_memory_gb_seconds,
    AVG(duration_ms) AS avg_duration_ms
FROM sandbox_metrics
GROUP BY hour, customer_id, operation_type;

-- Refresh policy for hourly summary (update every 15 minutes)
SELECT add_continuous_aggregate_policy(
    'hourly_sandbox_costs',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

-- Continuous aggregate: daily cost by customer
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_customer_sandbox_costs
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS day,
    customer_id,
    COUNT(*) AS total_operations,
    SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successful_operations,
    SUM(cost_usd) AS total_cost,
    SUM(cpu_seconds) AS total_cpu_seconds,
    SUM(memory_gb_seconds) AS total_memory_gb_seconds,
    AVG(duration_ms) AS avg_duration_ms,
    MAX(duration_ms) AS max_duration_ms
FROM sandbox_metrics
WHERE customer_id IS NOT NULL
GROUP BY day, customer_id;

-- Refresh policy for daily customer summary
SELECT add_continuous_aggregate_policy(
    'daily_customer_sandbox_costs',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- View: Current day's cost summary per customer
CREATE OR REPLACE VIEW today_customer_costs AS
SELECT
    customer_id,
    COUNT(*) AS total_operations,
    SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successful_operations,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
    ROUND(AVG(duration_ms)::numeric, 0) AS avg_duration_ms
FROM sandbox_metrics
WHERE time >= DATE_TRUNC('day', NOW())
  AND customer_id IS NOT NULL
GROUP BY customer_id
ORDER BY total_cost_usd DESC;

-- View: Recent failure summary (last hour)
CREATE OR REPLACE VIEW recent_failures AS
SELECT
    time,
    operation_id,
    operation_type,
    error_message,
    duration_ms,
    customer_id,
    sandbox_id
FROM sandbox_metrics
WHERE time > NOW() - INTERVAL '1 hour'
  AND NOT success
ORDER BY time DESC;

-- View: Slow operations (>10s in last day)
CREATE OR REPLACE VIEW slow_operations AS
SELECT
    time,
    operation_id,
    operation_type,
    duration_ms,
    cost_usd,
    success,
    customer_id,
    sandbox_id
FROM sandbox_metrics
WHERE time > NOW() - INTERVAL '1 day'
  AND duration_ms > 10000
ORDER BY duration_ms DESC;

-- Function: Get customer cost for period
CREATE OR REPLACE FUNCTION get_customer_cost(
    p_customer_id TEXT,
    p_start_date TIMESTAMPTZ DEFAULT NOW() - INTERVAL '30 days',
    p_end_date TIMESTAMPTZ DEFAULT NOW()
)
RETURNS TABLE (
    total_operations BIGINT,
    successful_operations BIGINT,
    success_rate NUMERIC,
    total_cost_usd NUMERIC,
    avg_duration_ms NUMERIC,
    total_cpu_seconds NUMERIC,
    total_memory_gb_seconds NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        SUM(CASE WHEN success THEN 1 ELSE 0 END)::BIGINT,
        ROUND(SUM(CASE WHEN success THEN 1 ELSE 0 END)::NUMERIC / NULLIF(COUNT(*), 0) * 100, 2),
        ROUND(SUM(cost_usd)::NUMERIC, 6),
        ROUND(AVG(duration_ms)::NUMERIC, 0),
        ROUND(SUM(cpu_seconds)::NUMERIC, 2),
        ROUND(SUM(memory_gb_seconds)::NUMERIC, 2)
    FROM sandbox_metrics
    WHERE customer_id = p_customer_id
      AND time BETWEEN p_start_date AND p_end_date;
END;
$$ LANGUAGE plpgsql;

-- Function: Check if customer is over cost threshold
CREATE OR REPLACE FUNCTION check_cost_threshold(
    p_customer_id TEXT,
    p_threshold_usd NUMERIC,
    p_period_hours INTEGER DEFAULT 24
)
RETURNS BOOLEAN AS $$
DECLARE
    current_cost NUMERIC;
BEGIN
    SELECT SUM(cost_usd)
    INTO current_cost
    FROM sandbox_metrics
    WHERE customer_id = p_customer_id
      AND time > NOW() - (p_period_hours || ' hours')::INTERVAL;

    RETURN COALESCE(current_cost, 0) > p_threshold_usd;
END;
$$ LANGUAGE plpgsql;

-- Function: Get failure rate for alerting
CREATE OR REPLACE FUNCTION get_failure_rate(
    p_hours INTEGER DEFAULT 1,
    p_customer_id TEXT DEFAULT NULL
)
RETURNS TABLE (
    total_operations BIGINT,
    failures BIGINT,
    failure_rate NUMERIC
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT,
        SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)::BIGINT,
        ROUND(SUM(CASE WHEN NOT success THEN 1 ELSE 0 END)::NUMERIC / NULLIF(COUNT(*), 0) * 100, 2)
    FROM sandbox_metrics
    WHERE time > NOW() - (p_hours || ' hours')::INTERVAL
      AND (p_customer_id IS NULL OR customer_id = p_customer_id);
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- Trial and Usage Tracking (REPO-296)
-- =============================================================================

-- Customer usage tracking table
CREATE TABLE IF NOT EXISTS customer_usage (
    customer_id TEXT PRIMARY KEY,
    executions_used INTEGER DEFAULT 0,
    subscription_tier TEXT DEFAULT 'trial',  -- 'trial', 'free', 'pro', 'enterprise'
    trial_started_at TIMESTAMPTZ DEFAULT NOW(),
    last_execution_at TIMESTAMPTZ,
    monthly_reset_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for tier-based queries
CREATE INDEX IF NOT EXISTS idx_customer_usage_tier
    ON customer_usage (subscription_tier);

-- Function: Check if customer can execute (trial limit check)
CREATE OR REPLACE FUNCTION check_trial_limit(
    p_customer_id TEXT,
    p_trial_limit INTEGER DEFAULT 50
)
RETURNS TABLE (
    can_execute BOOLEAN,
    executions_used INTEGER,
    executions_limit INTEGER,
    is_trial BOOLEAN,
    message TEXT
) AS $$
DECLARE
    v_tier TEXT;
    v_used INTEGER;
    v_limit INTEGER;
    v_monthly_reset TIMESTAMPTZ;
BEGIN
    -- Get or create customer usage record
    INSERT INTO customer_usage (customer_id)
    VALUES (p_customer_id)
    ON CONFLICT (customer_id) DO NOTHING;

    -- Get current status
    SELECT cu.subscription_tier, cu.executions_used, cu.monthly_reset_at
    INTO v_tier, v_used, v_monthly_reset
    FROM customer_usage cu
    WHERE cu.customer_id = p_customer_id;

    -- Determine limit based on tier (Option A: trial → paid, no free tier)
    v_limit := CASE v_tier
        WHEN 'trial' THEN p_trial_limit  -- 50 one-time to try the product
        WHEN 'pro' THEN 5000             -- $49/mo, 5000 executions/month
        WHEN 'enterprise' THEN -1        -- Unlimited, custom pricing
        ELSE 0                           -- Unknown tier = blocked
    END;

    -- Check for monthly reset (pro tier only, trial doesn't reset)
    IF v_tier = 'pro' AND v_monthly_reset IS NOT NULL THEN
        IF NOW() - v_monthly_reset > INTERVAL '30 days' THEN
            -- Reset monthly usage
            UPDATE customer_usage
            SET executions_used = 0,
                monthly_reset_at = NOW(),
                updated_at = NOW()
            WHERE customer_id = p_customer_id;
            v_used := 0;
        END IF;
    END IF;

    -- Return result
    RETURN QUERY SELECT
        CASE
            WHEN v_limit = -1 THEN TRUE  -- Unlimited
            WHEN v_used < v_limit THEN TRUE
            ELSE FALSE
        END,
        v_used,
        v_limit,
        v_tier = 'trial',
        CASE
            WHEN v_limit = -1 THEN 'Unlimited executions'
            WHEN v_used < v_limit THEN format('OK (%s/%s executions used)', v_used, v_limit)
            WHEN v_tier = 'trial' THEN format('Trial limit exceeded (%s/%s). Upgrade at https://repotoire.dev/pricing', v_used, v_limit)
            ELSE format('Monthly limit exceeded (%s/%s). Upgrade or wait for reset.', v_used, v_limit)
        END;
END;
$$ LANGUAGE plpgsql;

-- Function: Increment usage count
CREATE OR REPLACE FUNCTION increment_customer_usage(
    p_customer_id TEXT
)
RETURNS INTEGER AS $$
DECLARE
    v_new_count INTEGER;
BEGIN
    INSERT INTO customer_usage (customer_id, executions_used, last_execution_at)
    VALUES (p_customer_id, 1, NOW())
    ON CONFLICT (customer_id) DO UPDATE
    SET executions_used = customer_usage.executions_used + 1,
        last_execution_at = NOW(),
        updated_at = NOW()
    RETURNING executions_used INTO v_new_count;

    RETURN v_new_count;
END;
$$ LANGUAGE plpgsql;

-- Function: Upgrade customer tier
CREATE OR REPLACE FUNCTION upgrade_customer_tier(
    p_customer_id TEXT,
    p_new_tier TEXT
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO customer_usage (customer_id, subscription_tier, monthly_reset_at)
    VALUES (p_customer_id, p_new_tier, NOW())
    ON CONFLICT (customer_id) DO UPDATE
    SET subscription_tier = p_new_tier,
        monthly_reset_at = NOW(),
        updated_at = NOW();
END;
$$ LANGUAGE plpgsql;

-- View: Current trial/usage status for all customers
-- Option A: Simple trial → paid (no free tier)
CREATE OR REPLACE VIEW customer_usage_status AS
SELECT
    cu.customer_id,
    cu.executions_used,
    cu.subscription_tier,
    CASE cu.subscription_tier
        WHEN 'trial' THEN 50       -- 50 one-time to try
        WHEN 'pro' THEN 5000       -- $49/mo, 5000/month
        WHEN 'enterprise' THEN -1  -- Unlimited
        ELSE 0                     -- Unknown = blocked
    END AS executions_limit,
    cu.trial_started_at,
    cu.last_execution_at,
    cu.monthly_reset_at,
    CASE
        WHEN cu.subscription_tier = 'enterprise' THEN 0
        WHEN cu.subscription_tier NOT IN ('trial', 'pro', 'enterprise') THEN 100  -- Blocked
        ELSE ROUND(cu.executions_used::NUMERIC / NULLIF(
            CASE cu.subscription_tier
                WHEN 'trial' THEN 50
                WHEN 'pro' THEN 5000
                ELSE 1
            END, 0) * 100, 1)
    END AS usage_percentage
FROM customer_usage cu
ORDER BY cu.last_execution_at DESC NULLS LAST;

-- =============================================================================
-- Documentation
-- =============================================================================

-- Comments for documentation
COMMENT ON TABLE sandbox_metrics IS 'Time-series storage for E2B sandbox execution metrics and costs';
COMMENT ON COLUMN sandbox_metrics.time IS 'Timestamp of operation completion (partitioning key)';
COMMENT ON COLUMN sandbox_metrics.operation_type IS 'Type: test_execution, skill_run, tool_run, code_validation';
COMMENT ON COLUMN sandbox_metrics.cost_usd IS 'Calculated cost based on E2B pricing (CPU + memory)';
COMMENT ON COLUMN sandbox_metrics.customer_id IS 'Customer identifier for billing aggregation';
COMMENT ON FUNCTION get_customer_cost IS 'Get cost summary for a customer over a time period';
COMMENT ON FUNCTION check_cost_threshold IS 'Check if customer has exceeded cost threshold';
COMMENT ON FUNCTION get_failure_rate IS 'Get failure rate for alerting (default: last hour)';

-- Trial/usage tracking comments
COMMENT ON TABLE customer_usage IS 'Track customer execution usage and subscription tiers for trial/billing limits';
COMMENT ON COLUMN customer_usage.subscription_tier IS 'Tier: trial (50 one-time), pro ($49/mo, 5000/mo), enterprise (unlimited)';
COMMENT ON FUNCTION check_trial_limit IS 'Check if customer can execute based on tier limits';
COMMENT ON FUNCTION increment_customer_usage IS 'Increment execution count after successful operation';
COMMENT ON FUNCTION upgrade_customer_tier IS 'Upgrade customer to new subscription tier';
