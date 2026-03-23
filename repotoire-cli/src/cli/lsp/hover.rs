use crate::models::Finding;

/// Render a rich markdown hover for a finding.
/// Returns None if the finding has no extra context beyond title/description
/// (since the diagnostic tooltip already shows the title).
pub fn render_hover(finding: &Finding) -> Option<String> {
    let mut parts: Vec<String> = Vec::new();

    if !finding.description.is_empty() {
        parts.push(finding.description.clone());
    }

    if let Some(why) = &finding.why_it_matters {
        parts.push(format!("**Why it matters:** {}", why));
    }

    if let Some(fix) = &finding.suggested_fix {
        parts.push(format!("**Suggested fix:** {}", fix));
    }

    let mut footer = Vec::new();
    if let Some(cwe) = &finding.cwe_id {
        footer.push(format!("**CWE:** {}", cwe));
    }
    if let Some(conf) = finding.confidence {
        footer.push(format!("**Confidence:** {:.2}", conf));
    }
    if let Some(effort) = &finding.estimated_effort {
        footer.push(format!("**Effort:** {}", effort));
    }
    if !footer.is_empty() {
        parts.push(footer.join(" · "));
    }

    if parts.is_empty() {
        None
    } else {
        Some(parts.join("\n\n"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Severity;
    use std::path::PathBuf;

    fn make_finding() -> Finding {
        Finding {
            id: "f1".to_string(),
            detector: "SQLi".to_string(),
            severity: Severity::Critical,
            title: "SQL Injection".to_string(),
            description: "User input flows into SQL query.".to_string(),
            affected_files: vec![PathBuf::from("/tmp/a.rs")],
            line_start: Some(10),
            why_it_matters: Some("Attacker can read/modify data.".to_string()),
            suggested_fix: Some("Use parameterized queries.".to_string()),
            cwe_id: Some("CWE-89".to_string()),
            confidence: Some(0.92),
            estimated_effort: Some("Low".to_string()),
            ..Default::default()
        }
    }

    #[test]
    fn hover_full_finding() {
        let f = make_finding();
        let md = render_hover(&f).unwrap();
        assert!(md.contains("User input flows into SQL query."));
        assert!(md.contains("**Why it matters:**"));
        assert!(md.contains("**Suggested fix:**"));
        assert!(md.contains("CWE-89"));
        assert!(md.contains("0.92"));
        assert!(md.contains("Low"));
    }

    #[test]
    fn hover_minimal_finding() {
        let f = Finding {
            title: "Something".to_string(),
            ..Default::default()
        };
        // No description, no extra fields → None
        assert!(render_hover(&f).is_none());
    }

    #[test]
    fn hover_partial_fields() {
        let f = Finding {
            description: "Some issue.".to_string(),
            suggested_fix: Some("Fix it.".to_string()),
            ..Default::default()
        };
        let md = render_hover(&f).unwrap();
        assert!(md.contains("Some issue."));
        assert!(md.contains("**Suggested fix:** Fix it."));
        assert!(!md.contains("CWE"));
        assert!(!md.contains("Confidence"));
    }
}
