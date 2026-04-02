//! Post-detection filter stage.
//!
//! Applies baseline matching, config overrides (severity remap,
//! enable/disable, confidence threshold), and delta attribution.

use crate::baseline::Baseline;
use crate::config::DetectorConfigOverride;
use crate::models::{Attribution, Confidence, Finding, FindingStatus};
#[cfg(test)]
use crate::models::Severity;
use std::collections::{HashMap, HashSet};

/// Input to the filter stage.
pub struct FilterInput<'a> {
    /// Raw findings from the detect stage.
    pub findings: Vec<Finding>,
    /// Loaded baseline (if any).
    pub baseline: Option<&'a Baseline>,
    /// Per-detector config overrides from `repotoire.toml`.
    pub detector_overrides: &'a HashMap<String, DetectorConfigOverride>,
    /// Qualified names of nodes whose source changed in the diff.
    pub changed_node_qnames: Option<&'a HashSet<String>>,
    /// Qualified names of direct callers of changed nodes.
    pub caller_of_changed_qnames: Option<&'a HashSet<String>>,
    /// Resolves a finding to its entity qualified name (if any).
    pub resolve_qualified_name: Option<&'a dyn Fn(&Finding) -> Option<String>>,
}

/// Output of the filter stage.
pub struct FilterOutput {
    /// Surviving findings (New + Baselined).
    pub findings: Vec<Finding>,
    /// How many findings were marked Baselined.
    pub baselined_count: usize,
    /// Baseline entries whose issue no longer reproduces (entity still exists).
    pub fixed_entries: Vec<crate::baseline::BaselineEntry>,
    /// Baseline entries whose entity no longer exists (renamed/deleted).
    pub stale_entries: Vec<crate::baseline::BaselineEntry>,
}

