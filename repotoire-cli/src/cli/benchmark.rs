use anyhow::Result;
use std::path::Path;

pub fn run(path: &Path, format: crate::reporters::OutputFormat, telemetry: &crate::telemetry::Telemetry) -> Result<()> {
    if !telemetry.is_enabled() {
        println!("Telemetry is off. Enable with: repotoire config telemetry on");
        return Ok(());
    }

    // Load last analysis from findings cache
    let health_path = crate::cache::paths::health_cache_path(path);
    if !health_path.exists() {
        println!("No analysis found. Run 'repotoire analyze' first.");
        return Ok(());
    }

    // Read cached health report to get score, language, kloc
    let health_json = std::fs::read_to_string(&health_path)?;
    let health: serde_json::Value = serde_json::from_str(&health_json)?;

    let score = health["overall_score"].as_f64().unwrap_or(0.0);
    let primary_language = health["primary_language"].as_str().unwrap_or("unknown");
    let total_kloc = health["total_kloc"].as_f64().unwrap_or(0.0);

    // Fetch benchmarks
    let benchmark_data =
        crate::telemetry::benchmarks::fetch_benchmarks(primary_language, total_kloc);

    match benchmark_data {
        Some(data) => {
            let score_pct =
                crate::telemetry::benchmarks::interpolate_percentile(score, &data.score);
            let ctx = crate::telemetry::display::EcosystemContext {
                score_percentile: score_pct,
                comparison_group: data
                    .segment
                    .language
                    .clone()
                    .unwrap_or_else(|| "all".into())
                    + " projects",
                sample_size: data.sample_size,
                pillar_percentiles: None,
                modularity_percentile: None,
                coupling_percentile: None,
                trend: None,
            };

            match format {
                crate::reporters::OutputFormat::Json => println!("{}", serde_json::to_string_pretty(&ctx)?),
                _ => println!(
                    "{}",
                    crate::telemetry::display::format_ecosystem_context(&ctx)
                ),
            }
        }
        None => {
            println!(
                "{}",
                crate::telemetry::display::format_insufficient_data(primary_language)
            );
        }
    }

    Ok(())
}
