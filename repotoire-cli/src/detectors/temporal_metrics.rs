//! Temporal metrics analyzer for code evolution tracking.
//!
//! Analyzes how code metrics change over time to detect degradation patterns,
//! code hotspots, and technical debt velocity.

use std::collections::HashMap;

use crate::graph::GraphStore;
use crate::models::Severity;

/// Metric trend information
#[derive(Debug, Clone)]
pub struct MetricTrend {
    /// Name of the metric
    pub metric_name: String,
    /// Values over time
    pub values: Vec<f64>,
    /// Timestamps (Unix timestamps)
    pub timestamps: Vec<i64>,
    /// Trend direction: "increasing", "decreasing", or "stable"
    pub trend_direction: String,
    /// Percentage change from first to last
    pub change_percentage: f64,
    /// Average change per day
    pub velocity: f64,
    /// Whether the metric is degrading
    pub is_degrading: bool,
}

/// Code hotspot information
#[derive(Debug, Clone)]
pub struct CodeHotspot {
    /// File path
    pub file_path: String,
    /// Number of modifications
    pub churn_count: i64,
    /// Complexity velocity (change per commit)
    pub complexity_velocity: f64,
    /// Coupling velocity (change per commit)
    pub coupling_velocity: f64,
    /// Risk score
    pub risk_score: f64,
    /// Last modification timestamp
    pub last_modified: i64,
    /// Top authors
    pub top_authors: Vec<String>,
}

/// Commit comparison result
#[derive(Debug, Clone, Default)]
pub struct CommitComparison {
    pub before_commit: String,
    pub after_commit: String,
    pub before_date: Option<i64>,
    pub after_date: Option<i64>,
    pub improvements: Vec<String>,
    pub regressions: Vec<String>,
    pub changes: HashMap<String, MetricChange>,
}

/// Individual metric change
#[derive(Debug, Clone)]
pub struct MetricChange {
    pub before: f64,
    pub after: f64,
    pub change: f64,
    pub change_percentage: f64,
}

/// Temporal metrics analyzer
///
/// Provides methods to:
/// - Track metric trends (modularity, coupling, complexity)
/// - Detect code hotspots (high churn + increasing complexity)
/// - Calculate technical debt velocity
/// - Compare commits (before/after analysis)
pub struct TemporalMetrics<'a> {
    graph: &'a dyn crate::graph::GraphQuery,
}

impl<'a> TemporalMetrics<'a> {
    /// Create a new temporal metrics analyzer
    pub fn new(graph: &'a dyn crate::graph::GraphQuery) -> Self {
        Self { graph }
    }

    /// Get trend for a specific metric over time
    pub fn get_metric_trend(
        &self,
        metric_name: &str,
        window_days: i64,
    ) -> anyhow::Result<Option<MetricTrend>> {
        // Validate metric name (prevent injection)
        if !Self::is_valid_metric_name(metric_name) {
            anyhow::bail!("Invalid metric name: {}", metric_name);
        }

        let cutoff_timestamp = chrono::Utc::now().timestamp() - (window_days * 24 * 60 * 60);

        let query = format!(
            r#"
            MATCH (s:Session)
            WHERE s.committedAt >= $cutoff_timestamp
            AND s.metricsSnapshot IS NOT NULL
            AND s.metricsSnapshot.{} IS NOT NULL
            RETURN
                s.committedAt as timestamp,
                s.metricsSnapshot.{} as value
            ORDER BY s.committedAt ASC
            "#,
            metric_name, metric_name
        );

        let results = self.graph.execute_with_params(
            &query,
            vec![("cutoff_timestamp", cutoff_timestamp.into())],
        )?;

        if results.len() < 2 {
            return Ok(None);
        }

        let timestamps: Vec<i64> = results
            .iter()
            .filter_map(|r| r.get_i64("timestamp"))
            .collect();
        let values: Vec<f64> = results
            .iter()
            .filter_map(|r| r.get_f64("value"))
            .collect();

        if values.len() < 2 {
            return Ok(None);
        }

        // Calculate trend statistics
        let trend_direction = Self::calculate_trend_direction(&values);
        let change_pct = if values[0] != 0.0 {
            ((values.last().expect("non-empty values") - values[0]) / values[0]) * 100.0
        } else {
            0.0
        };
        let velocity = Self::calculate_velocity(&values, &timestamps);

        // Determine if degrading
        let degrading_metrics = ["coupling", "circular_dependencies", "dead_code_percentage"];
        let improving_metrics = ["modularity", "abstraction_ratio"];

        let is_degrading = if degrading_metrics.contains(&metric_name) {
            trend_direction == "increasing"
        } else if improving_metrics.contains(&metric_name) {
            trend_direction == "decreasing"
        } else {
            false
        };

        Ok(Some(MetricTrend {
            metric_name: metric_name.to_string(),
            values,
            timestamps,
            trend_direction,
            change_percentage: change_pct,
            velocity,
            is_degrading,
        }))
    }

