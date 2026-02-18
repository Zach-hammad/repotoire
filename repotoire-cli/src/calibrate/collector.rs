//! Metric collection from parsed results

use crate::calibrate::profile::{MetricDistribution, MetricKind, StyleProfile};
use crate::parsers::ParseResult;
use std::collections::HashMap;
use tracing::info;

/// Collect metrics from parsed results (no graph needed).
pub fn collect_metrics(
    parse_results: &[(ParseResult, usize)], // (parse_result, file_loc)
    total_files: usize,
    commit_sha: Option<String>,
) -> StyleProfile {
    let mut complexity_values = Vec::new();
    let mut func_length_values = Vec::new();
    let mut nesting_values = Vec::new();
    let mut param_values = Vec::new();
    let mut file_length_values = Vec::new();
    let mut class_method_values = Vec::new();
    let mut total_functions = 0;

    for (result, file_loc) in parse_results {
        file_length_values.push(*file_loc as f64);

        for func in &result.functions {
            total_functions += 1;
            if let Some(c) = func.complexity {
                complexity_values.push(c as f64);
            }
            let loc = func.line_end.saturating_sub(func.line_start) + 1;
            if loc > 0 {
                func_length_values.push(loc as f64);
            }
            param_values.push(func.parameters.len() as f64);
            if let Some(n) = func.max_nesting {
                nesting_values.push(n as f64);
            }
        }

        for class in &result.classes {
            class_method_values.push(class.methods.len() as f64);
        }
    }

    let mut metrics = HashMap::new();
    metrics.insert(MetricKind::Complexity, MetricDistribution::from_values(&mut complexity_values));
    metrics.insert(MetricKind::FunctionLength, MetricDistribution::from_values(&mut func_length_values));
    metrics.insert(MetricKind::NestingDepth, MetricDistribution::from_values(&mut nesting_values));
    metrics.insert(MetricKind::ParameterCount, MetricDistribution::from_values(&mut param_values));
    metrics.insert(MetricKind::FileLength, MetricDistribution::from_values(&mut file_length_values));
    metrics.insert(MetricKind::ClassMethodCount, MetricDistribution::from_values(&mut class_method_values));

    let now = chrono::Utc::now().to_rfc3339();

    info!(
        "Calibrated {} metrics from {} functions across {} files",
        metrics.len(), total_functions, total_files
    );

    for kind in MetricKind::all() {
        if let Some(dist) = metrics.get(kind) {
            if dist.confident {
                info!(
                    "  {}: mean={:.1}, p50={:.0}, p90={:.0}, p95={:.0}, max={:.0} (n={})",
                    kind.name(), dist.mean, dist.p50, dist.p90, dist.p95, dist.max, dist.count
                );
            }
        }
    }

    StyleProfile {
        version: StyleProfile::VERSION,
        generated_at: now,
        commit_sha,
        total_files,
        total_functions,
        metrics,
    }
}
