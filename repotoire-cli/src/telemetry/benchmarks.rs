//! Benchmark data fetch with CDN fallback chain and percentile interpolation.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::{Duration, SystemTime};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BENCHMARK_BASE_URL: &str = "https://benchmarks.repotoire.dev/v1";
const EXPECTED_SCHEMA_VERSION: u32 = 1;
const MIN_SAMPLE_SIZE: u64 = 50;
const CACHE_FRESH_SECS: u64 = 24 * 60 * 60; // 24 hours
const CACHE_STALE_SECS: u64 = 48 * 60 * 60; // 48 hours

// ---------------------------------------------------------------------------
// Structs
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BenchmarkData {
    pub schema_version: u32,
    pub segment: BenchmarkSegment,
    pub sample_size: u64,
    pub updated_at: String,
    pub score: PercentileDistribution,
    pub pillar_structure: PercentileDistribution,
    pub pillar_quality: PercentileDistribution,
    pub pillar_architecture: PercentileDistribution,
    pub graph_modularity: PercentileDistribution,
    pub graph_avg_degree: PercentileDistribution,
    pub graph_scc_count: SccDistribution,
    pub grade_distribution: HashMap<String, f64>,
    pub top_detectors: Vec<DetectorStat>,
    pub detector_accuracy: Vec<DetectorAccuracy>,
    pub avg_improvement_per_analysis: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BenchmarkSegment {
    pub language: Option<String>,
    pub kloc_bucket: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PercentileDistribution {
    pub p25: f64,
    pub p50: f64,
    pub p75: f64,
    pub p90: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SccDistribution {
    pub pct_zero: f64,
    pub p50: f64,
    pub p75: f64,
    pub p90: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DetectorStat {
    pub name: String,
    pub pct_repos_with_findings: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DetectorAccuracy {
    pub name: String,
    pub true_positive_rate: f64,
    pub feedback_count: u64,
}

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

/// Convert kLOC to size bucket (inclusive-exclusive boundaries)
pub fn kloc_to_bucket(kloc: f64) -> &'static str {
    match kloc {
        k if k < 5.0 => "0-5k",
        k if k < 10.0 => "5-10k",
        k if k < 50.0 => "10-50k",
        k if k < 100.0 => "50-100k",
        _ => "100k+",
    }
}

/// Build fallback URL chain: language+size → language → global
pub fn build_fallback_urls(language: &str, kloc: f64) -> Vec<String> {
    let bucket = kloc_to_bucket(kloc);
    vec![
        format!("{}/lang/{}/{}.json", BENCHMARK_BASE_URL, language, bucket),
        format!("{}/lang/{}.json", BENCHMARK_BASE_URL, language),
        format!("{}/global.json", BENCHMARK_BASE_URL),
    ]
}

/// Interpolate percentile rank from p25/p50/p75/p90 distribution.
///
/// Returns a value in [0, 100] representing where `value` falls in the distribution.
pub fn interpolate_percentile(value: f64, dist: &PercentileDistribution) -> f64 {
    let pct = if value <= dist.p25 {
        // Interpolate 0–25: assume distribution extends to 0 at p25 - (p50-p25)*2
        // Use linear interpolation from 0 to 25 over [p25 - range, p25]
        let range = dist.p50 - dist.p25;
        let lower_bound = dist.p25 - 2.0 * range;
        if range <= 0.0 || value <= lower_bound {
            0.0
        } else {
            25.0 * (value - lower_bound) / (dist.p25 - lower_bound)
        }
    } else if value <= dist.p50 {
        // Interpolate 25–50
        let range = dist.p50 - dist.p25;
        if range <= 0.0 {
            25.0
        } else {
            25.0 + 25.0 * (value - dist.p25) / range
        }
    } else if value <= dist.p75 {
        // Interpolate 50–75
        let range = dist.p75 - dist.p50;
        if range <= 0.0 {
            50.0
        } else {
            50.0 + 25.0 * (value - dist.p50) / range
        }
    } else if value <= dist.p90 {
        // Interpolate 75–90
        let range = dist.p90 - dist.p75;
        if range <= 0.0 {
            75.0
        } else {
            75.0 + 15.0 * (value - dist.p75) / range
        }
    } else {
        // Interpolate 90–100: assume tail extends to p90 + (p90-p75)*2
        let range = dist.p90 - dist.p75;
        let upper_bound = dist.p90 + 2.0 * range;
        if range <= 0.0 || value >= upper_bound {
            100.0
        } else {
            90.0 + 10.0 * (value - dist.p90) / (upper_bound - dist.p90)
        }
    };

    pct.clamp(0.0, 100.0)
}

// ---------------------------------------------------------------------------
// Cache helpers
// ---------------------------------------------------------------------------

/// Cache file path for a given URL slug (last two path segments, normalized).
fn cache_file_path(url: &str) -> std::path::PathBuf {
    let cache_dir = crate::cache::paths::benchmark_cache_dir();
    // Build a safe filename from the URL by replacing slashes and special chars.
    let slug = url
        .trim_start_matches("https://")
        .replace(['/', '.'], "_");
    cache_dir.join(format!("{}.json", slug))
}

/// Returns the age of a cache file in seconds, or None if it doesn't exist.
fn cache_age_secs(path: &std::path::Path) -> Option<u64> {
    let metadata = std::fs::metadata(path).ok()?;
    let modified = metadata.modified().ok()?;
    let age = SystemTime::now().duration_since(modified).ok()?;
    Some(age.as_secs())
}

/// Write benchmark data to cache.
fn write_cache(path: &std::path::Path, data: &BenchmarkData) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(json) = serde_json::to_string(data) {
        let _ = std::fs::write(path, json);
    }
}

/// Read benchmark data from cache.
fn read_cache(path: &std::path::Path) -> Option<BenchmarkData> {
    let content = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&content).ok()
}

// ---------------------------------------------------------------------------
// Fetch logic
// ---------------------------------------------------------------------------

/// Attempt a single CDN fetch with a 5-second timeout. Returns None on any error.
fn try_fetch_url(url: &str) -> Option<BenchmarkData> {
    let agent = ureq::config::Config::builder()
        .timeout_global(Some(Duration::from_secs(5)))
        .build()
        .new_agent();

    let response = agent.get(url).call().ok()?;
    let data: BenchmarkData = response.into_body().read_json().ok()?;

    // Validate schema version and sample size
    if data.schema_version != EXPECTED_SCHEMA_VERSION {
        return None;
    }
    if data.sample_size < MIN_SAMPLE_SIZE {
        return None;
    }

    Some(data)
}

/// Fetch benchmarks from CDN with fallback chain.
///
/// Returns `None` if all fetches fail or data is insufficient.
/// Uses a 24-hour cache. If CDN fails, stale cache (<48h) is returned.
pub fn fetch_benchmarks(language: &str, kloc: f64) -> Option<BenchmarkData> {
    let urls = build_fallback_urls(language, kloc);

    // Check the primary URL's cache first (freshness check applies to first URL)
    let primary_cache_path = cache_file_path(&urls[0]);

    if let Some(age) = cache_age_secs(&primary_cache_path) {
        if age < CACHE_FRESH_SECS {
            // Cache is fresh — use it
            if let Some(data) = read_cache(&primary_cache_path) {
                return Some(data);
            }
        }
    }

    // Try each URL in the fallback chain
    for url in &urls {
        let cache_path = cache_file_path(url);

        if let Some(data) = try_fetch_url(url) {
            // Cache the successful result
            write_cache(&cache_path, &data);
            return Some(data);
        }
    }

    // CDN failed — return stale cache if within 48h
    if let Some(age) = cache_age_secs(&primary_cache_path) {
        if age < CACHE_STALE_SECS {
            if let Some(data) = read_cache(&primary_cache_path) {
                return Some(data);
            }
        }
    }

    // Try stale cache from any fallback URL
    for url in &urls[1..] {
        let cache_path = cache_file_path(url);
        if let Some(age) = cache_age_secs(&cache_path) {
            if age < CACHE_STALE_SECS {
                if let Some(data) = read_cache(&cache_path) {
                    return Some(data);
                }
            }
        }
    }

    None
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_kloc_to_bucket() {
        assert_eq!(kloc_to_bucket(3.0), "0-5k");
        assert_eq!(kloc_to_bucket(5.0), "5-10k"); // 5.0 is in [5k, 10k)
        assert_eq!(kloc_to_bucket(20.0), "10-50k");
        assert_eq!(kloc_to_bucket(75.0), "50-100k");
        assert_eq!(kloc_to_bucket(150.0), "100k+");
    }

    #[test]
    fn test_fallback_chain_order() {
        let chain = build_fallback_urls("rust", 20.0);
        assert_eq!(chain.len(), 3);
        assert!(chain[0].contains("lang/rust/10-50k.json"));
        assert!(chain[1].contains("lang/rust.json"));
        assert!(chain[2].contains("global.json"));
    }

    #[test]
    fn test_interpolate_percentile_at_p50() {
        let dist = PercentileDistribution {
            p25: 50.0,
            p50: 65.0,
            p75: 80.0,
            p90: 90.0,
        };
        let pct = interpolate_percentile(65.0, &dist);
        assert!((pct - 50.0).abs() < 0.1); // exactly at p50
    }

    #[test]
    fn test_interpolate_percentile_between_p50_p75() {
        let dist = PercentileDistribution {
            p25: 50.0,
            p50: 65.0,
            p75: 80.0,
            p90: 90.0,
        };
        let pct = interpolate_percentile(72.5, &dist);
        assert!(pct > 50.0 && pct < 75.0);
    }

    #[test]
    fn test_interpolate_percentile_above_p90() {
        let dist = PercentileDistribution {
            p25: 50.0,
            p50: 65.0,
            p75: 80.0,
            p90: 90.0,
        };
        let pct = interpolate_percentile(95.0, &dist);
        assert!(pct > 90.0 && pct <= 100.0);
    }

    #[test]
    fn test_interpolate_percentile_below_p25() {
        let dist = PercentileDistribution {
            p25: 50.0,
            p50: 65.0,
            p75: 80.0,
            p90: 90.0,
        };
        let pct = interpolate_percentile(30.0, &dist);
        assert!(pct >= 0.0 && pct < 25.0);
    }

    #[test]
    fn test_parse_benchmark_json() {
        let json = r#"{
            "schema_version": 1,
            "segment": {"language": "rust", "kloc_bucket": "10-50k"},
            "sample_size": 1247,
            "updated_at": "2026-03-20T14:00:00Z",
            "score": {"p25": 58.2, "p50": 67.1, "p75": 76.8, "p90": 84.3},
            "pillar_structure": {"p25": 62.0, "p50": 71.3, "p75": 80.1, "p90": 87.5},
            "pillar_quality": {"p25": 55.1, "p50": 64.8, "p75": 74.2, "p90": 82.0},
            "pillar_architecture": {"p25": 60.4, "p50": 69.7, "p75": 78.5, "p90": 85.8},
            "graph_modularity": {"p25": 0.45, "p50": 0.58, "p75": 0.71, "p90": 0.82},
            "graph_avg_degree": {"p25": 3.2, "p50": 5.1, "p75": 8.4, "p90": 12.7},
            "graph_scc_count": {"pct_zero": 0.45, "p50": 2, "p75": 5, "p90": 11},
            "grade_distribution": {"A+": 0.02, "A": 0.05, "B+": 0.12, "B": 0.18},
            "top_detectors": [{"name": "dead_code", "pct_repos_with_findings": 0.78}],
            "detector_accuracy": [{"name": "sql_injection", "true_positive_rate": 0.88, "feedback_count": 234}],
            "avg_improvement_per_analysis": 0.8
        }"#;
        let data: BenchmarkData = serde_json::from_str(json).expect("parse benchmark JSON");
        assert_eq!(data.schema_version, 1);
        assert_eq!(data.sample_size, 1247);
        assert!((data.score.p50 - 67.1).abs() < f64::EPSILON);
    }
}