    /// Find code hotspots with high churn and increasing complexity
    pub fn find_code_hotspots(
        &self,
        window_days: i64,
        min_churn: i64,
    ) -> anyhow::Result<Vec<CodeHotspot>> {
        let cutoff_timestamp = chrono::Utc::now().timestamp() - (window_days * 24 * 60 * 60);

        let query = r#"
            MATCH (s:Session)-[:MODIFIED]->(f:File)
            WHERE s.committedAt >= $cutoff_timestamp
            WITH f.filePath as path, count(s) as churn_count, max(s.committedAt) as last_modified
            WHERE churn_count >= $min_churn
            RETURN path, churn_count, last_modified
            ORDER BY churn_count DESC
            LIMIT 50
        "#;

        let results = self.graph.execute_with_params(
            query,
            vec![
                ("cutoff_timestamp", cutoff_timestamp.into()),
                ("min_churn", min_churn.into()),
            ],
        )?;

        let mut hotspots = Vec::new();

        for row in results {
            let file_path = row.get_string("path").unwrap_or_default();
            let churn_count = row.get_i64("churn_count").unwrap_or(0);
            let last_modified = row.get_i64("last_modified").unwrap_or(0);

            // Placeholder values for velocity (would need historical data)
            let complexity_velocity = 0.0;
            let coupling_velocity = 0.0;

            let risk_score = churn_count as f64 * f64::max(complexity_velocity, 0.1);

            let top_authors = self.get_file_top_authors(&file_path, window_days)?;

            hotspots.push(CodeHotspot {
                file_path,
                churn_count,
                complexity_velocity,
                coupling_velocity,
                risk_score,
                last_modified,
                top_authors,
            });
        }

        // Sort by risk score
        hotspots.sort_by(|a, b| b.risk_score.partial_cmp(&a.risk_score).unwrap_or(std::cmp::Ordering::Equal));

        Ok(hotspots)
    }

    /// Compare metrics between two commits
    pub fn compare_commits(
        &self,
        before_hash: &str,
        after_hash: &str,
    ) -> anyhow::Result<CommitComparison> {
        let query = r#"
            MATCH (before:Session {commitHash: $before_hash})
            MATCH (after:Session {commitHash: $after_hash})
            RETURN
                before.metricsSnapshot as before_metrics,
                after.metricsSnapshot as after_metrics,
                before.committedAt as before_date,
                after.committedAt as after_date
        "#;

        let results = self.graph.execute_with_params(
            query,
            vec![
                ("before_hash", before_hash.into()),
                ("after_hash", after_hash.into()),
            ],
        )?;

        if results.is_empty() {
            return Ok(CommitComparison {
                before_commit: before_hash[..7.min(before_hash.len())].to_string(),
                after_commit: after_hash[..7.min(after_hash.len())].to_string(),
                ..Default::default()
            });
        }

        let row = &results[0];
        let before_metrics: HashMap<String, f64> = row
            .get_string("before_metrics")
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        let after_metrics: HashMap<String, f64> = row
            .get_string("after_metrics")
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();

        let mut comparison = CommitComparison {
            before_commit: before_hash[..7.min(before_hash.len())].to_string(),
            after_commit: after_hash[..7.min(after_hash.len())].to_string(),
            before_date: row.get_i64("before_date"),
            after_date: row.get_i64("after_date"),
            ..Default::default()
        };

        // Compare metrics
        let metrics_to_compare = [
            ("modularity", true),           // Higher is better
            ("coupling", false),            // Lower is better
            ("circular_dependencies", false),
            ("dead_code_percentage", false),
        ];

        for (metric_name, higher_is_better) in metrics_to_compare {
            if let (Some(&before_val), Some(&after_val)) =
                (before_metrics.get(metric_name), after_metrics.get(metric_name))
            {
                let change = after_val - before_val;
                let change_pct = if before_val != 0.0 {
                    (change / before_val) * 100.0
                } else {
                    0.0
                };

                comparison.changes.insert(
                    metric_name.to_string(),
                    MetricChange {
                        before: before_val,
                        after: after_val,
                        change,
                        change_percentage: change_pct,
                    },
                );

                // Determine if improvement or regression
                let is_improvement = if higher_is_better {
                    change > 0.0
                } else {
                    change < 0.0
                };

                if change != 0.0 {
                    if is_improvement {
                        comparison.improvements.push(metric_name.to_string());
                    } else {
                        comparison.regressions.push(metric_name.to_string());
                    }
                }
            }
        }

        Ok(comparison)
    }

