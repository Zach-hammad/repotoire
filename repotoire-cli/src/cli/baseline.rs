use crate::baseline::{Baseline, BaselineEntry};
use crate::cli::BaselineAction;
use crate::engine::{AnalysisConfig, AnalysisEngine};
use anyhow::Result;
use std::path::Path;

pub fn run(repo_path: &Path, action: BaselineAction) -> Result<()> {
    match action {
        BaselineAction::Update { replace } => {
            let mut baseline = if replace {
                Baseline::empty()
            } else {
                Baseline::load(repo_path)?
            };

            // Run analysis to get current findings
            let no_git = !repo_path.join(".git").exists();
            let config = AnalysisConfig {
                no_git,
                ..Default::default()
            };
            let mut engine = AnalysisEngine::new(repo_path, false)?;
            let result = engine.analyze(&config)?;

            let mut added = 0;
            for finding in &result.findings {
                let fingerprint = crate::baseline::fingerprint::file_fingerprint(
                    &finding.detector,
                    &finding
                        .affected_files
                        .first()
                        .map(|p| p.to_string_lossy().to_string())
                        .unwrap_or_default(),
                    finding.description.lines().next().unwrap_or(""),
                );
                if baseline.add(BaselineEntry {
                    detector: finding.detector.clone(),
                    fingerprint,
                    qualified_name: None,
                    file: finding
                        .affected_files
                        .first()
                        .map(|p| p.to_string_lossy().to_string()),
                    first_line_content: finding.description.lines().next().map(|s| s.to_string()),
                    accepted_by: None,
                    reason: None,
                }) {
                    added += 1;
                }
            }

            let path = baseline.save(repo_path)?;
            eprintln!(
                "Added {} findings to baseline ({} total). Saved to {}",
                added,
                baseline.findings.len(),
                path.display()
            );
            Ok(())
        }
        BaselineAction::Add {
            fingerprint,
            reason,
        } => {
            let mut baseline = Baseline::load(repo_path)?;
            if baseline.add(BaselineEntry {
                detector: String::new(),
                fingerprint: fingerprint.clone(),
                qualified_name: None,
                file: None,
                first_line_content: None,
                accepted_by: None,
                reason,
            }) {
                baseline.save(repo_path)?;
                eprintln!("Finding {} added to baseline", fingerprint);
            } else {
                eprintln!("Finding {} already in baseline", fingerprint);
            }
            Ok(())
        }
        BaselineAction::Prune => {
            let mut baseline = Baseline::load(repo_path)?;

            let no_git = !repo_path.join(".git").exists();
            let config = AnalysisConfig {
                no_git,
                ..Default::default()
            };
            let mut engine = AnalysisEngine::new(repo_path, false)?;
            let result = engine.analyze(&config)?;

            let active: std::collections::HashSet<String> = result
                .findings
                .iter()
                .map(|f| {
                    crate::baseline::fingerprint::file_fingerprint(
                        &f.detector,
                        &f.affected_files
                            .first()
                            .map(|p| p.to_string_lossy().to_string())
                            .unwrap_or_default(),
                        f.description.lines().next().unwrap_or(""),
                    )
                })
                .collect();

            let removed = baseline.prune(&active);
            baseline.save(repo_path)?;
            eprintln!(
                "Pruned {} stale entries ({} remaining)",
                removed,
                baseline.findings.len()
            );
            Ok(())
        }
    }
}