/// Run the filter stage on raw detection output.
pub fn filter_stage(input: FilterInput) -> FilterOutput {
    let mut findings = Vec::new();
    let mut baselined_count = 0;
    let mut active_fingerprints = HashSet::new();

    for mut finding in input.findings {
        // 1. Config: enabled check
        if let Some(ovr) = input.detector_overrides.get(&finding.detector) {
            if ovr.enabled == Some(false) {
                continue;
            }
        }

        // 2. Resolve qualified name
        let qname = input
            .resolve_qualified_name
            .and_then(|resolve| resolve(&finding));

        // 3. Compute fingerprint
        let fingerprint = if let Some(ref qn) = qname {
            crate::baseline::fingerprint::entity_fingerprint(&finding.detector, qn)
        } else if let Some(file) = finding.affected_files.first() {
            let first_line = finding.description.lines().next().unwrap_or("");
            crate::baseline::fingerprint::file_fingerprint(
                &finding.detector,
                &file.to_string_lossy(),
                first_line,
            )
        } else {
            String::new()
        };
        active_fingerprints.insert(fingerprint.clone());

        // 4. Baseline check
        if let Some(baseline) = input.baseline {
            if baseline.contains(&fingerprint) {
                finding.status = FindingStatus::Baselined;
                baselined_count += 1;
                findings.push(finding);
                continue;
            }
        }

        // 5. Config: severity remap + confidence threshold
        if let Some(ovr) = input.detector_overrides.get(&finding.detector) {
            if let Some(new_severity) = ovr.severity {
                finding.original_severity = Some(finding.severity);
                finding.severity = new_severity;
            }
            if let Some(threshold) = ovr.confidence_threshold {
                let finding_confidence = finding
                    .confidence
                    .map(Confidence::from_score)
                    .unwrap_or(Confidence::Medium);
                if finding_confidence < threshold {
                    continue;
                }
            }
        }

        // 6. Delta attribution
        if let Some(ref changed) = input.changed_node_qnames {
            if let Some(ref qn) = qname {
                if changed.contains(qn) {
                    finding.attribution = Attribution::InChangedNode;
                } else if input
                    .caller_of_changed_qnames
                    .map_or(false, |c| c.contains(qn))
                {
                    finding.attribution = Attribution::InCallerOfChanged;
                } else {
                    finding.attribution = Attribution::InUnrelated;
                }
            }
        }

        finding.status = FindingStatus::New;
        findings.push(finding);
    }

    // Fixed / stale from baseline
    let (fixed_entries, stale_entries) = if let Some(baseline) = input.baseline {
        let mut fixed = Vec::new();
        let mut stale = Vec::new();
        for entry in &baseline.findings {
            if !active_fingerprints.contains(&entry.fingerprint) {
                let entity_exists = entry.qualified_name.as_ref().map_or(false, |qn| {
                    if let Some(resolve) = &input.resolve_qualified_name {
                        let dummy = Finding {
                            detector: entry.detector.clone(),
                            affected_files: entry
                                .file
                                .as_ref()
                                .map(|f| vec![std::path::PathBuf::from(f)])
                                .unwrap_or_default(),
                            ..Default::default()
                        };
                        resolve(&dummy).as_deref() == Some(qn.as_str())
                    } else {
                        false
                    }
                });
                if entity_exists {
                    fixed.push(entry.clone());
                } else {
                    stale.push(entry.clone());
                }
            }
        }
        (fixed, stale)
    } else {
        (Vec::new(), Vec::new())
    };

    FilterOutput {
        findings,
        baselined_count,
        fixed_entries,
        stale_entries,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_overrides() -> HashMap<String, DetectorConfigOverride> {
        HashMap::new()
    }

    #[test]
    fn test_filter_drops_disabled_detector() {
        let mut overrides = HashMap::new();
        overrides.insert(
            "bus-factor".to_string(),
            DetectorConfigOverride {
                enabled: Some(false),
                ..Default::default()
            },
        );

        let findings = vec![Finding {
            detector: "bus-factor".to_string(),
            severity: Severity::Medium,
            description: "Only one contributor".to_string(),
            ..Default::default()
        }];

        let output = filter_stage(FilterInput {
            findings,
            baseline: None,
            detector_overrides: &overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: None,
        });

        assert!(output.findings.is_empty(), "disabled detector should be dropped");
    }

    #[test]
    fn test_filter_remaps_severity() {
        let mut overrides = HashMap::new();
        overrides.insert(
            "god-class".to_string(),
            DetectorConfigOverride {
                severity: Some(Severity::Medium),
                ..Default::default()
            },
        );

        let findings = vec![Finding {
            detector: "god-class".to_string(),
            severity: Severity::High,
            description: "Too many methods".to_string(),
            ..Default::default()
        }];

        let output = filter_stage(FilterInput {
            findings,
            baseline: None,
            detector_overrides: &overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: None,
        });

        assert_eq!(output.findings.len(), 1);
        assert_eq!(output.findings[0].severity, Severity::Medium);
        assert_eq!(output.findings[0].original_severity, Some(Severity::High));
    }

    #[test]
    fn test_filter_marks_baselined() {
        let overrides = empty_overrides();

        // Build a finding whose file-level fingerprint we can predict
        let finding = Finding {
            detector: "god-class".to_string(),
            severity: Severity::High,
            description: "Too many methods".to_string(),
            affected_files: vec![std::path::PathBuf::from("src/big.rs")],
            ..Default::default()
        };

        // Compute the fingerprint the filter stage will produce
        let fp = crate::baseline::fingerprint::file_fingerprint(
            "god-class",
            "src/big.rs",
            "Too many methods",
        );

        let baseline = Baseline {
            version: 1,
            accepted_at: "2025-01-01T00:00:00Z".to_string(),
            findings: vec![crate::baseline::BaselineEntry {
                detector: "god-class".to_string(),
                fingerprint: fp,
                qualified_name: None,
                file: Some("src/big.rs".to_string()),
                first_line_content: None,
                accepted_by: None,
                reason: None,
            }],
        };

        let output = filter_stage(FilterInput {
            findings: vec![finding],
            baseline: Some(&baseline),
            detector_overrides: &overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: None,
        });

        assert_eq!(output.findings.len(), 1);
        assert_eq!(output.findings[0].status, FindingStatus::Baselined);
        assert_eq!(output.baselined_count, 1);
    }

    #[test]
    fn test_filter_attribution_in_changed_node() {
        let overrides = empty_overrides();
        let qname = "mod::MyClass".to_string();
        let changed: HashSet<String> = [qname.clone()].into();

        let finding = Finding {
            detector: "god-class".to_string(),
            severity: Severity::High,
            description: "Too many methods".to_string(),
            ..Default::default()
        };

        let resolve_qn = qname.clone();
        let resolver = move |_f: &Finding| -> Option<String> { Some(resolve_qn.clone()) };

        let output = filter_stage(FilterInput {
            findings: vec![finding],
            baseline: None,
            detector_overrides: &overrides,
            changed_node_qnames: Some(&changed),
            caller_of_changed_qnames: None,
            resolve_qualified_name: Some(&resolver),
        });

        assert_eq!(output.findings.len(), 1);
        assert_eq!(output.findings[0].attribution, Attribution::InChangedNode);
    }

    #[test]
    fn test_filter_confidence_threshold() {
        let mut overrides = HashMap::new();
        overrides.insert(
            "god-class".to_string(),
            DetectorConfigOverride {
                confidence_threshold: Some(Confidence::High),
                ..Default::default()
            },
        );

        let findings = vec![
            Finding {
                detector: "god-class".to_string(),
                severity: Severity::High,
                description: "High confidence".to_string(),
                confidence: Some(0.90), // -> Confidence::High
                ..Default::default()
            },
            Finding {
                detector: "god-class".to_string(),
                severity: Severity::Medium,
                description: "Low confidence".to_string(),
                confidence: Some(0.40), // -> Confidence::Low
                ..Default::default()
            },
        ];

        let output = filter_stage(FilterInput {
            findings,
            baseline: None,
            detector_overrides: &overrides,
            changed_node_qnames: None,
            caller_of_changed_qnames: None,
            resolve_qualified_name: None,
        });

        assert_eq!(output.findings.len(), 1, "only high-confidence finding should pass");
        assert_eq!(output.findings[0].description, "High confidence");
    }
}