    fn is_valid_metric_name(name: &str) -> bool {
        // Only allow alphanumeric and underscore
        name.chars().all(|c| c.is_alphanumeric() || c == '_')
    }

    fn calculate_trend_direction(values: &[f64]) -> String {
        if values.len() < 2 {
            return "stable".to_string();
        }

        let n = values.len() as f64;
        let x_mean = (n - 1.0) / 2.0;
        let y_mean: f64 = values.iter().sum::<f64>() / n;

        let mut numerator = 0.0;
        let mut denominator = 0.0;

        for (i, &y) in values.iter().enumerate() {
            let x = i as f64;
            numerator += (x - x_mean) * (y - y_mean);
            denominator += (x - x_mean).powi(2);
        }

        if denominator == 0.0 {
            return "stable".to_string();
        }

        let slope = numerator / denominator;

        if slope.abs() < 0.01 {
            "stable".to_string()
        } else if slope > 0.0 {
            "increasing".to_string()
        } else {
            "decreasing".to_string()
        }
    }

    fn calculate_velocity(values: &[f64], timestamps: &[i64]) -> f64 {
        if values.len() < 2 || timestamps.len() < 2 {
            return 0.0;
        }

        let total_change = values.last().expect("non-empty values") - values[0];
        let time_span_days = (*timestamps.last().expect("non-empty timestamps") - timestamps[0]) as f64 / 86400.0;

        if time_span_days == 0.0 {
            0.0
        } else {
            total_change / time_span_days
        }
    }

    fn get_file_top_authors(
        &self,
        file_path: &str,
        window_days: i64,
    ) -> anyhow::Result<Vec<String>> {
        let cutoff_timestamp = chrono::Utc::now().timestamp() - (window_days * 24 * 60 * 60);

        let query = r#"
            MATCH (s:Session)-[:MODIFIED]->(f:File {filePath: $file_path})
            WHERE s.committedAt >= $cutoff_timestamp
            WITH s.author as author, count(*) as mod_count
            RETURN author
            ORDER BY mod_count DESC
            LIMIT 5
        "#;

        let results = self.graph.execute_with_params(
            query,
            vec![
                ("file_path", file_path.into()),
                ("cutoff_timestamp", cutoff_timestamp.into()),
            ],
        )?;

        Ok(results
            .iter()
            .filter_map(|r| r.get_string("author"))
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_trend_direction() {
        let increasing = vec![1.0, 2.0, 3.0, 4.0, 5.0];
        assert_eq!(TemporalMetrics::calculate_trend_direction(&increasing), "increasing");

        let decreasing = vec![5.0, 4.0, 3.0, 2.0, 1.0];
        assert_eq!(TemporalMetrics::calculate_trend_direction(&decreasing), "decreasing");

        let stable = vec![3.0, 3.0, 3.0, 3.0];
        assert_eq!(TemporalMetrics::calculate_trend_direction(&stable), "stable");
    }

    #[test]
    fn test_valid_metric_name() {
        assert!(TemporalMetrics::is_valid_metric_name("modularity"));
        assert!(TemporalMetrics::is_valid_metric_name("code_coverage"));
        assert!(!TemporalMetrics::is_valid_metric_name("metric; DROP TABLE"));
        assert!(!TemporalMetrics::is_valid_metric_name("metric.nested"));
    }

    #[test]
    fn test_velocity_calculation() {
        let values = vec![10.0, 20.0];
        let timestamps = vec![0, 86400]; // 1 day apart
        assert_eq!(TemporalMetrics::calculate_velocity(&values, &timestamps), 10.0);
    }
}